"""S1 EvaluateFetchTransport — Playwright page.evaluate(fetch).

Mirrors the existing Compiled Growth Worker's proven approach. Flow's own
JavaScript layer attaches the Authorization: Bearer header before fetch()
leaves the browser. page.request.post() bypasses that JS layer — this
strategy avoids that pitfall by firing fetch from inside the page context.

Lifecycle:
    setup(profile_dir)           — launch own persistent context, open page, navigate to Flow.
    setup(profile_dir, page=p)   — shared-page path: reuse caller's Page, skip own launch.
    generate_images(*)           — page.evaluate("async (args) => fetch(...)").
    refresh_auth()               — re-navigate to Flow URL to refresh page-context tokens.
    teardown()                   — close page + context + stop playwright (idempotent).
                                   No-op when _owns_playwright is False (caller owns context).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page

import structlog

from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import (
    GenerateImageRequest,
    _build_batch_generate_images_body,
)
from gflow_cli.api.transports._common import (
    FLOW_URL,
    PER_CALL_TIMEOUT_S,
    interpret_response,
    mint_batch_id,
)
from gflow_cli.errors import (
    AuthExpiredError,
    TransportTimeoutError,
    WafRejectionError,
)

log = structlog.get_logger(__name__)

# JS snippet fired from inside the page context — credentials:'include' ensures
# Flow's own JS attaches the Bearer header before the request leaves the browser.
_FETCH_JS = """
async (args) => {
    const r = await fetch(args.url, {
        method: 'POST',
        headers: {'content-type': 'text/plain;charset=UTF-8'},
        body: args.body,
        credentials: 'include',
    });
    return {status: r.status, body: await r.text()};
}
""".strip()

_BATCH_GENERATE_URL_TEMPLATE = (
    "https://aisandbox-pa.googleapis.com/v1/projects/{project_id}/flowMedia:batchGenerateImages"
)


class _ResponseLike:
    """Adapts the dict returned by page.evaluate to the httpx-like interface
    expected by interpret_response (which reads .status_code and .text)."""

    __slots__ = ("status_code", "text")

    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class EvaluateFetchTransport:
    """S1 — page.evaluate fetch strategy.

    The safest known-working approach: fetch fires from inside the page's JS
    context so Flow's own scripts attach the Authorization header.

    Single-flight per ``profile_dir`` — do not share a profile directory across
    concurrent processes. Chromium holds an exclusive lockfile on the
    user-data-dir; two ``EvaluateFetchTransport.setup()`` calls against the
    same profile will conflict. See spec § 5.4.4. S2 and S3 are
    concurrent-safe after their initial setup capture.
    """

    name = "evaluate_fetch"

    def __init__(self) -> None:
        self._pw_cm: Any | None = None
        self._ctx: Any | None = None
        self._page: Any | None = None
        self._setup_done: bool = False
        # Tracks whether THIS instance opened its own Playwright context.
        # False when setup() was called with page= (shared-page path, spec § 5.4.4).
        # teardown() is a no-op for Playwright resources when False.
        self._owns_playwright: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def setup(self, profile_dir: Path, *, page: Page | None = None) -> None:
        """Launch a persistent Playwright context and navigate to Flow.

        Idempotent — a second call is a no-op.

        When ``page`` is provided (shared-page path), the strategy stores the
        caller's Page and marks ``_owns_playwright = False`` — teardown() will
        NOT close the context or stop playwright; that responsibility stays with
        the caller (FlowApiClient).  This eliminates the Chromium lockfile
        conflict described in spec § 5.4.4.

        When ``page`` is None (back-compat / standalone use), the strategy
        opens its own context as before with ``_owns_playwright = True``.
        """
        if self._setup_done:
            return

        if page is not None:
            # Shared-page path: caller owns Playwright lifecycle.
            self._page = page
            self._owns_playwright = False
            self._setup_done = True
            log.info("evaluate_fetch.setup_shared_page")
            return

        # Own-context path: strategy owns the full Playwright lifecycle.
        # Lazy import: keeps module-level import cheap when other transports
        # are selected; only imported when S1 is actually used.
        from playwright.async_api import async_playwright  # noqa: PLC0415

        pw_cm = async_playwright()
        self._pw_cm = pw_cm
        # Mark ownership BEFORE the risky operations so partial-setup teardown
        # can correctly identify "we own this pw_cm, close it on failure".
        # Without this, a failure between __aenter__ and the end of setup would
        # leak the open Playwright handles (teardown's `if _owns_playwright`
        # guard would skip the close).
        self._owns_playwright = True
        try:
            pw = await pw_cm.__aenter__()
            ctx = await pw.chromium.launch_persistent_context(
                str(profile_dir),
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 720},
                locale="en-US",
            )
            self._ctx = ctx
            new_page = await ctx.new_page()
            self._page = new_page
            await new_page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=30_000)
            self._setup_done = True
            log.info("evaluate_fetch.setup_done", profile=str(profile_dir))
        except BaseException:
            await self.teardown()
            raise

    async def refresh_auth(self) -> None:
        """Re-navigate to Flow URL to refresh page-context tokens.

        For S1 the browser still holds valid cookies — a simple re-nav is
        enough to let Flow's JS re-attach fresh Bearer tokens. If navigation
        itself fails, raise AuthExpiredError so the caller can surface a
        clear remediation hint.
        """
        if not self._setup_done or self._page is None:
            raise AuthExpiredError("evaluate_fetch: cannot refresh — setup not done")
        try:
            await self._page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=30_000)
            log.info("evaluate_fetch.refresh_auth_done")
        except Exception as exc:
            raise AuthExpiredError(f"evaluate_fetch: refresh navigation failed: {exc}") from exc

    async def generate_images(
        self,
        *,
        project_id: str,
        request: GenerateImageRequest,
    ) -> list[GeneratedImage]:
        """Generate images via page.evaluate fetch.

        Enforces a 30 s wall-clock budget. On HTTP 401, calls refresh_auth()
        and retries exactly once; a second 401 raises AuthExpiredError.
        """
        return await self._generate_images_inner(
            project_id=project_id, request=request, is_retry=False
        )

    async def _generate_images_inner(
        self,
        *,
        project_id: str,
        request: GenerateImageRequest,
        is_retry: bool,
    ) -> list[GeneratedImage]:
        """Internal implementation; ``is_retry`` tracks single-retry state."""
        if self._page is None:
            raise RuntimeError("evaluate_fetch: setup() must be called before generate_images()")

        seed = (
            int(hashlib.sha256(request.refs[0].name.encode("utf-8")).hexdigest(), 16) % 2**31
            if request.refs
            else int(time.time())
        )
        body = _build_batch_generate_images_body(
            request,
            project_id=project_id,
            batch_id=mint_batch_id(),
            seed=seed,
            session_id=f";{int(time.time() * 1000)}",
        )
        url = _BATCH_GENERATE_URL_TEMPLATE.format(project_id=project_id)

        try:
            raw: dict[str, Any] = await asyncio.wait_for(
                self._page.evaluate(
                    _FETCH_JS,
                    {"url": url, "body": json.dumps(body)},
                ),
                timeout=PER_CALL_TIMEOUT_S,
            )
        except TimeoutError as exc:
            raise TransportTimeoutError(
                f"evaluate_fetch: page.evaluate hung > {PER_CALL_TIMEOUT_S}s"
            ) from exc

        return await self._handle_response(
            raw, project_id=project_id, request=request, is_retry=is_retry
        )

    async def teardown(self) -> None:
        """Close the page, context, and playwright instance. Idempotent.

        When ``_owns_playwright`` is False (shared-page path), this method is a
        no-op for Playwright resources — the caller (FlowApiClient) owns the
        context and is responsible for closing it.  Internal state flags are
        still reset so a reused instance is clean.
        """
        if self._owns_playwright:
            if self._ctx is not None:
                try:
                    await self._ctx.close()
                except Exception:
                    log.warning("evaluate_fetch.teardown: ctx.close() failed", exc_info=True)
                finally:
                    self._ctx = None
                    self._page = None

            if self._pw_cm is not None:
                try:
                    await self._pw_cm.__aexit__(None, None, None)
                except Exception:
                    log.warning("evaluate_fetch.teardown: pw_cm.__aexit__() failed", exc_info=True)
                finally:
                    self._pw_cm = None

        self._setup_done = False
        self._owns_playwright = False
        log.info("evaluate_fetch.teardown_done")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _handle_response(
        self,
        raw: dict[str, Any],
        *,
        project_id: str,
        request: GenerateImageRequest,
        is_retry: bool,
    ) -> list[GeneratedImage]:
        status: int = int(raw.get("status", 0))
        body_text: str = raw.get("body", "")

        if status == 401:
            if is_retry:
                raise AuthExpiredError(
                    "evaluate_fetch: HTTP 401 persisted after refresh — session expired"
                )
            # First 401: refresh then retry exactly once.
            await self.refresh_auth()
            return await self._generate_images_inner(
                project_id=project_id,
                request=request,
                is_retry=True,
            )

        if status == 403:
            raise WafRejectionError(
                f"evaluate_fetch: HTTP 403 — WAF/fingerprint rejection: {body_text[:200]}"
            )

        # Delegate all other status codes (200, 429, 5xx, etc.) to the shared
        # interpreter from _common so error taxonomy stays consistent across
        # all three strategies.
        resp = _ResponseLike(status_code=status, text=body_text)
        return interpret_response("evaluate_fetch", resp)
