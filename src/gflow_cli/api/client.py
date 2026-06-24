"""FlowApiClient — typed wrapper around Flow's private REST surface.

Architecture: the client manages its own Playwright persistent-context
lifecycle (async context manager). All HTTP goes through page.request so
Google's session cookies attach automatically.

Usage:
    async with FlowApiClient(profile_dir) as client:
        project = await client.create_project()
        asset = await client.upload_image(project.project_id, Path("hero.png"))
        ...
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from dataclasses import replace as _dc_replace
from datetime import datetime
from typing import TYPE_CHECKING, Any, Self, cast
from urllib.parse import quote, urlsplit, urlunsplit

import structlog
from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from gflow_cli.api import routes
from gflow_cli.api._engine import (
    active_engine,
    log_engine_selected,
    mint_evaluate_kwargs,
    resolve_async_playwright,
)
from gflow_cli.api._retry import parse_retry_after, post_with_retry
from gflow_cli.api.character import Character, CharacterImageRequest, parse_characters
from gflow_cli.api.dto import AssetInfo, GeneratedImage, ProjectInfo
from gflow_cli.api.image_upscale import (
    TargetResolution,
    UpsampleImageRequest,
    build_upsample_image_body,
)
from gflow_cli.api.recaptcha import TokenMinter
from gflow_cli.api.scene import ConcatInput, Scene, SceneWorkflow
from gflow_cli.api.transports import make_transport
from gflow_cli.api.transports.base import FlowTransportStrategy, VideoCapableTransport
from gflow_cli.browser_manager import channel_for_profile
from gflow_cli.config import BrowserEngine, Settings
from gflow_cli.errors import (
    AisandboxAuthError,
    AuthExpiredError,
    AuthMissingError,
    BrowserSessionClosedError,
    ConfigurationError,
    ContentPolicyError,
    FlowApiError,  # re-exported via gflow_cli.api.__init__
    NetworkError,
    RateLimitError,
    SceneConcatError,
    TransportTimeoutError,
    UpscaleUnavailableError,
    WafRejectionError,
    WireFormatError,
)
from gflow_cli.paths import adjust_key_extension, character_output_path
from gflow_cli.storage import AnyPath, storage_path, write_asset_async

if TYPE_CHECKING:
    from pathlib import Path

    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.video import GenerateVideoRequest, VideoResult, VideoStartedCallback

# Shorthand for an untyped JSON-ish string-keyed mapping (request/response
# bodies, launch kwargs, etc.). A single definition avoids the duplicated-literal
# smell (SonarCloud S1192) from repeating the bare mapping type across the module.
JsonObject = dict[str, Any]

# Marker substring used by Playwright when a Page/Context/Browser is closed.
# Stable across recent Playwright versions; we match on message text to avoid
# importing from ``playwright._impl._errors`` (private API).
_TARGET_CLOSED_MARKERS = (
    "Target page, context or browser has been closed",
    "Target closed",
)


def _is_target_closed(exc: BaseException) -> bool:
    """Heuristic — True if ``exc`` is Playwright's TargetClosedError or wraps it."""
    name = type(exc).__name__
    if name == "TargetClosedError":
        return True
    msg = str(exc)
    return any(marker in msg for marker in _TARGET_CLOSED_MARKERS)


# Silence "imported but unused" — FlowApiError is re-exported from this module
# via ``gflow_cli.api.__init__`` for back-compat with Phase 3 call sites.
__all__ = ["FlowApiClient", "FlowApiError"]

logger = structlog.get_logger(__name__)

# Cap matches Flow's UI upload limit (~20 MB observed in captured traffic). Used
# by `upload_image` to reject oversize files BEFORE reading them into memory —
# protects this process from OOM and the remote endpoint from DoS-shaped traffic.
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB

# Server-side concat returns the combined MP4 inline as base64 in `encodedVideo`
# (~1.27 MB base64 per second of video). Cap before b64decode to avoid OOM on a
# pathologically long scene; ~350 MB base64 ≈ 260 MB MP4 ≈ a ~4.5-min scene.
MAX_CONCAT_B64_LEN = 350 * 1024 * 1024

# upsampleImage returns the upscaled image inline as base64 in `encodedImage`
# (~3.8–5.1 MB observed for 2K). Cap before b64decode to avoid OOM on a
# pathological 4K PNG; 50 MB base64 ≈ 37 MB decoded gives generous headroom.
MAX_UPSAMPLE_B64_LEN = 50 * 1024 * 1024

# aisandbox-pa rejects application/json — see samples/captured/*.json.
_AISANDBOX_CONTENT_TYPE = "text/plain;charset=UTF-8"
# Content type for the labs.google BFF (tRPC) endpoints, which DO accept JSON.
_APPLICATION_JSON = "application/json"

# aisandbox-pa POSTs return 401 to page.request unless an Authorization: Bearer
# <access_token> is attached — the SPA's OAuth2 token, fetched from the BFF
# session endpoint (cookie-auth). The labs.google BFF itself authenticates on
# cookies alone, so the Bearer header is scoped to the aisandbox host only.
_AISANDBOX_HOST = "aisandbox-pa.googleapis.com"
_LABS_ORIGIN = "https://labs.google"
_SESSION_API_URL = "https://labs.google/fx/api/auth/session"


def _parse_iso_to_epoch(value: object) -> float:
    """Parse an ISO-8601 timestamp (e.g. ``/auth/session``'s ``expires``) to
    epoch seconds. Falls back to ``now + 55min`` when absent/unparseable so the
    token cache keeps a horizon (the 401 refresh-retry is the safety net).
    """
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return time.time() + 3300.0


def _is_supported_image_header(header: bytes) -> bool:
    """Return True if ``header`` (first 12 bytes of a file) matches a known image
    container's magic bytes.

    Allowed formats — every one is accepted by Flow's web UI:

    * **PNG** — ``\\x89PNG\\r\\n\\x1a\\n``
    * **JPEG** — bytes 0..2 are ``\\xff\\xd8\\xff``
    * **WebP** — bytes 0..3 ``RIFF`` + bytes 8..11 ``WEBP``
    * **GIF** — ``GIF87a`` or ``GIF89a``

    Rejecting anything else is a defense-in-depth measure: combined with
    ``resolve_path=True`` on the CLI argument it stops a symlink-laundering
    attack (``./photo.png -> ~/.ssh/id_rsa``) at the bytes layer.
    """
    if len(header) < 12:
        return False
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if header[:3] == b"\xff\xd8\xff":
        return True
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return True
    return header[:6] in (b"GIF87a", b"GIF89a")


def _unwrap_trpc(data: Any) -> JsonObject:
    """Unwrap the tRPC envelope ``result.data.json`` and return the inner dict.

    Only the standard tRPC v10 shape is accepted:
    ``{"result": {"data": {"json": {...}}}}``.

    A no-``"json"``-key shape was considered but has no observed evidence in
    any captured HAR for createEntity or projectInitialData (see
    ``docs/CHARACTER_RECON.md`` and ``scripts/dev/character_create_spike.py``).
    Responses without ``"json"`` therefore surface as
    :class:`~gflow_cli.errors.WireFormatError` rather than being silently
    accepted.

    Raises :class:`~gflow_cli.errors.WireFormatError` when the envelope is
    malformed or when the extracted payload is not a dict.
    """
    if not isinstance(data, dict):
        raise WireFormatError(
            detail=f"tRPC response is not a dict; got {type(data).__name__}",
            route="tRPC",
        )
    data_dict = cast("JsonObject", data)
    result = data_dict.get("result")
    if not isinstance(result, dict):
        raise WireFormatError(
            detail="tRPC reply missing 'result' dict",
            route="tRPC",
        )
    result_dict = cast("JsonObject", result)
    data_obj = result_dict.get("data")
    if not isinstance(data_obj, dict):
        raise WireFormatError(
            detail="tRPC reply missing result.data dict",
            route="tRPC",
        )
    data_obj_dict = cast("JsonObject", data_obj)
    inner: Any = data_obj_dict.get("json")
    if not isinstance(inner, dict):
        raise WireFormatError(
            detail=f"tRPC reply missing result.data.json dict; got {type(inner).__name__}",
            route="tRPC",
        )
    return cast("JsonObject", inner)


class FlowApiClient:
    """Async context-managed client for Flow's REST surface.

    Holds a Playwright persistent context and a single page (used as the HTTP
    transport via `page.request`). Auth = whatever cookies the profile dir
    has from a prior `gflow auth login`.
    """

    def __init__(
        self,
        profile_dir: Path,
        *,
        headless: bool = False,
        settings: Settings | None = None,
        transport: FlowTransportStrategy | str | None = None,
        out_dir: Path | None = None,
    ) -> None:
        self.profile_dir = profile_dir
        self.headless = headless
        # Optional directory for debug screenshots when a generation step
        # fails on a selector. Propagated to the transport (see __aenter__)
        # so long-lived workers can diagnose a "Could not find ... CTA"
        # error without restructuring their call sites (#18).
        self._out_dir = out_dir
        # NOTE: A bare ``Settings()`` here would resolve env vars / .env at
        # construction time, which is fine for production but lets tests
        # opt out by supplying a fully built settings object.
        self.settings = settings if settings is not None else Settings()
        # Transport lifecycle ownership (spec § 4.3):
        # - pre-initialized instance → caller owns (no setup/teardown invoked)
        # - str/None → client owns (resolves via make_transport, calls setup/teardown)
        # Duck-check instead of isinstance(x, FlowTransportStrategy) because the
        # Protocol is not @runtime_checkable — adding that decorator would widen
        # its API surface and constrain future Protocol evolution.
        self._transport_input: FlowTransportStrategy | str | None = transport
        self.transport: FlowTransportStrategy | None = None
        self._owns_transport: bool = False
        # OAuth2 access token for aisandbox-pa REST calls, fetched lazily from
        # the BFF session endpoint and cached against its expiry. Re-fetched on
        # a 401. (page.request sends cookies but not the SPA's Bearer token.)
        self._access_token: str | None = None
        self._access_token_exp: float = 0.0  # epoch seconds; 0 = unknown/expired
        self._pw: Playwright | None = None
        self._context: BrowserContext | None = None
        # Per-worker Page pool (Phase 4 T2). All Pages live inside ONE
        # persistent BrowserContext and therefore SHARE cookies + auth state
        # at the Context level — this is intentional and matches Playwright's
        # per-worker-Page recommendation. If per-user isolation is ever
        # needed, separate Contexts (one per user) would be required.
        self._pages: list[Page] = []
        self._page_queue: asyncio.Queue[Page] | None = None
        # Back-compat: existing callers in this module still reach for
        # ``self._page``. T3 rewires them to ``_checkout_page()`` /
        # ``_checkin_page()`` and this alias goes away.
        self._page: Page | None = None

    # --- lifecycle --------------------------------------------------------

    async def __aenter__(self) -> Self:
        # --- Step 1: Launch Playwright FIRST so self._page is ready before
        # transport.setup() is called.  This order is load-bearing for S1
        # (EvaluateFetchTransport): it needs a live Page passed via the
        # ``page=`` kwarg so it can reuse the client's context instead of
        # opening a second Playwright process against the same profile dir
        # (which would conflict on the Chromium lockfile — spec § 5.4.4).
        # Engine selection: the default (playwright) path uses this module's
        # ``async_playwright`` symbol unchanged — byte-identical behaviour and the
        # existing test monkeypatches still apply. Only the opt-in patchright
        # engine routes through the resolver (which raises a typed exit-24 error
        # if the optional package is missing).
        engine = active_engine()
        log_engine_selected(engine)
        if engine == BrowserEngine.PATCHRIGHT:
            self._pw = await resolve_async_playwright(engine)().start()
        else:
            self._pw = await async_playwright().start()
        # Partial-setup leak guard: __aexit__ is NOT invoked when __aenter__
        # raises, so a launched persistent context (and its chrome process)
        # would leak and lock the profile dir — the next run then fails to
        # acquire it and spirals into about:blank / TargetClosedError. Tear
        # down everything opened after the driver starts on any failure.
        try:
            await self._enter_setup()
        except BaseException:
            await self._close_browser_resources()
            raise
        return self

    def _persistent_context_kwargs(self) -> JsonObject:
        """Keyword arguments for the persistent browser-context launch.

        Extracted as an overridable seam so out-of-core tooling (e.g. a
        dev-scoped recording subclass that adds ``record_video_dir``) can
        augment the launch without any recording/test concern living in this
        core path. The returned dict is identical to the previous inline call.
        """
        return {
            "user_data_dir": str(self.profile_dir),
            "headless": self.headless,
            "viewport": {"width": 1280, "height": 720},
            "locale": "en-US",
            "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9"},
            "channel": channel_for_profile(self.profile_dir),
            "ignore_default_args": [
                "--enable-automation",
                "--no-sandbox",
                "--password-store=basic",
            ],
            "args": ["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
        }

    async def _enter_setup(self) -> None:
        """Body of __aenter__ after the Playwright driver starts.

        Extracted so __aenter__ can wrap it in a partial-setup leak guard
        (a failed launch must not orphan a chrome process).
        """
        # Invariant: __aenter__ sets self._pw immediately before calling us.
        assert self._pw is not None
        self._context = await self._pw.chromium.launch_persistent_context(
            **self._persistent_context_kwargs()
        )
        # Hide the automation flag so reCAPTCHA Enterprise doesn't score
        # the session as a bot — navigator.webdriver=true causes low-score
        # tokens and HTTP 403 on batchGenerateImages.
        await self._context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})",
        )
        # Open ``Settings.concurrency`` Pages inside the one persistent
        # BrowserContext. ``launch_persistent_context`` opens one Page by
        # default; reuse it as slot 0 to avoid an unused N+1 Page.
        n = max(1, self.settings.concurrency)
        self._pages = []
        if self._context.pages:
            self._pages.append(self._context.pages[0])
            for _ in range(n - 1):
                self._pages.append(await self._context.new_page())
        else:
            for _ in range(n):
                self._pages.append(await self._context.new_page())
        # asyncio.Queue gives FIFO checkout/checkin with no manual locking.
        # ``maxsize=n`` makes the upper bound STRUCTURAL — a double-checkin
        # (bug in a future caller) raises QueueFull rather than silently
        # corrupting the pool. The generic parameter satisfies pyright strict.
        self._page_queue = asyncio.Queue[Page](maxsize=n)
        for p in self._pages:
            self._page_queue.put_nowait(p)
        # Back-compat alias for callers that still touch ``self._page``
        # directly. T3 removes the field entirely.
        self._page = self._pages[0]
        # Bootstrap navigation so cookies + JS context are loaded before any
        # API call. Many endpoints 401 if you POST cold without an active page.
        # (Phase 3 deferred ``_new_session_id`` flake is addressed in T3 by
        # re-minting reCAPTCHA inside each retry loop on the worker's own
        # Page; no session-id work happens in T2.)
        await self._page.goto(
            routes.EDITOR_BOOTSTRAP_URL,
            wait_until="domcontentloaded",
            timeout=60_000,
        )

        # --- Step 2: Resolve and set up transport, passing the live Page so
        # S1 can share this context rather than opening its own.
        # Branch on the discriminating types (str, None) so pyright narrows
        # the else-branch to FlowTransportStrategy. We deliberately avoid
        # `@runtime_checkable` on the Protocol — that would freeze its
        # public surface and constrain future evolution.
        inp = self._transport_input
        if inp is None or isinstance(inp, str):
            # Client-owned: resolve from factory, run full lifecycle.
            # Pass self._page so S1 can reuse the already-open context.
            # S2 and S3 accept and ignore the page= kwarg.
            self.transport = make_transport(inp)
            # Plumb the client's out_dir to the transport so debug screenshots
            # taken inside `_generate_images_locked` land somewhere the caller
            # can inspect (#18). Guarded by hasattr so transports without an
            # `_out_dir` slot are unaffected.
            self._plumb_out_dir(self.transport)
            self._plumb_storage_uri(self.transport)
            await self.transport.setup(self.profile_dir, page=self._page)
            self._owns_transport = True
        else:
            # Caller-owned: pre-initialized FlowTransportStrategy instance.
            # Do NOT call setup() — the caller already did that.
            self.transport = inp
            self._owns_transport = False
            self._plumb_out_dir(self.transport)
            self._plumb_storage_uri(self.transport)

    def _plumb_out_dir(self, transport: FlowTransportStrategy) -> None:
        """Forward ``self._out_dir`` onto a transport that exposes the slot."""
        if self._out_dir is None:
            return
        if not hasattr(transport, "_out_dir"):
            return
        transport._out_dir = self._out_dir  # type: ignore[attr-defined]

    def _plumb_storage_uri(self, transport: FlowTransportStrategy) -> None:
        """Forward ``self.settings.storage_uri`` and ``output_dir`` onto the transport."""
        if not hasattr(transport, "_storage_uri"):
            return
        transport._storage_uri = self.settings.storage_uri  # type: ignore[attr-defined]
        transport._output_dir = self.settings.output_dir  # type: ignore[attr-defined]

    async def __aexit__(self, *exc: object) -> None:
        # _close_browser_resources is fully guarded internally and always resets
        # the pool fields (even if context.close raises), so the client is left
        # in a clean state without a separate finally block here.
        await self._close_browser_resources()
        if self._owns_transport and self.transport is not None:
            try:
                await self.transport.teardown()
            except Exception:
                logger.warning("transport_teardown_error", exc_info=True)

    async def _close_browser_resources(self) -> None:
        """Close the BrowserContext and stop the Playwright driver, best-effort.

        Shared by :meth:`__aexit__` and :meth:`__aenter__`'s partial-setup
        guard so a failed launch can't orphan a chrome process that then locks
        the profile dir. Each step is independently guarded so one failure
        still runs the next; errors surface as warnings (CLAUDE.md: never
        silently swallow). Resets the browser fields so a reused client never
        holds references to a dead BrowserContext.
        """
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                # Browser cleanup is best-effort but MUST be surfaced for
                # diagnosis (CLAUDE.md: never silently swallow errors).
                logger.warning("browser_context_close_error", exc_info=True)
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                logger.warning("playwright_stop_error", exc_info=True)
        self._pages = []
        self._page_queue = None
        self._page = None
        self._context = None
        self._pw = None

    async def _checkout_page(self) -> Page:
        """Block until a Page is available from the pool; FIFO.

        Waits indefinitely if the pool is exhausted (no Pages available).
        Callers that need a deadline must wrap the call themselves (T3's
        retry layer applies the per-attempt timeout).

        Test affordance: when ``_page_queue`` is None but ``_page`` was
        injected directly (existing test pattern: ``c._page = MagicMock()``),
        return ``_page`` so the mock-based test surface keeps working without
        having to populate the queue. Production code always enters via
        ``async with`` which initializes the queue.
        """
        if self._page_queue is None:
            if self._page is not None:
                return self._page
            msg = "FlowApiClient not entered — use `async with`"
            raise RuntimeError(msg)
        return await self._page_queue.get()

    def _checkin_page(self, page: Page) -> None:
        """Return a Page to the pool. Non-blocking; pool size is bounded
        by ``maxsize=n`` so a double-checkin raises ``QueueFull`` loudly
        rather than corrupting the pool silently.

        Test affordance mirrors :meth:`_checkout_page`: when the queue is
        absent (mock-injected ``_page``), checkin is a no-op.
        """
        if self._page_queue is None:
            return
        self._page_queue.put_nowait(page)

    @property
    def page(self) -> Page:
        if self._page is None:
            msg = "FlowApiClient not entered — use `async with`"
            raise RuntimeError(msg)
        return self._page

    # --- private HTTP helpers --------------------------------------------

    @staticmethod
    def _is_aisandbox_url(url: str) -> bool:
        """True for aisandbox-pa REST URLs, which require Bearer-token auth.

        BFF (labs.google) URLs authenticate on cookies alone — never matched.
        """
        return _AISANDBOX_HOST in url

    async def _fetch_access_token(self) -> tuple[str, float]:
        """Fetch the OAuth2 access token from the BFF session endpoint.

        Uses ``self._context.request`` (the BrowserContext APIRequestContext) —
        NOT a checked-out Page — because this runs from inside a ``_post_json``
        ``attempt()`` that already holds a Page; a nested checkout deadlocks a
        size-1 pool. The request carries the session cookies, so the BFF returns
        the SPA's current ``access_token`` (a ``ya29.`` Bearer).

        Returns ``(token, expiry_epoch_seconds)``.
        """
        ctx = self._context
        if ctx is None:
            msg = "access-token fetch needs an active browser context."
            raise AuthMissingError(msg)
        resp = await ctx.request.get(_SESSION_API_URL)
        try:
            parsed = json.loads(await resp.text())
        except json.JSONDecodeError as exc:
            raise AisandboxAuthError(
                detail="non-JSON /auth/session response",
                status=resp.status,
                instance=_make_instance(),
                route="auth/session",
            ) from exc
        data = cast("JsonObject", parsed) if isinstance(parsed, dict) else {}
        token = data.get("access_token")
        if not token:
            raise AisandboxAuthError(
                detail="no access_token in /fx/api/auth/session (session expired?)",
                status=resp.status,
                instance=_make_instance(),
                route="auth/session",
            )
        return str(token), _parse_iso_to_epoch(data.get("expires"))

    async def _ensure_access_token(self) -> str:
        """Return a cached access token, (re)fetching when missing or near expiry."""
        if self._access_token is None or time.time() >= self._access_token_exp - 60:
            self._access_token, self._access_token_exp = await self._fetch_access_token()
        return self._access_token

    async def _aisandbox_auth_headers(self) -> dict[str, str]:
        """Build the Bearer Authorization header for an aisandbox call.

        aisandbox-pa authenticates with the SPA's OAuth2 access token, not
        cookies. NEVER log the returned values.
        """
        token = await self._ensure_access_token()
        return {
            "authorization": f"Bearer {token}",
            "origin": _LABS_ORIGIN,
        }

    async def _run_with_aisandbox_retry(
        self,
        attempt: Any,
        *,
        route: str,
        is_aisandbox: bool,
    ) -> Any:
        """Run ``attempt`` under the retry policy; on an aisandbox 401, re-fetch
        the access token once and retry, then raise ``AisandboxAuthError``.

        Shared by ``_post_json`` and ``_patch_json`` so the auth-refresh policy
        lives in one place.
        """
        resp = await self._run_with_retry(attempt, route=route)
        if is_aisandbox and resp.status == 401:
            # Token may have expired mid-session — re-fetch once and retry.
            self._access_token = None
            await self._ensure_access_token()
            resp = await self._run_with_retry(attempt, route=route)
            if resp.status == 401:
                raise AisandboxAuthError(
                    detail="aisandbox-pa returned 401 after token refresh",
                    status=401,
                    instance=_make_instance(),
                    route=route,
                )
        return resp

    async def _post_json(
        self,
        url: str,
        body: JsonObject,
        *,
        content_type: str = _AISANDBOX_CONTENT_TYPE,
        route_name: str | None = None,
    ) -> Any:
        """POST a JSON body with retry + typed-error classification.

        aisandbox-pa requires text/plain content-type (not application/json)
        — see samples/captured/*.json. The tRPC host on labs.google accepts
        standard application/json.

        ``route_name`` is the sanitized route identifier used in raised errors
        (RFC 9457 ``route`` extension). Defaults to the URL when omitted; pass
        an explicit short name (e.g. ``"createProject"``) so logs are stable
        across query-string churn.
        """
        body_str = json.dumps(body)
        # docs/SECURITY.md: "No cookies, no tokens, no API keys" in logs.
        # The reCAPTCHA token is single-use with ~2min TTL, but the policy
        # holds regardless. Redact before logging.
        logger.debug("post_json", url=url, body=_redact_for_log(body_str)[:300])
        route = route_name or url
        is_aisandbox = self._is_aisandbox_url(url)

        async def attempt() -> Any:
            page = await self._checkout_page()
            try:
                headers = {"content-type": content_type}
                if is_aisandbox:
                    headers.update(await self._aisandbox_auth_headers())
                if os.environ.get("GFLOW_CLI_LOG_REQUEST_HEADERS") == "1":
                    logger.info(
                        "request_headers",
                        url=url,
                        headers=_redact_headers_for_log(headers),
                    )
                return await page.request.post(url, data=body_str, headers=headers)
            finally:
                self._checkin_page(page)

        resp = await self._run_with_aisandbox_retry(attempt, route=route, is_aisandbox=is_aisandbox)
        text = await resp.text()
        _raise_for_non_retryable(resp, text, route=route)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise WireFormatError(
                detail=f"non-JSON response: {text[:200]}",
                status=resp.status,
                instance=_make_instance(),
                route=route,
                discovery=_build_wire_format_discovery(resp, text, route),
            ) from e

    async def _patch_json(
        self,
        url: str,
        body: JsonObject,
        *,
        route_name: str | None = None,
    ) -> Any:
        body_str = json.dumps(body)
        logger.debug("patch_json", url=url, body=body_str[:300])
        route = route_name or url
        is_aisandbox = self._is_aisandbox_url(url)

        async def attempt() -> Any:
            page = await self._checkout_page()
            try:
                headers = {"content-type": _AISANDBOX_CONTENT_TYPE}
                if is_aisandbox:
                    headers.update(await self._aisandbox_auth_headers())
                if os.environ.get("GFLOW_CLI_LOG_REQUEST_HEADERS") == "1":
                    logger.info(
                        "request_headers",
                        url=url,
                        headers=_redact_headers_for_log(headers),
                    )
                return await page.request.patch(url, data=body_str, headers=headers)
            finally:
                self._checkin_page(page)

        resp = await self._run_with_aisandbox_retry(attempt, route=route, is_aisandbox=is_aisandbox)
        text = await resp.text()
        _raise_for_non_retryable(resp, text, route=route)
        try:
            return json.loads(text) if text else {}
        except json.JSONDecodeError:
            return {}

    async def _get_json(
        self,
        url: str,
        *,
        route_name: str | None = None,
    ) -> Any:
        """GET a JSON body with retry + aisandbox Bearer auth + typed errors.

        Mirrors _post_json for the read side: aisandbox-pa GETs require the
        Bearer token. 401 -> single token-refresh-retry via the shared helper.
        """
        logger.debug("get_json", url=url)
        route = route_name or url
        is_aisandbox = self._is_aisandbox_url(url)

        async def attempt() -> Any:
            page = await self._checkout_page()
            try:
                headers: dict[str, str] = {}
                if is_aisandbox:
                    headers.update(await self._aisandbox_auth_headers())
                return await page.request.get(url, headers=headers, timeout=30_000)
            finally:
                self._checkin_page(page)

        resp = await self._run_with_aisandbox_retry(attempt, route=route, is_aisandbox=is_aisandbox)
        text = await resp.text()
        _raise_for_non_retryable(resp, text, route=route)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise WireFormatError(
                detail=f"non-JSON response: {text[:200]}",
                status=resp.status,
                instance=_make_instance(),
                route=route,
                discovery=_build_wire_format_discovery(resp, text, route),
            ) from e

    async def _run_with_retry(self, attempt: Any, *, route: str) -> Any:
        """Execute ``attempt()`` under the tenacity retry policy.

        Inside the retry block we ONLY classify retryable failures
        (429 → RateLimitError, 5xx → NetworkError) so tenacity can act.
        Non-retryable 4xx fallthrough is classified outside this helper via
        ``_raise_for_non_retryable`` to keep retry-vs-classify concerns
        separate.
        """
        response: Any = None
        async for retrying in post_with_retry():
            with retrying:
                response = await attempt()
                if response.status == 429:
                    raise RateLimitError(
                        detail=f"HTTP {response.status}",
                        status=response.status,
                        retry_after=parse_retry_after(response),
                        route=route,
                    )
                if response.status >= 500:
                    raise NetworkError(
                        detail=f"HTTP {response.status}",
                        status=response.status,
                        route=route,
                    )
        assert response is not None  # tenacity reraise=True guarantees this
        return response

    # --- public API -------------------------------------------------------

    async def create_project(self, title: str | None = None) -> ProjectInfo:
        """Bootstrap a fresh Flow project. Title defaults to a timestamp.

        Maps to `POST .../trpc/project.createProject`.
        """
        title = title or _default_project_title()
        body = {"json": {"projectTitle": title, "toolName": "PINHOLE"}}
        data = await self._post_json(routes.CREATE_PROJECT, body, content_type=_APPLICATION_JSON)
        return ProjectInfo.from_create_response(data)

    async def upload_image(self, project_id: str, image_path: Path) -> AssetInfo:
        """Upload an image into a Flow project's library.

        Maps to `POST /v1/flow/uploadImage`. Image bytes go in base64.
        Returns the asset UUID + dimensions Flow inferred.

        Validates BEFORE reading the full file:

        * **Size cap** — files larger than ``MAX_IMAGE_BYTES`` (20 MB, matching
          Flow's UI limit) are rejected. Prevents OOM on accidental uploads of
          huge files and protects the remote endpoint from DoS-shaped traffic.
        * **Magic-byte check** — the first 12 bytes must match a PNG / JPEG /
          WebP / GIF signature. Stops users from silently exfiltrating
          arbitrary local files (e.g. ``~/.bashrc``, ``~/.ssh/id_rsa``) just
          because the path resolved cleanly.

        Both validations run before ``image_path.read_bytes()`` so a hostile
        path is never fully loaded into memory.
        """
        if not image_path.exists():
            raise FileNotFoundError(image_path)
        size = image_path.stat().st_size
        if size > MAX_IMAGE_BYTES:
            msg = (
                f"Image too large: {size / 1_048_576:.1f} MB exceeds "
                f"{MAX_IMAGE_BYTES // 1_048_576} MB limit"
            )
            raise ValueError(
                msg,
            )

        # Staged read: validate magic bytes first (12 B) before loading the full
        # file. Run both reads in a worker thread to keep the event loop free.
        def _read_header(p: Path) -> bytes:
            with p.open("rb") as fh:
                return fh.read(12)

        header = await asyncio.to_thread(_read_header, image_path)
        if not _is_supported_image_header(header):
            msg = f"Not a supported image format: {image_path.name}"
            raise ValueError(msg)
        full_bytes = await asyncio.to_thread(image_path.read_bytes)
        b64 = base64.b64encode(full_bytes).decode()
        body = {
            "clientContext": {"projectId": project_id, "tool": "PINHOLE"},
            "imageBytes": b64,
        }
        data = await self._post_json(routes.UPLOAD_IMAGE, body)
        return AssetInfo.from_upload_response(data)

    async def download(self, name_or_url: str, out_path: Path) -> Path:
        """Download an asset (image or video) to `out_path`. Returns out_path.

        Retries 5xx (transient CDN hiccups) via the tenacity layer. 429 from
        Google's CDN on signed URLs is rare-to-impossible in practice but the
        retry predicate handles it uniformly if it ever happens.
        """
        url = (
            name_or_url
            if name_or_url.startswith("http")
            else routes.media_download_url(name_or_url)
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        route = "mediaDownload"

        async def attempt() -> Any:
            page = await self._checkout_page()
            try:
                return await page.request.get(url, max_redirects=5, timeout=120_000)
            finally:
                self._checkin_page(page)

        resp = await self._run_with_retry(attempt, route=route)
        # Strip query string before logging — signed CDN URLs carry
        # bearer-style tokens (Signature=, Expires=) that must not
        # leak via str(exc) or log lines. See docs/SECURITY.md.
        if resp.status >= 400:
            _raise_for_non_retryable(resp, await resp.text(), route=_strip_query(url))
        out_path.write_bytes(await resp.body())
        return out_path

    async def download_image(self, image: GeneratedImage, out_path: Path) -> AnyPath:
        """Download a generated image's signed ``fifeUrl`` to local disk or cloud storage.

        Distinct from :meth:`download`: ``fifeUrl`` is already a fully
        qualified signed CDN URL on ``flow-content.google`` (carrying
        ``Expires=...&Signature=...``), so we MUST NOT route it through
        ``routes.media_download_url`` — that helper builds the
        labs.google redirect path which doesn't apply here.

        When ``GFLOW_CLI_STORAGE_URI`` is set the file is uploaded to the
        configured cloud backend instead of local disk.
        The actual write target is derived from ``out_path`` relative to the
        configured output directory.

        Returns the final write location (local ``Path`` or cloud ``UPath``).

        Raises:
            FlowApiError: when the CDN responds with a 4xx/5xx status.
            ValueError: when ``image.fife_url`` is not an HTTPS URL on a
                trusted Google host (SSRF guard).
        """
        _validate_fife_url(image.fife_url)
        # Strip query string before logging — signed CDN URLs carry
        # bearer-style tokens (Signature=, Expires=) that must not
        # leak via str(exc) or log lines. See docs/SECURITY.md.
        route = _strip_query(image.fife_url)

        async def attempt() -> Any:
            page = await self._checkout_page()
            try:
                return await page.request.get(image.fife_url, max_redirects=2, timeout=120_000)
            finally:
                self._checkin_page(page)

        resp = await self._run_with_retry(attempt, route=route)
        if resp.status >= 400:
            _raise_for_non_retryable(resp, await resp.text(), route=route)
        body = await resp.body()

        # Resolve the write target: local Path or cloud UPath.
        # Compute a relative key from out_path so cloud keys mirror the local
        # directory structure (images/YYYY-MM-DD/media_id_N.ext).
        storage_uri = self.settings.storage_uri
        if storage_uri:
            try:
                key = out_path.relative_to(self.settings.output_dir).as_posix()
            except ValueError:
                key = out_path.name
            target: AnyPath = storage_path(storage_uri, self.settings.output_dir, key)
        else:
            target = out_path

        # Issue #96: detect actual format from in-memory bytes and correct the
        # suffix before writing — avoids post-write rename (unsupported on cloud).
        target = adjust_key_extension(target, body)
        await write_asset_async(target, body)
        return target

    async def download_video(self, media_id: str, out_path: Path) -> Path:
        """Download a generated video by media ID to disk.

        Wraps :meth:`download` — ``media.getMediaUrlRedirect`` is followed
        transparently; the response body (mp4) is written to ``out_path``.

        Args:
            media_id: The UUID returned in :attr:`VideoStatus.media_id`.
            out_path: Destination file path. Parent directories are created
                if missing.

        Returns:
            ``out_path`` for ergonomic chaining.
        """
        return await self.download(media_id, out_path)

    async def archive_workflow(self, workflow_id: str, project_id: str) -> None:
        """Soft-delete (archive) a workflow — used by clear-library tooling.

        Maps to `PATCH /v1/flowWorkflows/{id}` with `metadata.archived=true`.
        """
        url = f"{routes.ARCHIVE_WORKFLOW_BASE}/{workflow_id}"
        body = {
            "workflow": {
                "name": workflow_id,
                "projectId": project_id,
                "metadata": {"archived": True},
            },
            "updateMask": "metadata.archived",
        }
        await self._patch_json(url, body)

    async def commit_workflow(
        self, workflow_id: str, *, project_id: str, primary_media_id: str
    ) -> None:
        """Commit a workflow's primaryMediaId so it can be placed in a scene.

        PATCH /v1/flowWorkflows/{id}, updateMask metadata.primaryMediaId.
        Auth handled by the _patch_json Bearer path.
        """
        body = {
            "workflow": {
                "name": workflow_id,
                "projectId": project_id,
                "metadata": {"primaryMediaId": primary_media_id},
            },
            "updateMask": "metadata.primaryMediaId",
        }
        await self._patch_json(
            routes.flow_workflow_url(workflow_id), body, route_name="commitWorkflow"
        )

    async def create_scene(self, *, project_id: str, workflow_ids: list[str]) -> Scene:
        """Compose a scene from an ordered list of source workflowIds.

        POST /v1/flow/projects/{pid}/scenes. Repeat an id to clone a clip.
        """
        data = await self._post_json(
            routes.scenes_url(project_id),
            {"workflowIds": list(workflow_ids)},
            route_name="createScene",
        )
        return Scene.from_create_response(data, project_id=project_id)

    async def update_scene_workflows(
        self, *, scene_id: str, project_id: str, workflows: list[SceneWorkflow]
    ) -> None:
        """Set per-clip order + trim. POST /v1/flow/scene/sceneWorkflows:update."""
        body = {
            "sceneId": scene_id,
            "projectId": project_id,
            "sceneWorkflows": [w.to_wire(scene_id=scene_id) for w in workflows],
        }
        await self._post_json(
            routes.SCENE_WORKFLOWS_UPDATE, body, route_name="updateSceneWorkflows"
        )

    async def get_scene_workflows(self, scene_id: str, *, project_id: str) -> Scene:
        """Read back a scene's clips (order + trims). GET via _get_json."""
        data = await self._get_json(
            routes.scene_workflows_url(scene_id, project_id), route_name="getSceneWorkflows"
        )
        return Scene.from_get_response(data, scene_id=scene_id, project_id=project_id)

    async def _poll_concat_until_done(
        self,
        operation: Any,
        *,
        poll_interval: float,
        deadline: float,
        timeout_s: float,
    ) -> str:
        """Poll ``runVideoFxCheckConcatenationStatus`` until successful.

        Returns the raw base64 ``encodedVideo`` string on success.
        Raises :class:`SceneConcatError` on job failure and
        :class:`TransportTimeoutError` on deadline breach.
        """
        while True:
            status_resp = await self._post_json(
                routes.RUN_VIDEO_FX_CHECK_CONCATENATION_STATUS,
                {"operation": operation},
                route_name="runVideoFxCheckConcatenationStatus",
            )
            status = str(status_resp.get("status", ""))
            if status == "MEDIA_GENERATION_STATUS_SUCCESSFUL":
                return str(status_resp.get("encodedVideo") or "")
            if status and status != "MEDIA_GENERATION_STATUS_ACTIVE":
                # FAILED / unspecified — detail from status ONLY, never encodedVideo.
                logger.warning("scene.concat_failed", status=status)
                raise SceneConcatError(
                    detail=f"concatenation job status: {status}",
                    route="runVideoFxCheckConcatenationStatus",
                )
            if time.monotonic() >= deadline:
                raise TransportTimeoutError(
                    f"scene concatenation did not finish within {timeout_s:.0f}s"
                )
            await asyncio.sleep(poll_interval)

    @staticmethod
    def _decode_concat_video(encoded: str) -> bytes:
        """Validate size, base64-decode, and magic-byte-check a concat payload.

        Raises :class:`SceneConcatError` when the payload is absent, oversized,
        undecodable, or not a valid MP4.
        """
        if not encoded:
            raise SceneConcatError(
                detail="concatenation succeeded but returned no encodedVideo",
                route="runVideoFxCheckConcatenationStatus",
            )
        if len(encoded) > MAX_CONCAT_B64_LEN:
            # Reject before decode — never log the body (mitigation: no 20MB+ in logs).
            raise SceneConcatError(
                detail=(
                    f"concatenated video exceeds the {MAX_CONCAT_B64_LEN // (1024 * 1024)} MB "
                    "size cap; compose fewer/shorter clips"
                ),
                route="runVideoFxConcatenation",
            )
        try:
            video_bytes = base64.b64decode(encoded)
        except ValueError as e:  # binascii.Error subclasses ValueError
            # Don't include the (undecodable) body in the message.
            raise SceneConcatError(
                detail="concatenation returned undecodable video data",
                route="runVideoFxCheckConcatenationStatus",
            ) from e
        if video_bytes[4:8] != b"ftyp":
            raise SceneConcatError(detail="concatenation output is not a valid MP4")
        return video_bytes

    async def concatenate_scene(
        self,
        inputs: list[ConcatInput],
        *,
        out_path: Path,
        poll_interval: float = 3.0,
        timeout_s: float = 180.0,
    ) -> AnyPath:
        """Render a scene's clips into ONE extended MP4 via Flow's server-side
        concatenation. Credit-free, no reCAPTCHA, no ffmpeg.

        Pipeline: ``POST runVideoFxConcatenation`` → poll
        ``runVideoFxCheckConcatenationStatus`` (each poll is its own
        ``_post_json``, so the Page pool is free during the ``asyncio.sleep`` —
        no nested checkout, no deadlock) until ``MEDIA_GENERATION_STATUS_SUCCESSFUL``.
        The combined MP4 is returned inline as base64 in ``encodedVideo``.

        Writes to ``out_path`` (or the configured cloud ``storage_uri``) and
        returns the write target. Raises ``SceneConcatError`` if the job fails
        or returns no/invalid video, ``TransportTimeoutError`` on poll timeout.
        """
        if not inputs:
            msg = "concatenate_scene requires at least one clip"
            raise ValueError(msg)
        op = await self._post_json(
            routes.RUN_VIDEO_FX_CONCATENATION,
            {"inputVideos": [i.to_wire() for i in inputs]},
            route_name="runVideoFxConcatenation",
        )
        operation = op.get("operation")
        logger.info("scene.concat_started", clips=len(inputs))

        deadline = time.monotonic() + timeout_s
        encoded = await self._poll_concat_until_done(
            operation,
            poll_interval=poll_interval,
            deadline=deadline,
            timeout_s=timeout_s,
        )
        video_bytes = self._decode_concat_video(encoded)
        del encoded  # drop the ~20MB+ payload promptly
        logger.info("scene.concat_completed", bytes=len(video_bytes))

        # Write via the same storage_uri-aware path as download_image.
        storage_uri = self.settings.storage_uri
        if storage_uri:
            try:
                key = out_path.relative_to(self.settings.output_dir).as_posix()
            except ValueError:
                key = out_path.name
            target: AnyPath = storage_path(storage_uri, self.settings.output_dir, key)
        else:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            target = out_path
        await write_asset_async(target, video_bytes)
        return target

    async def upsample_image(
        self,
        *,
        media_id: str,
        project_id: str,
        target_resolution: TargetResolution,
        out_path: Path,
        recaptcha_action: str = "IMAGE_GENERATION",
    ) -> AnyPath:
        """Upscale a platform-generated image to 2K/4K via Flow's ``upsampleImage``.

        reCAPTCHA-gated (a Bearer-only call is 403-walled — REST is not viable),
        so this mints a fresh single-use token, POSTs, and decodes the synchronous
        ``{"encodedImage": <base64>}`` response (no async poll loop). Writes to
        ``out_path`` (or the configured cloud ``storage_uri``) and returns the
        target.

        ``project_id`` is the project that owns ``media_id`` — the live wire
        requires it inside ``clientContext`` (a minimal body 403s even with a
        valid token; confirmed by live smoke). The request is validated BEFORE the
        reCAPTCHA mint so malformed ids fail fast without spending a token.

        4K is Ultra-tier-gated: a 403 on a 4K request surfaces as
        :class:`UpscaleUnavailableError` (exit 22, never auto-retried) rather than
        :class:`WafRejectionError` — both are HTTP 403 on the wire, disambiguated
        by the requested resolution. A 403 on a 2K request stays a WAF rejection.

        The ~5 MB ``encodedImage`` base64 is NEVER logged; it is capped before
        decode, validated by magic bytes, and dropped promptly after decode.
        """
        # Construct (and validate media_id/project_id) BEFORE minting — fail fast.
        base_req = UpsampleImageRequest(
            media_id=media_id,
            project_id=project_id,
            target_resolution=target_resolution,
        )
        logger.info(
            "image.upscale_started",
            media_id=media_id,
            resolution=target_resolution.name,
        )
        token = await self._mint_recaptcha_token(recaptcha_action)
        req: UpsampleImageRequest = _dc_replace(base_req, recaptcha_token=token)
        session_id = f";{int(time.time() * 1000)}"
        try:
            resp = await self._post_json(
                routes.UPSAMPLE_IMAGE,
                build_upsample_image_body(req, session_id=session_id),
                route_name="upsampleImage",
            )
        except WafRejectionError as exc:
            # A 403 on a 4K request is almost certainly the Ultra-tier gate, not a
            # WAF/fingerprint block. Surface the distinct error (exit 22) so callers
            # can branch on "upgrade your plan" — and crucially, NEVER auto-retry it
            # (a retry only inflates per-profile WAF heat and never succeeds).
            if target_resolution is TargetResolution.RES_4K:
                raise UpscaleUnavailableError(
                    detail="4K upscale rejected (HTTP 403) — requires a Flow Ultra subscription",
                    status=403,
                    instance=_make_instance(),
                    route="upsampleImage",
                ) from exc
            raise

        resp_obj: JsonObject = cast("JsonObject", resp) if isinstance(resp, dict) else {}
        encoded = str(resp_obj.get("encodedImage") or "")
        if not encoded:
            raise WireFormatError(
                detail="upsampleImage response missing encodedImage",
                instance=_make_instance(),
                route="upsampleImage",
                discovery={"keys": sorted(resp_obj)},
            )
        if len(encoded) > MAX_UPSAMPLE_B64_LEN:
            # Reject before decode — never log the body (mitigation: no MBs in logs).
            raise WireFormatError(
                detail=(
                    f"upscaled image exceeds the {MAX_UPSAMPLE_B64_LEN // (1024 * 1024)} MB "
                    "size cap"
                ),
                route="upsampleImage",
            )
        try:
            image_bytes = base64.b64decode(encoded)
        except ValueError as exc:  # binascii.Error subclasses ValueError
            raise WireFormatError(
                detail="upsampleImage returned undecodable image data",
                route="upsampleImage",
            ) from exc
        del encoded, resp  # drop the multi-MB payload promptly
        if not _is_png_or_jpeg(image_bytes):
            raise WireFormatError(
                detail="upscaled output is not a valid PNG/JPEG",
                route="upsampleImage",
            )
        logger.info(
            "image.upscale_completed",
            media_id=media_id,
            resolution=target_resolution.name,
            bytes=len(image_bytes),
        )

        storage_uri = self.settings.storage_uri
        if storage_uri:
            try:
                key = out_path.relative_to(self.settings.output_dir).as_posix()
            except ValueError:
                key = out_path.name
            target: AnyPath = storage_path(storage_uri, self.settings.output_dir, key)
        else:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            target = out_path
        target = adjust_key_extension(target, image_bytes)
        await write_asset_async(target, image_bytes)
        return target

    async def _mint_recaptcha_token(self, action: str) -> str:
        """Mint a single-use reCAPTCHA Enterprise token via the client's Page.

        Flow's `batchGenerateImages` (and `batchAsyncGenerateVideoText`) endpoints
        reject requests with empty / stale tokens with HTTP 403 "reCAPTCHA
        evaluation failed". Tokens are single-use, ~2 min TTL — mint fresh per call.

        Extracted from `_drive_image_generation` so unit tests can monkeypatch
        the mint without standing up a real Playwright Page + reCAPTCHA
        Enterprise script (which requires loading `enterprise.js` from Google).
        Production code path is unchanged.
        """
        page = await self._checkout_page()
        try:
            # Patchright evaluates in an isolated world by default, where the
            # page's main-world ``grecaptcha`` global is undefined; the resolver
            # supplies ``isolated_context=False`` for patchright ({} for playwright).
            minter = TokenMinter(page, mint_evaluate_kwargs=mint_evaluate_kwargs())
            return await minter.mint(action)
        finally:
            self._checkin_page(page)

    async def _drive_images_generation(
        self,
        *,
        project_id: str,
        req: GenerateImageRequest,
        recaptcha_action: str,
    ) -> list[GeneratedImage]:
        """Mint a token, call the transport once, and return all images.

        ``req.count`` controls how many images Flow generates (1–4). The UI
        transport clicks the matching x{N} tab so one submission produces N
        images; other transports may fan-out internally, but that is their
        concern. This method is the single place reCAPTCHA minting happens.
        """
        if self.transport is None:
            msg = "FlowApiClient.transport is None — call generate_image inside 'async with client'"
            raise RuntimeError(
                msg,
            )
        token = await self._mint_recaptcha_token(recaptcha_action)
        req_with_token = _dc_replace(req, recaptcha_token=token)
        images = await self.transport.generate_images(
            project_id=project_id,
            request=req_with_token,
        )
        if not images:
            raise ContentPolicyError(
                detail="empty media[]",
                instance=_make_instance(),
                route=routes.batch_generate_images_url(project_id),
            )
        return images

    async def _drive_image_generation(
        self,
        *,
        project_id: str,
        req: GenerateImageRequest,
        recaptcha_action: str,
    ) -> GeneratedImage:
        """Single-image shortcut — delegates to ``_drive_images_generation`` with count=1.

        Forces ``count=1`` on the request before delegating so that callers
        constructing a :class:`GenerateImageRequest` with ``count>1`` and then
        invoking the single-image API still receive exactly one image (no
        silent discard).
        """
        req_one: GenerateImageRequest = _dc_replace(req, count=1)
        images = await self._drive_images_generation(
            project_id=project_id,
            req=req_one,
            recaptcha_action=recaptcha_action,
        )
        return images[0]

    async def generate_image(
        self,
        *,
        project_id: str | None = None,
        req: GenerateImageRequest,
        recaptcha_action: str = "imageGeneration",
    ) -> GeneratedImage:
        """Single-shot Imagen/Narwhal image generation.

        Spec C2: retry+mint live in the per-method closure inside
        :meth:`_drive_image_generation` — fresh token on each attempt.
        Multi-image fan-out is the caller's responsibility
        (see ``generate_images_batch``); this method always returns the FIRST
        media item.

        When ``project_id`` is ``None``, a new Flow project is created
        automatically via :meth:`create_project`.  Existing callers that supply
        an explicit ``project_id`` are unaffected.

        """
        try:
            resolved_project_id: str
            if project_id is None:
                project = await self.create_project()
                resolved_project_id = project.project_id
            else:
                resolved_project_id = project_id
            return await self._drive_image_generation(
                project_id=resolved_project_id,
                req=req,
                recaptcha_action=recaptcha_action,
            )
        except Exception as e:
            if _is_target_closed(e):
                raise BrowserSessionClosedError from e
            raise

    async def generate_images_batch(
        self,
        *,
        project_id: str | None = None,
        req: GenerateImageRequest,
        count: int = 1,
        recaptcha_action: str = "imageGeneration",
    ) -> list[GeneratedImage]:
        """Generate ``count`` images using Flow's native count selector (1–4).

        One transport call, one submission round-trip — the UI transport clicks
        the x{count} tab so Flow produces N images in one shot.

        Args:
            project_id: Flow project ID.  When ``None``, a new project is
                created automatically via :meth:`create_project`.
            req: Shared request (prompt, aspect, reference image, ...).
            count: How many images to generate (1–4, Flow's UI cap).

        Raises:
            ValueError: if ``count`` is outside ``[1, 4]``.
        """
        if not 1 <= count <= 4:
            msg = f"count must be between 1 and 4, got {count}"
            raise ValueError(msg)

        try:
            resolved_project_id: str
            if project_id is None:
                project = await self.create_project()
                resolved_project_id = project.project_id
            else:
                resolved_project_id = project_id

            req_with_count: GenerateImageRequest = _dc_replace(req, count=count)
            return await self._drive_images_generation(
                project_id=resolved_project_id,
                req=req_with_count,
                recaptcha_action=recaptcha_action,
            )
        except Exception as e:
            if _is_target_closed(e):
                raise BrowserSessionClosedError from e
            raise

    async def generate_video(
        self,
        *,
        req: GenerateVideoRequest,
        project_id: str | None = None,
        out_dir: Path | None = None,
        poll_timeout_s: float = 600.0,
        download: bool = True,
        on_started: VideoStartedCallback | None = None,
    ) -> VideoResult:
        """Generate a video via the transport's ``generate_video`` method.

        Routes all video generation through a single client boundary so the
        data-layer recorder (Task 8) can hook in at one place.

        Raises:
            RuntimeError: transport is None (client not entered) or the transport
                doesn't implement :class:`VideoCapableTransport`.
            BrowserSessionClosedError: Playwright target was closed mid-call.
        """
        if self.transport is None:
            msg = "FlowApiClient.transport is None - call generate_video inside 'async with client'"
            raise RuntimeError(
                msg,
            )
        if not isinstance(self.transport, VideoCapableTransport):
            msg = f"transport {type(self.transport).__name__} does not support video generation"
            raise RuntimeError(
                msg,
            )

        try:
            return await self.transport.generate_video(
                request=req,
                project_id=project_id,
                out_dir=out_dir,
                poll_timeout_s=poll_timeout_s,
                download=download,
                on_started=on_started,
            )

        except Exception as e:
            if _is_target_closed(e):
                raise BrowserSessionClosedError from e
            raise

    async def health_check(self) -> bool:
        """Return True if the browser context is alive and on a Google domain.

        Safe to call from long-lived workers. Returns False (never raises) on
        TargetClosedError or any other exception so callers can branch without
        try/except.
        """
        if self._page_queue is None:
            return False
        try:
            page = await self._checkout_page()
            try:
                hostname: str = await page.evaluate("() => document.location.hostname")
                return hostname.endswith(".google") or hostname == "google.com"
            finally:
                self._checkin_page(page)
        except Exception:
            logger.debug("health_check_failed", exc_info=True)
            return False

    # --- Character entity API (issue #145) -----------------------------------

    async def create_entity(self, project_id: str) -> str:
        """Mint a fresh CHARACTER entity for *project_id*. Returns the new entityId.

        Maps to ``POST .../trpc/flow.createEntity``.  Session-cookie auth
        (``application/json`` content-type, same as ``createProject``).
        FREE — no reCAPTCHA, no credit.
        """
        body = {"json": {"projectId": project_id}}
        data = await self._post_json(
            routes.CREATE_ENTITY_URL,
            body,
            content_type=_APPLICATION_JSON,
            route_name="createEntity",
        )
        payload = _unwrap_trpc(data)
        entity_id = payload.get("entityId")
        if not entity_id:
            raise WireFormatError(
                detail=f"createEntity returned no entityId; keys={sorted(payload)}",
                route="createEntity",
            )
        logger.debug("character.entity_created", project_id=project_id, entity_id=entity_id)
        return str(entity_id)

    async def list_characters(self, project_id: str) -> list[Character]:
        """Return all CHARACTER entities in *project_id*.

        Maps to ``GET .../trpc/flow.projectInitialData?input=…``.
        Session-cookie auth.  FREE — no reCAPTCHA, no credit.
        """
        trpc_input = json.dumps({"json": {"projectId": project_id}}, separators=(",", ":"))
        url = f"{routes.PROJECT_INITIAL_DATA_URL}?input={quote(trpc_input, safe='')}"
        data = await self._get_json(url, route_name="projectInitialData")
        chars = parse_characters(_unwrap_trpc(data))
        logger.debug("character.list_fetched", project_id=project_id, count=len(chars))
        return chars

    async def get_character(
        self,
        project_id: str,
        *,
        entity_id: str | None = None,
        name: str | None = None,
    ) -> Character:
        """Fetch a single :class:`Character` by ``entity_id`` or ``name``.

        Raises :class:`~gflow_cli.errors.ConfigurationError` when:
        * no character matches (``entity_id`` / ``name`` not found), or
        * multiple characters share the same ``name`` (ambiguous).

        Exactly one of ``entity_id`` or ``name`` must be supplied.

        Note: ``name`` matching is case-sensitive (exact ``display_name`` equality)
        in Phase 1.
        """
        if entity_id is None and name is None:
            msg = "Provide either entity_id or name"
            raise ValueError(msg)
        chars = await self.list_characters(project_id)
        if entity_id is not None:
            match = [c for c in chars if c.entity_id == entity_id]
        else:
            match = [c for c in chars if c.display_name == name]
        if not match:
            lookup = entity_id if entity_id is not None else repr(name)
            raise ConfigurationError(
                detail=f"character not found: {lookup}",
                route="projectInitialData",
                remediation_hint="Run `gflow character list` to see available characters.",
            )
        if len(match) > 1:
            ids = ", ".join(c.entity_id for c in match)
            raise ConfigurationError(
                detail=f"ambiguous character name {name!r} matches multiple entities: {ids}",
                route="projectInitialData",
                remediation_hint="Use --entity-id to select one character unambiguously.",
            )
        return match[0]

    async def patch_entity(
        self,
        *,
        project_id: str,
        entity_id: str,
        display_name: str,
        workflow_ids: list[str],
        voice: str | None = None,
        personality: str | None = None,
    ) -> None:
        """Update a CHARACTER entity's display name, image references, and optional
        voice/personality fields via Bearer PATCH to ``flow/entities``.

        Maps to ``PATCH .../flow/entities``.  Bearer auth (aisandbox).
        FREE — no reCAPTCHA, no credit.

        Only the fields supplied are written; absent optional fields are omitted
        from both the request body and the ``updateMask``.
        """
        character_info: JsonObject = {
            "imageReferences": [{"workflowId": w} for w in workflow_ids],
        }
        update_mask_parts = [
            "entityInfo.displayName",
            "entityInfo.characterInfo.imageReferences",
        ]

        if personality is not None:
            character_info["personalityNotes"] = personality
            update_mask_parts.append("entityInfo.characterInfo.personalityNotes")

        if voice is not None:
            character_info["audioReferences"] = [{"presetVoiceId": voice}]
            update_mask_parts.append("entityInfo.characterInfo.audioReferences")

        entity_info: JsonObject = {
            "displayName": display_name,
            "characterInfo": character_info,
        }
        body: JsonObject = {
            "entity": {
                "projectId": project_id,
                "entityId": entity_id,
                "entityInfo": entity_info,
            },
            "updateMask": ",".join(update_mask_parts),
        }
        await self._patch_json(
            routes.FLOW_ENTITIES_URL,
            body,
            route_name="patchEntity",
        )
        logger.debug(
            "character.entity_patched",
            project_id=project_id,
            entity_id=entity_id,
            workflow_count=len(workflow_ids),
        )

    async def delete_characters(self, project_id: str, entity_ids: list[str]) -> None:
        """Delete one or more CHARACTER entities from *project_id*.

        Maps to ``POST .../v1/flow:batchDeleteAssets`` with body
        ``{"projectId": …, "entityIds": [...]}``.  Bearer auth (aisandbox).
        FREE — no reCAPTCHA, no credit.  Reverse-engineered from the editor's
        "Excluir personagem" button (scripts/dev/spike_char_delete.py).
        """
        if not entity_ids:
            msg = "entity_ids must be non-empty"
            raise ValueError(msg)
        body = {"projectId": project_id, "entityIds": list(entity_ids)}
        await self._post_json(
            routes.BATCH_DELETE_ASSETS_URL,
            body,
            route_name="batchDeleteAssets",
        )
        logger.debug(
            "character.entities_deleted",
            project_id=project_id,
            count=len(entity_ids),
        )

    async def generate_character_image(
        self,
        *,
        project_id: str,
        entity_id: str,
        req: CharacterImageRequest,
        image_reference_index: int = 0,
        locale: str = "en-US",
    ) -> tuple[str, str, AnyPath | None]:
        """Generate a character reference image via the UI transport and return
        ``(workflow_id, primary_media_id, local_path)``.

        All generation is UI-driven (Option B passive capture) — this method
        NEVER posts directly to a generation REST endpoint.  The transport's
        ``generate_character_images`` is the only call that may trigger network
        I/O.

        The generated image is downloaded INSIDE this client boundary using the
        signed ``fifeUrl`` carried by the captured response.  That signed URL is
        used ONLY for the download and is NEVER returned to the caller or logged
        in cleartext — the saga/recorder/DB only ever see the local file path and
        stable ids (scenario #16).  When the captured response carries no
        downloadable image, ``local_path`` is ``None`` (a warning is logged) and
        the method still returns the ids so the saga can proceed.

        Args:
            project_id: Flow project that owns the character entity.
            entity_id: The CHARACTER entity whose editor will be driven.
            req: :class:`~gflow_cli.api.character.CharacterImageRequest` DTO
                (prompt, aspect, model, face_media_id, …).
            image_reference_index: 0-based slot index for the character image
                reference (0 = face/first slot).
            locale: BCP-47 locale forwarded to the UI transport so Flow renders
                in the correct language.  Defaults to ``"en-US"``.

        Returns:
            ``(workflow_id, primary_media_id, local_path)`` — the two stable ids
            extracted from the first returned workflow plus the LOCAL path the
            generated image was downloaded to (``None`` if it could not be
            downloaded).  No value is a signed URL.

        Raises:
            RuntimeError: transport is ``None`` (client not entered / not set up).
            :class:`~gflow_cli.errors.WireFormatError`: the returned workflow's
                ``parentEntityId`` does not match ``entity_id`` (foreign workflow
                guard, scenario #5), or no workflows were returned.
        """
        if self.transport is None:
            msg = (
                "FlowApiClient.transport is None — call generate_character_image "
                "inside 'async with client' with a Chrome-strategy profile"
            )
            raise RuntimeError(msg)

        _raw = await self.transport.generate_character_images(  # type: ignore[attr-defined]
            project_id=project_id,
            entity_id=entity_id,
            request=req,
            image_reference_index=image_reference_index,
            locale=locale,
        )
        _images, workflows = cast(
            "tuple[list[GeneratedImage], list[JsonObject]]",
            _raw,
        )

        if not workflows:
            raise WireFormatError(
                detail="generate_character_images returned no workflows",
                route="generateCharacterImage",
            )

        wf: JsonObject = workflows[0]
        parent: str | None = wf.get("parentEntityId")
        if parent != entity_id:
            raise WireFormatError(
                detail=f"workflow parentEntityId {parent!r} != entity {entity_id!r}",
                route="generateCharacterImage",
            )

        workflow_id: str = cast(str, wf["name"])
        media_id: str = cast(str, wf["metadata"]["primaryMediaId"])

        # ------------------------------------------------------------------
        # Download the generated image INSIDE the client boundary.
        # The signed fifeUrl lives on images[0]; it is used ONLY for the
        # download here and is NEVER returned to the saga or logged in
        # cleartext (scenario #16). The caller receives only the local path.
        # ------------------------------------------------------------------
        local_path: AnyPath | None = None
        image: GeneratedImage | None = _images[0] if _images else None
        if image is not None and image.fife_url:
            out_path = character_output_path(
                self.settings.output_dir,
                entity_id=entity_id,
                slot=image_reference_index,
            )
            local_path = await self.download_image(image, out_path)
        else:
            logger.warning(
                "character.image_no_download_url",
                entity_id=entity_id,
                workflow_id=workflow_id,
                slot=image_reference_index,
            )

        logger.info(
            "character.image_generated",
            entity_id=entity_id,
            workflow_id=workflow_id,
            slot=image_reference_index,
            saved=local_path is not None,
        )
        return workflow_id, media_id, local_path


def _default_project_title() -> str:
    return datetime.now().strftime("gflow-cli %b %d, %I:%M %p")


def _strip_query(url: str) -> str:
    """Return ``url`` with its query string and fragment removed.

    Signed CDN URLs from ``flow-content.google`` carry a time-limited
    ``Signature=...&Expires=...`` query — that's a bearer-style credential
    for the resource. We strip it before passing the URL to ``FlowApiError``
    so it cannot leak via ``str(exc)`` or any log line that formats the
    exception. See docs/SECURITY.md ("No cookies, no tokens, no API keys").
    """
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _validate_fife_url(url: str) -> None:
    """Reject non-HTTPS URLs and hosts outside Google's CDN namespace.

    SSRF guard: ``GeneratedImage.fife_url`` is parsed verbatim from the
    server response. If that response is tampered with (or the DTO is
    constructed from untrusted input), the URL could point to internal
    services (``http://169.254.169.254/``, ``http://localhost:6006/``,
    ``http://127.0.0.1/``, ...). Playwright's ``max_redirects`` does not
    constrain the redirect target host, so we validate up-front.

    Allowlist: scheme must be ``https`` AND host must be ``flow-content.google``
    or any subdomain of ``.google``. The captured samples (see
    ``samples/captured/06_batchGenerateImages.json``) only ever serve from
    ``flow-content.google``; the ``.google`` allowance is a small concession
    for any future CDN swap that stays inside Google's TLD.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        msg = f"Refusing non-HTTPS download URL: scheme={parts.scheme!r}"
        raise ValueError(msg)
    host = parts.hostname or ""
    if not (host == "flow-content.google" or host.endswith(".google")):
        msg = f"Refusing download from unexpected host: {host!r}"
        raise ValueError(msg)


def _make_instance() -> str:
    """Build the RFC 9457 ``instance`` URI from the current correlation context.

    Returns ``gflow:error:<correlation_id>`` so error tracking can group
    occurrences without leaking the failed route URL. When no correlation
    context is bound (e.g. unit tests run outside the CLI boundary),
    ``correlation_id`` resolves to an empty string and we still emit a
    well-formed prefix to keep the parser side simple.
    """
    correlation = structlog.contextvars.get_contextvars().get("correlation_id", "")
    return f"gflow:error:{correlation}"


def _build_wire_format_discovery(resp: Any, body_text: str, route: str) -> JsonObject:
    """Build the RFC 9457 ``discovery`` payload for a :class:`WireFormatError`.

    Shared between the JSON-parse-failure raise site (``_post_json``,
    ``_drive_image_generation``) and the 4xx-fallthrough
    raise site (``_raise_for_non_retryable``) so the ``top_level_keys`` and
    ``body_prefix_redacted`` fields are populated uniformly. Addresses
    code-review MEDIUM-3 about cross-raise-site consistency.

    ``top_level_keys`` is the SORTED list of top-level dict keys if the body
    parses as JSON; ``[]`` otherwise (matches the pre-fixup behavior for the
    non-JSON branch).
    """
    try:
        content_type = resp.headers.get("content-type", "") if hasattr(resp, "headers") else ""
    except (AttributeError, TypeError):
        content_type = ""
    top_keys: list[str] = []
    try:
        parsed = json.loads(body_text) if content_type.startswith(_APPLICATION_JSON) else None
        if isinstance(parsed, dict):
            top_keys = sorted(cast("JsonObject", parsed).keys())
    except ValueError:  # json.JSONDecodeError is a ValueError subclass
        top_keys = []
    # SECURITY: redact BEFORE truncating to 200 chars. If we truncated first,
    # a body slightly over 200 chars could carry an intact reCAPTCHA token in
    # the prefix and the redactor (which parses JSON) would fail to recognize
    # it (truncated JSON is invalid → returns "<unparseable body redacted>"
    # which is safe by accident but not by design). Audit gap #11.
    return {
        "route_name": route,
        "http_status": resp.status,
        "content_type": content_type,
        "top_level_keys": top_keys,
        "body_prefix_redacted": _redact_for_log(body_text)[:200],
    }


def _is_png_or_jpeg(data: bytes) -> bool:
    """True if *data* begins with a PNG or JPEG magic-byte signature.

    Guards against writing a non-image payload (e.g. an error blob that decoded
    as base64) to an image file. PNG: ``89 50 4E 47 0D 0A 1A 0A``; JPEG/JFIF:
    ``FF D8 FF``.
    """
    return data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff"


def _raise_for_non_retryable(resp: Any, body_text: str, *, route: str) -> None:
    """Classify a response that survived the retry loop.

    Called on responses that EITHER succeeded (2xx) OR fell through with a
    non-retryable 4xx (e.g. 400, 401, 403, 404, 422). Anything outside those
    ranges should have been raised inside the retry loop and never reach
    here. Side-effect-only: raises on 4xx, returns silently on 2xx.

    * 401 → :class:`AuthExpiredError`
    * 403 → :class:`WafRejectionError` (reCAPTCHA/WAF wall, not auth expiry)
    * other 4xx → :class:`WireFormatError` with discovery payload so
      ``grep error_class=WireFormatError`` reveals what was unexpected.
    """
    if resp.status < 400:
        return
    instance = _make_instance()
    if resp.status == 401:
        raise AuthExpiredError(
            detail=f"HTTP {resp.status}",
            status=resp.status,
            instance=instance,
            route=route,
        )
    if resp.status == 403:
        # 403 on a Flow route is the reCAPTCHA/WAF wall, NOT auth expiry
        # (direct-REST generation is 403-walled — see docs/CHARACTER.md §11).
        raise WafRejectionError(
            detail=f"HTTP {resp.status}",
            status=resp.status,
            instance=instance,
            route=route,
        )
    if 400 <= resp.status < 500:
        raise WireFormatError(
            detail=f"HTTP {resp.status} on 4xx fallthrough",
            status=resp.status,
            instance=instance,
            route=route,
            discovery=_build_wire_format_discovery(resp, body_text, route),
        )


def _redact_for_log(body_str: str) -> str:
    """Replace any reCAPTCHA token in a JSON request body with ``<redacted>``.

    The Flow batch image/video routes embed the token in two places:

    - root ``clientContext.recaptchaContext.token``
    - each ``requests[*].clientContext.recaptchaContext.token``

    If parsing fails (the body is not the JSON shape we expect), we degrade
    safely by returning a string that hides the body entirely — better to
    lose log fidelity than to leak a token because of a parser hiccup.
    """
    try:
        parsed = json.loads(body_str)
    except ValueError:  # json.JSONDecodeError is a ValueError subclass
        return "<unparseable body redacted>"

    if not isinstance(parsed, dict):
        return body_str

    parsed_dict = cast("JsonObject", parsed)
    _redact_in_client_context(parsed_dict.get("clientContext"))
    requests_list = parsed_dict.get("requests")
    if isinstance(requests_list, list):
        for item in cast("list[Any]", requests_list):
            if isinstance(item, dict):
                _redact_in_client_context(cast("JsonObject", item).get("clientContext"))

    return json.dumps(parsed_dict)


def _redact_headers_for_log(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of `headers` with any `authorization` value masked.

    The SOLE permitted way to log a headers dict — `_redact_for_log` covers
    request bodies only, not headers. Spec §4.5.
    """
    redacted = dict(headers)
    auth = redacted.get("authorization")
    if auth is not None:
        redacted["authorization"] = f"Bearer <len={len(auth)}>"
    return redacted


def _redact_in_client_context(client_context: Any) -> None:
    """Mutate ``client_context["recaptchaContext"]["token"]`` to ``<redacted>``
    if present. No-op for any non-dict shape."""
    if not isinstance(client_context, dict):
        return
    ctx_dict = cast("JsonObject", client_context)
    recaptcha = ctx_dict.get("recaptchaContext")
    if isinstance(recaptcha, dict):
        recaptcha_dict = cast("JsonObject", recaptcha)
        if "token" in recaptcha_dict:
            recaptcha_dict["token"] = "<redacted>"
