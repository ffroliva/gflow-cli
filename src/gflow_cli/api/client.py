"""FlowApiClient — typed wrapper around Flow's private REST surface.

Architecture: the client manages its own Playwright persistent-context
lifecycle (async context manager). All HTTP goes through `page.request`
so Google's session cookies attach automatically — no manual bearer-token
extraction.

The video-generation route requires a fresh reCAPTCHA token per call;
that piece lives in `gflow_cli.api.recaptcha` and `generate_video()` (added
in a later commit). For now this client implements the four routes that
DON'T need reCAPTCHA: createProject, uploadImage, checkStatus, download.

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
import secrets
import time
import uuid
from collections.abc import Sequence
from dataclasses import replace as _dc_replace
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

import structlog
from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from gflow_cli.api import routes
from gflow_cli.api._retry import parse_retry_after, post_with_retry
from gflow_cli.api.dto import AssetInfo, GeneratedImage, ProjectInfo, VideoOperation, VideoStatus
from gflow_cli.api.image import GenerateImageRequest
from gflow_cli.api.recaptcha import TokenMinter
from gflow_cli.api.transports import make_transport
from gflow_cli.api.transports.base import FlowTransportStrategy
from gflow_cli.api.video import GenerateVideoRequest, build_generate_body
from gflow_cli.config import Settings
from gflow_cli.errors import (
    AuthExpiredError,
    ContentPolicyError,
    FlowApiError,  # re-exported via gflow_cli.api.__init__
    NetworkError,
    RateLimitError,
    WireFormatError,
)

# Silence "imported but unused" — FlowApiError is re-exported from this module
# via ``gflow_cli.api.__init__`` for back-compat with Phase 3 call sites.
__all__ = ["FlowApiClient", "FlowApiError"]

logger = structlog.get_logger(__name__)

# Cap matches Flow's UI upload limit (~20 MB observed in captured traffic). Used
# by `upload_image` to reject oversize files BEFORE reading them into memory —
# protects this process from OOM and the remote endpoint from DoS-shaped traffic.
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB

# aisandbox-pa rejects application/json — see samples/captured/*.json.
_AISANDBOX_CONTENT_TYPE = "text/plain;charset=UTF-8"


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
    if header[:6] in (b"GIF87a", b"GIF89a"):
        return True
    return False


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
    ) -> None:
        self.profile_dir = profile_dir
        self.headless = headless
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

    async def __aenter__(self) -> FlowApiClient:
        # --- Step 1: Launch Playwright FIRST so self._page is ready before
        # transport.setup() is called.  This order is load-bearing for S1
        # (EvaluateFetchTransport): it needs a live Page passed via the
        # ``page=`` kwarg so it can reuse the client's context instead of
        # opening a second Playwright process against the same profile dir
        # (which would conflict on the Chromium lockfile — spec § 5.4.4).
        self._pw = await async_playwright().start()
        from gflow_cli.browser_manager import channel_for_profile

        self._context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            viewport={"width": 1280, "height": 720},
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            channel=channel_for_profile(self.profile_dir),
            ignore_default_args=["--enable-automation", "--no-sandbox"],
            args=["--disable-blink-features=AutomationControlled"],
        )
        # Hide the automation flag so reCAPTCHA Enterprise doesn't score
        # the session as a bot — navigator.webdriver=true causes low-score
        # tokens and HTTP 403 on batchGenerateImages.
        await self._context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
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
            routes.EDITOR_BOOTSTRAP_URL, wait_until="domcontentloaded", timeout=60_000
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
            await self.transport.setup(self.profile_dir, page=self._page)
            self._owns_transport = True
        else:
            # Caller-owned: pre-initialized FlowTransportStrategy instance.
            # Do NOT call setup() — the caller already did that.
            self.transport = inp
            self._owns_transport = False

        return self

    async def __aexit__(self, *exc: object) -> None:
        try:
            if self._context:
                try:
                    await self._context.close()
                except Exception:
                    # Browser cleanup is best-effort but MUST be surfaced for
                    # diagnosis (CLAUDE.md: never silently swallow errors).
                    logger.warning("browser_context_close_error", exc_info=True)
            if self._pw:
                try:
                    await self._pw.stop()
                except Exception:
                    logger.warning("playwright_stop_error", exc_info=True)
            if self._owns_transport and self.transport is not None:
                try:
                    await self.transport.teardown()
                except Exception:
                    logger.warning("transport_teardown_error", exc_info=True)
        finally:
            # Always reset pool state — even if close() raised — so a
            # reused client instance doesn't keep dangling references to a
            # dead BrowserContext's Pages.
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
            raise RuntimeError("FlowApiClient not entered — use `async with`")
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
            raise RuntimeError("FlowApiClient not entered — use `async with`")
        return self._page

    # --- private HTTP helpers --------------------------------------------

    async def _post_json(
        self,
        url: str,
        body: dict[str, Any],
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

        async def attempt() -> Any:
            page = await self._checkout_page()
            try:
                if os.environ.get("GFLOW_CLI_LOG_REQUEST_HEADERS") == "1":
                    logger.info(
                        "request_headers",
                        url=url,
                        headers=_redact_headers_for_log({"content-type": content_type}),
                    )
                return await page.request.post(
                    url,
                    data=body_str,
                    headers={"content-type": content_type},
                )
            finally:
                self._checkin_page(page)

        resp = await self._run_with_retry(attempt, route=route)
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
        self, url: str, body: dict[str, Any], *, route_name: str | None = None
    ) -> Any:
        body_str = json.dumps(body)
        logger.debug("patch_json", url=url, body=body_str[:300])
        route = route_name or url

        async def attempt() -> Any:
            page = await self._checkout_page()
            try:
                if os.environ.get("GFLOW_CLI_LOG_REQUEST_HEADERS") == "1":
                    logger.info(
                        "request_headers",
                        url=url,
                        headers=_redact_headers_for_log({"content-type": _AISANDBOX_CONTENT_TYPE}),
                    )
                return await page.request.patch(
                    url,
                    data=body_str,
                    headers={"content-type": _AISANDBOX_CONTENT_TYPE},
                )
            finally:
                self._checkin_page(page)

        resp = await self._run_with_retry(attempt, route=route)
        text = await resp.text()
        _raise_for_non_retryable(resp, text, route=route)
        try:
            return json.loads(text) if text else {}
        except json.JSONDecodeError:
            return {}

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
        data = await self._post_json(routes.CREATE_PROJECT, body, content_type="application/json")
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
            raise ValueError(
                f"Image too large: {size / 1_048_576:.1f} MB exceeds "
                f"{MAX_IMAGE_BYTES // 1_048_576} MB limit"
            )

        # Staged read: validate magic bytes first (12 B) before loading the full
        # file. Run both reads in a worker thread to keep the event loop free.
        def _read_header(p: Path) -> bytes:
            with p.open("rb") as fh:
                return fh.read(12)

        header = await asyncio.to_thread(_read_header, image_path)
        if not _is_supported_image_header(header):
            raise ValueError(f"Not a supported image format: {image_path.name}")
        full_bytes = await asyncio.to_thread(image_path.read_bytes)
        b64 = base64.b64encode(full_bytes).decode()
        body = {
            "clientContext": {"projectId": project_id, "tool": "PINHOLE"},
            "imageBytes": b64,
        }
        data = await self._post_json(routes.UPLOAD_IMAGE, body)
        return AssetInfo.from_upload_response(data)

    async def get_video_status(self, project_id: str, media_names: list[str]) -> list[VideoStatus]:
        """Poll the status of one or more in-flight video generations.

        Maps to `POST /v1/video:batchCheckAsyncVideoGenerationStatus`.
        """
        body = {"media": [{"name": n, "projectId": project_id} for n in media_names]}
        data = await self._post_json(routes.CHECK_VIDEO_STATUS, body)
        return [VideoStatus.from_check_status_item(it) for it in data.get("media", [])]

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

    async def download_image(self, image: GeneratedImage, out_path: Path) -> Path:
        """Download a generated image's signed ``fifeUrl`` straight to disk.

        Distinct from :meth:`download`: ``fifeUrl`` is already a fully
        qualified signed CDN URL on ``flow-content.google`` (carrying
        ``Expires=...&Signature=...``), so we MUST NOT route it through
        ``routes.media_download_url`` — that helper builds the
        labs.google redirect path which doesn't apply here.

        Args:
            image: The :class:`GeneratedImage` returned from
                :meth:`generate_image` / :meth:`generate_images_batch`.
            out_path: Destination file path. Parent directories are
                created if missing.

        Returns:
            ``out_path`` for ergonomic chaining.

        Raises:
            FlowApiError: when the CDN responds with a 4xx/5xx status
                (e.g. the signature has already expired). The ``route``
                attribute carries a query-stripped URL so the time-limited
                ``Signature=...`` token cannot leak through logs.
            ValueError: when ``image.fife_url`` is not an HTTPS URL on a
                trusted Google host (SSRF guard — the response is parsed
                from the wire, so we refuse to follow attacker-controlled
                URLs blindly).
        """
        _validate_fife_url(image.fife_url)
        out_path.parent.mkdir(parents=True, exist_ok=True)
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
        out_path.write_bytes(await resp.body())
        return out_path

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

    async def generate_video(
        self,
        *,
        project_id: str,
        req: GenerateVideoRequest,
        seed: int | None = None,
        recaptcha_action: str = "videoGeneration",
        batch_id: str | None = None,
    ) -> VideoOperation:
        """Enqueue a Veo video generation. Returns the operation reference.

        Spec C2: mints a fresh reCAPTCHA token INSIDE the retry loop body, on
        the worker's OWN checked-out Page, EVERY attempt. The single-use Flow
        token has a ~2 min TTL — reusing a stale token across retries is the
        most common cause of "INVALID_ARGUMENT" on the second attempt of a
        flaky generation.
        """
        resolved_seed = seed if seed is not None else secrets.randbelow(2**31)
        resolved_batch_id = batch_id or _new_batch_id()
        route_name = "batchAsyncGenerateVideoText"

        async def attempt() -> Any:
            page = await self._checkout_page()
            try:
                minter = TokenMinter(page)
                token = await minter.mint(recaptcha_action)
                body = build_generate_body(
                    req,
                    project_id=project_id,
                    recaptcha_token=token,
                    batch_id=resolved_batch_id,
                    seed=resolved_seed,
                    session_id=f";{int(time.time() * 1000)}",
                )
                logger.debug(
                    "post_json",
                    url=routes.GENERATE_VIDEO,
                    body=_redact_for_log(json.dumps(body))[:300],
                )
                if os.environ.get("GFLOW_CLI_LOG_REQUEST_HEADERS") == "1":
                    logger.info(
                        "request_headers",
                        url=routes.GENERATE_VIDEO,
                        headers=_redact_headers_for_log({"content-type": _AISANDBOX_CONTENT_TYPE}),
                    )
                return await page.request.post(
                    routes.GENERATE_VIDEO,
                    data=json.dumps(body),
                    headers={"content-type": _AISANDBOX_CONTENT_TYPE},
                )
            finally:
                self._checkin_page(page)

        response = await self._run_with_retry(attempt, route=route_name)
        text = await response.text()
        _raise_for_non_retryable(response, text, route=route_name)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise WireFormatError(
                detail=f"non-JSON response: {text[:200]}",
                status=response.status,
                instance=_make_instance(),
                route=route_name,
                discovery=_build_wire_format_discovery(response, text, route_name),
            ) from e
        return VideoOperation.from_generate_response(data)

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
            minter = TokenMinter(page)
            return await minter.mint(action)
        finally:
            self._checkin_page(page)

    async def _drive_image_generation(
        self,
        *,
        project_id: str,
        req: GenerateImageRequest,
        seed: int,
        batch_id: str,
        recaptcha_action: str,
    ) -> GeneratedImage:
        """Per-shot drive of one ``flowMedia:batchGenerateImages`` request.

        Flow requires a freshly-minted reCAPTCHA Enterprise token on every
        ``batchGenerateImages`` request (single-use, ~2 min TTL). Minting
        requires a real Page context — Google's reCAPTCHA JS runs in the
        browser, not in httpx. So the **client** owns minting (it holds the
        persistent Chromium context) and the **strategy** owns sending.

        A.7 + Phase B: the strategy receives the request with the freshly
        minted ``recaptcha_token`` attached, builds the body via the shared
        ``_build_batch_generate_images_body`` (which reads
        ``request.recaptcha_token``), and sends via its transport mechanism.

        ``seed`` and ``batch_id`` are currently consumed inside the body
        builder via the request object after Phase A.2 moved
        ``recaptcha_token`` there. They are kept in the signature for caller
        APIs; the strategy receives ``req`` enriched with the live token.
        """
        if self.transport is None:
            raise RuntimeError(
                "FlowApiClient.transport is None — call generate_image inside 'async with client'"
            )
        # Mint a single-use reCAPTCHA token via the client's Page (extracted to
        # a method so unit tests can monkeypatch it without standing up a real
        # Playwright Page + reCAPTCHA Enterprise script).
        token = await self._mint_recaptcha_token(recaptcha_action)

        # `seed` + `batch_id` are reserved here for future extension; the
        # strategy uses what's already on the request.
        _ = seed, batch_id  # suppress unused-variable warnings
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
        return images[0]

    async def generate_image(
        self,
        *,
        project_id: str,
        req: GenerateImageRequest,
        seed: int | None = None,
        recaptcha_action: str = "imageGeneration",
        batch_id: str | None = None,
    ) -> GeneratedImage:
        """Single-shot Imagen/Narwhal image generation.

        Spec C2: retry+mint live in the per-method closure inside
        :meth:`_drive_image_generation` — fresh token on each attempt.
        Multi-image fan-out is the caller's responsibility
        (see ``generate_images_batch``); this method always returns the FIRST
        media item.

        Idempotency: calling twice with the same ``seed`` and ``batch_id``
        yields identical bodies modulo the per-call reCAPTCHA token AND the
        per-attempt session-id timestamp.
        """
        return await self._drive_image_generation(
            project_id=project_id,
            req=req,
            seed=seed if seed is not None else secrets.randbelow(2**31),
            batch_id=batch_id or _new_batch_id(),
            recaptcha_action=recaptcha_action,
        )

    async def generate_images_batch(
        self,
        *,
        project_id: str,
        req: GenerateImageRequest,
        count: int = 1,
        seeds: Sequence[int] | None = None,
        recaptcha_action: str = "imageGeneration",
    ) -> list[GeneratedImage]:
        """Fan out N parallel image generations sharing one ``batchId``.

        Spec C2: each of the N parallel tasks runs ITS OWN retry+mint loop
        (see :meth:`_drive_image_generation`) on its OWN checked-out Page.
        This sidesteps the previous shared-Page bottleneck where N tokens had
        to be minted sequentially (because ``page.evaluate`` is not re-entrant)
        before any POST could fire. With the per-worker pool, mints happen
        concurrently — one per Page.

        Args:
            project_id: Flow project ID.
            req: Shared request (prompt, aspect, reference image, ...).
            count: How many images to generate. Must be 1..4 (Flow UI cap).
            seeds: Optional explicit seeds. Defaults to ``count`` random
                31-bit ints. If provided, ``len(seeds)`` must equal ``count``.

        Returns:
            ``list[GeneratedImage]`` in the same order as ``seeds`` (or the
            order of internally generated seeds if ``seeds`` is None).

        Raises:
            ValueError: if ``count`` is outside ``[1, 4]`` or ``seeds``
                length disagrees with ``count``.
            FlowApiError: if any of the parallel calls fail. The first failure
                propagates immediately via asyncio.gather(return_exceptions=False);
                remaining in-flight requests are not cancelled and may complete
                (or fail) in the background. With count <= 4 the leakage is
                time-bounded and the tokens expire harmlessly.
        """
        if not 1 <= count <= 4:
            raise ValueError(f"count must be between 1 and 4, got {count}")
        seeds_list: list[int]
        if seeds is None:
            seeds_list = [secrets.randbelow(2**31) for _ in range(count)]
        elif len(seeds) != count:
            raise ValueError(f"len(seeds)={len(seeds)} does not match count={count}")
        else:
            seeds_list = list(seeds)

        shared_batch_id = _new_batch_id()

        # asyncio.gather preserves input order in its result list, so the
        # caller sees results in the same order as `seeds` even though the
        # network calls (and per-shot retry loops) may complete out of order.
        return await asyncio.gather(
            *(
                self._drive_image_generation(
                    project_id=project_id,
                    req=req,
                    seed=s,
                    batch_id=shared_batch_id,
                    recaptcha_action=recaptcha_action,
                )
                for s in seeds_list
            ),
            return_exceptions=False,
        )


def _default_project_title() -> str:
    return datetime.now().strftime("gflow-cli %b %d, %I:%M %p")


def _new_batch_id() -> str:
    """Generate a fresh batch ID for the mediaGenerationContext."""
    return str(uuid.uuid4())


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
        raise ValueError(f"Refusing non-HTTPS download URL: scheme={parts.scheme!r}")
    host = parts.hostname or ""
    if not (host == "flow-content.google" or host.endswith(".google")):
        raise ValueError(f"Refusing download from unexpected host: {host!r}")


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


def _build_wire_format_discovery(resp: Any, body_text: str, route: str) -> dict[str, Any]:
    """Build the RFC 9457 ``discovery`` payload for a :class:`WireFormatError`.

    Shared between the JSON-parse-failure raise site (``_post_json``,
    ``generate_video``, ``_drive_image_generation``) and the 4xx-fallthrough
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
        parsed = json.loads(body_text) if content_type.startswith("application/json") else None
        if isinstance(parsed, dict):
            top_keys = sorted(cast(dict[str, Any], parsed).keys())
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


def _raise_for_non_retryable(resp: Any, body_text: str, *, route: str) -> None:
    """Classify a response that survived the retry loop.

    Called on responses that EITHER succeeded (2xx) OR fell through with a
    non-retryable 4xx (e.g. 400, 401, 403, 404, 422). Anything outside those
    ranges should have been raised inside the retry loop and never reach
    here. Side-effect-only: raises on 4xx, returns silently on 2xx.

    * 401/403 → :class:`AuthExpiredError`
    * other 4xx → :class:`WireFormatError` with discovery payload so
      ``grep error_class=WireFormatError`` reveals what was unexpected.
    """
    if resp.status < 400:
        return
    instance = _make_instance()
    if resp.status in (401, 403):
        raise AuthExpiredError(
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

    parsed_dict = cast(dict[str, Any], parsed)
    _redact_in_client_context(parsed_dict.get("clientContext"))
    requests_list = parsed_dict.get("requests")
    if isinstance(requests_list, list):
        for item in cast(list[Any], requests_list):
            if isinstance(item, dict):
                _redact_in_client_context(cast(dict[str, Any], item).get("clientContext"))

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
    ctx_dict = cast(dict[str, Any], client_context)
    recaptcha = ctx_dict.get("recaptchaContext")
    if isinstance(recaptcha, dict):
        recaptcha_dict = cast(dict[str, Any], recaptcha)
        if "token" in recaptcha_dict:
            recaptcha_dict["token"] = "<redacted>"
