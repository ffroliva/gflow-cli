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
import sys
import time
import uuid
from dataclasses import replace as _dataclass_replace
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, NoReturn, Self, TypeVar, cast
from urllib.parse import quote, urlsplit, urlunsplit

import structlog
from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from gflow_cli.api import routes, video_extend
from gflow_cli.api._engine import (
    CONTEXT_TEARDOWN_TIMEOUT_S,
    DRIVER_STOP_TIMEOUT_S,
    active_engine,
    close_context_bounded,
    log_engine_selected,
    mint_evaluate_kwargs,
    resolve_async_playwright,
    run_teardown_step,
)
from gflow_cli.api._retry import parse_retry_after, post_with_retry
from gflow_cli.api.character import Character, CharacterImageRequest, parse_characters
from gflow_cli.api.dto import (
    AssetInfo,
    GeneratedImage,
    GenerationCheckpoint,
    GenerationCheckpointObserver,
    ProjectInfo,
)
from gflow_cli.api.image_upscale import (
    TargetResolution,
    UpsampleImageRequest,
    build_upsample_image_body,
)
from gflow_cli.api.recaptcha import TokenMinter
from gflow_cli.api.scene import ConcatInput, Scene, SceneWorkflow
from gflow_cli.api.transports import (
    STANDALONE_ONLY_TRANSPORTS,
    make_transport,
    resolve_transport_name,
)
from gflow_cli.api.transports._common import await_url_settled, raise_if_migrated
from gflow_cli.api.transports.base import (
    FlowTransportStrategy,
    SupportsTransportSetup,
    TransportSetup,
    VideoCapableTransport,
)
from gflow_cli.api.video import (
    VideoStatus,
    is_media_uuid,
    media_name_from_generate_response,
    parse_video_status,
)
from gflow_cli.api.video_extend import ExtendStarted
from gflow_cli.browser_manager import channel_for_profile
from gflow_cli.config import BrowserEngine, Settings
from gflow_cli.diagnostics import IncidentRecorder, run_retention, validated_incidents_root
from gflow_cli.errors import (
    CONTENT_SAFETY_REASONS,
    AisandboxAuthError,
    AuthExpiredError,
    AuthMissingError,
    BrowserSessionClosedError,
    ConfigurationError,
    ContentPolicyError,
    FlowApiError,  # re-exported via gflow_cli.api.__init__
    NetworkError,
    ProfileLockedError,
    RateLimitError,
    SceneConcatError,
    TransportTimeoutError,
    UpscaleUnavailableError,
    WafRejectionError,
    WireFormatError,
    classify_content_safety,
)
from gflow_cli.paths import adjust_key_extension, character_output_path, looks_like_video
from gflow_cli.profile_lease import ProfileLease
from gflow_cli.profile_store import (
    NOT_REDIRECTED,
    next_locale_state,
    read_account_locale,
    write_account_locale,
)
from gflow_cli.redaction import redact_sensitive_text
from gflow_cli.storage import AnyPath, storage_path, write_asset_async
from gflow_cli.winsec import ensure_profile_hardened

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from _typeshed import DataclassInstance

    from gflow_cli.api.image import AgentInstruction, GenerateImageRequest, ProjectBrief
    from gflow_cli.api.video import (
        GenerateVideoRequest,
        VideoResult,
        VideoStarted,
        VideoStartedCallback,
    )

# Shorthand for an untyped JSON-ish string-keyed mapping (request/response
# bodies, launch kwargs, etc.). A single definition avoids the duplicated-literal
# smell (SonarCloud S1192) from repeating the bare mapping type across the module.
JsonObject = dict[str, Any]

# Floor for the outbound video status poll. The cheapest extend model takes ~110s,
# so a short interval buys nothing and spends requests against a WAF-scored host.
_MIN_VIDEO_POLL_INTERVAL_S = 5.0

_DataclassT = TypeVar("_DataclassT", bound="DataclassInstance")


def _dc_replace(obj: _DataclassT, /, **changes: Any) -> _DataclassT:
    """Typed wrapper over ``dataclasses.replace`` that preserves the input type.

    The stdlib annotation for ``replace`` is opaque to some analyzers (they infer
    a generic ``DataclassInstance``), which produced spurious argument-type and
    declared-type findings at call sites. Re-declaring it with a ``TypeVar`` makes
    the return type flow through as the concrete request dataclass.
    """
    return _dataclass_replace(obj, **changes)


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
# The labs.google BFF authenticates on cookies alone, but Google still rejects a
# same-site tRPC MUTATION that arrives with no origin/referer as cross-site /
# CSRF-shaped, answering 401. Every other lane in the tree already sent one or
# both headers — the aisandbox lane, the agentic driver, the sapisidhash
# transport, the httpx auth probe. The labs tRPC production lane was the only
# one without, which is why `project create` failed while UI-automation
# generation kept succeeding on the same profile and the same cookie jar.
_LABS_REFERER = "https://labs.google/fx/tools/flow"
_LABS_BFF_HEADERS = {"origin": _LABS_ORIGIN, "referer": _LABS_REFERER}
_SESSION_API_URL = "https://labs.google/fx/api/auth/session"
# issue #222: the NextAuth Flow session cookie + the URL used to seed it.
_FLOW_SESSION_COOKIE = "__Secure-next-auth.session-token"
_FLOW_COOKIE_URL = _LABS_ORIGIN


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


async def validate_image_file(image_path: Path) -> None:
    """Reject a file that must not be uploaded to Flow, before any bytes leave.

    Exists, within :data:`MAX_IMAGE_BYTES`, and a real image by its first 12
    bytes — the header check is what stops a resolved path (a symlink, a typo)
    from laundering ``~/.ssh/id_rsa`` into a Flow project. Raises the same
    ``FileNotFoundError`` / ``ValueError`` the REST upload has always raised.
    """
    if not image_path.exists():
        raise FileNotFoundError(image_path)
    size = image_path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        msg = (
            f"Image too large: {size / 1_048_576:.1f} MB exceeds "
            f"{MAX_IMAGE_BYTES // 1_048_576} MB limit"
        )
        raise ValueError(msg)

    # Staged read: validate magic bytes first (12 B) before loading the full
    # file. Run both reads in a worker thread to keep the event loop free.
    def _read_header(p: Path) -> bytes:
        with p.open("rb") as fh:
            return fh.read(12)

    if not _is_supported_image_header(await asyncio.to_thread(_read_header, image_path)):
        msg = f"Not a supported image format: {image_path.name}"
        raise ValueError(msg)


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


def _storage_key_from_path(out_path: Path, output_dir: Path) -> str:
    """Compute a relative storage key from *out_path*.

    Attempts ``out_path.relative_to(output_dir)`` first. If *out_path* is not
    relative to *output_dir*, preserves a relative path's full POSIX key or
    falls back to ``out_path.name`` for absolute paths outside *output_dir*.
    """
    try:
        return out_path.relative_to(output_dir).as_posix()
    except ValueError:
        return out_path.as_posix() if not out_path.is_absolute() else out_path.name


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
        # Account locale segment, resolved once from the bootstrap navigation (#580),
        # then cached per profile (#587).
        self._account_locale: str | None = None
        # Cross-process profile lease (D3). Acquired in _enter_setup immediately
        # BEFORE the persistent context launches (so contention fails fast with
        # ProfileLockedError instead of racing two Chromes onto one profile dir)
        # and released in _close_browser_resources AFTER the context + driver
        # shut down. This client is the owning boundary for its own context;
        # transports it drives reuse this context (shared-page path) and never
        # take a second lease.
        self._lease: ProfileLease | None = None
        # Session-scoped private incident recorder (incident-diagnostics design
        # §6.2). Constructed in __aenter__ before lease acquisition so profile
        # contention can produce a metadata-only incident without Chrome.
        self._recorder: IncidentRecorder | None = None
        self._recorder_handlers: list[tuple[Any, str, Any]] = []
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
        # issue #222: Flow cookies read from the profile BEFORE the headed
        # generation context launches, used to seed that context if it cannot
        # decrypt the on-disk cookie store (macOS Keychain vs basic-store
        # mismatch). Populated by _preread_flow_session_cookies().
        self._preread_flow_cookies: dict[str, str] = {}
        # projectInitialData per project — see capability_listing.
        self._extend_listing_cache: dict[str, JsonObject] = {}

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
        # Incident recorder first (design §6.2 step 1): settings + correlation
        # context are available, and lease contention below must be able to
        # produce a metadata-only incident. Retention is pure cleanup and runs
        # even when capture is DISABLED (an opt-out must not freeze previously
        # accumulated bundles — incl. sensitive/ screenshots — on disk forever);
        # the existing-dir guard just avoids creating incidents/ for users who
        # never captured. to_thread keeps the sweep's stat/parse work off the
        # event loop (the daemon/MCP server enter clients on their shared loop).
        self._recorder = IncidentRecorder(self.settings)
        if self._recorder.enabled or (self.settings.home / "incidents").is_dir():
            try:
                root = validated_incidents_root(self.settings.home)
                if root is not None:
                    await asyncio.to_thread(run_retention, root)
            except Exception:  # noqa: BLE001 — retention must never block a command
                logger.warning("incident.retention_error", exc_info=True)
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
        core path. The returned dict is identical to the previous inline call,
        plus an optional ``record_har_path`` when ``GFLOW_CLI_HAR_PATH`` is set.
        """
        kwargs: JsonObject = {
            "user_data_dir": str(self.profile_dir),
            "headless": self.headless,
            "viewport": {"width": 1280, "height": 720},
            "locale": "en-US",
            "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9"},
            "channel": channel_for_profile(self.profile_dir),
            "ignore_default_args": [
                "--enable-automation",
                "--no-sandbox",
            ],
            # Pass --password-store=basic EXPLICITLY (issue #222). auth login
            # (auth/real_chrome.py:69) and verification (auth/verification.py:246)
            # seal and read the profile's cookies with Chrome's *basic* store —
            # as does every other launch site in the codebase (auth/cookies.py,
            # auth/internal_chromium.py, browser_manager.py, ui_automation.py).
            # This shared generation context was the ONE path that omitted the
            # flag and merely relied on Playwright's internal default; on macOS
            # that let Chrome read cookies via the OS Keychain ("Chrome Safe
            # Storage"), which cannot decrypt the basic-sealed cookies -> a
            # logged-out context -> HTTP 401 at project.createProject (login and
            # verify succeed, generation fails). #225 added a comment but never
            # the flag here. Passing it explicitly keeps all paths symmetric
            # regardless of Playwright's defaults.
            "args": [
                "--password-store=basic",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        }
        if self.settings.har_path is not None:
            self.settings.har_path.parent.mkdir(parents=True, exist_ok=True)
            kwargs["record_har_path"] = str(self.settings.har_path)
            logger.warning(
                "client.har_capture_enabled",
                har_path=str(self.settings.har_path),
                hint="HAR file will contain full request/response bodies, headers, "
                "and cookies — do not share it publicly or attach it to a public "
                "bug report.",
            )
        return kwargs

    def _log_and_guard_launch(self, kwargs: dict[str, Any]) -> None:
        """Log the resolved browser-launch identity and fail loud on a silent
        chrome->bundled downgrade (issue #222).

        Under the 'chrome' strategy ``channel`` must resolve to ``"chrome"``
        (system Chrome). If it resolved to ``None`` Chrome wasn't found and
        Playwright would fall back to bundled Chromium. On macOS that bundled
        Chromium cannot decrypt cookies written by real Chrome (per-app Keychain
        'Chrome Safe Storage'), producing a logged-out context and a confusing
        HTTP 401 at project.createProject. Make that fatal with a clear
        remediation. On other platforms the bundled fallback may still work
        (e.g. Windows DPAPI cookie key is per-user), so warn instead of raising —
        UNLESS the #477 engine guard below detects that the bundled Chromium's
        major version is older than the one that last wrote the profile: that
        launch would trigger Chromium's downgrade cleanup and can shred the
        session store, so it hard-stops on every platform.

        The diagnostic event names the resolved channel / executable /
        user-data-dir / cookie-db presence — the data needed to tell a channel
        downgrade apart from a cookie-decryption failure.
        """
        from gflow_cli.browser_manager import (
            chrome_strategy_requested,
            ensure_profile_engine_compatible,
            resolved_chrome_binary,
        )
        from gflow_cli.paths import get_cookies_path

        channel = kwargs.get("channel")
        wants_chrome = chrome_strategy_requested(self.profile_dir)
        executable = resolved_chrome_binary()
        # Resolve the cookie file the way auth/verification does (Chrome 130+
        # Default/Network/Cookies, then legacy Default/Cookies, then bundled
        # Cookies). Logging the actual path discriminates the H2 cookie-location
        # mismatch (the persistent context reading a different file than the one
        # the session was written to) from a plain decryption failure. See #222.
        cookies_db = None
        try:
            cookies_db = get_cookies_path(self.profile_dir)
        except FileNotFoundError:
            pass
        launch_args: list[str] = kwargs.get("args") or []
        logger.info(
            "client.persistent_context_launch",
            channel=channel,
            chrome_strategy_requested=wants_chrome,
            chrome_executable=executable,
            user_data_dir=str(self.profile_dir),
            cookies_db_present=cookies_db is not None,
            cookies_db_path=str(cookies_db) if cookies_db else None,
            platform=sys.platform,
            # issue #222: surface the actual launch args so we can confirm the
            # generation context passes --password-store=basic (cookie-store
            # symmetry with login/verification) on the failing macOS path.
            launch_args=launch_args,
            ignore_default_args=kwargs.get("ignore_default_args"),
            password_store_basic="--password-store=basic" in launch_args,
        )
        if wants_chrome and channel is None:
            msg = (
                "Profile requests the 'chrome' browser strategy "
                "(.gflow_browser_strategy=chrome) but Playwright's 'chrome' channel is "
                "unavailable (Google Chrome is not at the location Playwright probes — "
                "note a Chrome binary resolved elsewhere is logged as chrome_executable "
                "but does not satisfy the channel), so generation would fall back to "
                "Playwright's bundled Chromium. On macOS the bundled Chromium cannot "
                "decrypt cookies written by real Chrome (Keychain 'Chrome Safe Storage'), "
                "yielding a logged-out session and an HTTP 401 at project.createProject. "
                "Install Google Chrome in its default location (or set CHROME_BINARY), "
                "then retry; or re-run `gflow auth login` to re-capture the session."
            )
            if sys.platform == "darwin":
                raise ConfigurationError(msg)
            logger.warning("client.chrome_strategy_downgraded", detail=msg)
        # #477: refuse a bundled-Chromium open of a profile last written by a
        # newer Chromium — downgrade cleanup can shred the session store.
        ensure_profile_engine_compatible(self.profile_dir, channel)

    async def _preread_flow_session_cookies(self) -> None:
        """#222: read the profile's Flow cookies BEFORE the headed generation
        context launches, so _ensure_context_session_cookie can seed them if the
        headed context fails to decrypt the on-disk store (macOS Keychain vs
        basic-store mismatch).

        Why pre-launch: ``get_chrome_cookie_snapshot``'s fallback opens a
        *headless* ``--password-store=basic`` Chrome context on the same profile —
        the one read path that decrypts on macOS — and a second persistent context
        cannot be opened once the headed context holds the profile's singleton
        lock.

        Best-effort: any failure leaves the pre-read empty and the launch proceeds
        unchanged. Cheap where browser_cookie3 succeeds (no browser is launched).
        """
        try:
            from gflow_cli.auth.cookies import get_chrome_cookie_snapshot

            snapshot = await get_chrome_cookie_snapshot(self.profile_dir)
            self._preread_flow_cookies = dict(snapshot.httpx_cookies)
            logger.info(
                "client.preread_flow_cookies",
                preread_count=len(self._preread_flow_cookies),
                preread_session=_FLOW_SESSION_COOKIE in self._preread_flow_cookies,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort; must never break launch
            logger.warning("client.preread_flow_cookies_failed", error=type(exc).__name__)
            self._preread_flow_cookies = {}

    async def _ensure_context_session_cookie(self) -> None:
        """Diagnostic + seed (issue #222): log whether the launched persistent
        context loaded the Flow session cookie and, if it did not, seed it from
        the pre-launch snapshot.

        The accepting path (auth ``verify_flow_profile``) reads cookies via
        browser_cookie3 + httpx and never uses a persistent context; generation
        uses THIS persistent Chrome context. On macOS that headed context can load
        ZERO cookies — it cannot decrypt the profile's basic-store-sealed jar — so
        the Flow session never loads and project.createProject 401s, even though
        login + verification succeed (they read via the headless
        ``--password-store=basic`` path, which DOES decrypt). We seed from
        ``self._preread_flow_cookies`` (captured pre-launch via that same working
        reader; see ``_preread_flow_session_cookies``) so the context carries the
        session. No-op where the context loads the cookie itself (e.g. Windows).

        The diagnostic logs presence / count / expiry only — never values, so it
        is safe to paste into a public issue. Pure best-effort: never raises,
        never breaks the launch.
        """
        if self._context is None:
            return
        try:
            cookies = await self._context.cookies()
        except Exception as exc:  # noqa: BLE001 — must never break the launch
            logger.warning("client.context_cookie_probe_error", error=type(exc).__name__)
            return
        now = time.time()
        flow_cookie = next(
            (c for c in cookies if c.get("name") == _FLOW_SESSION_COOKIE),
            None,
        )
        flow_expires = flow_cookie.get("expires") if flow_cookie is not None else None
        logger.info(
            "client.context_cookie_state",
            context_cookie_count=len(cookies),
            flow_session_cookie_present=flow_cookie is not None,
            # expires == -1 -> a session cookie (no persisted expiry); otherwise
            # epoch seconds. Chromium prunes already-expired cookies from the
            # jar, so the common "logged out" signal is present=False; this flag
            # only catches a near-boundary expiry that survived into the jar.
            flow_session_cookie_expired=(
                flow_expires is not None and flow_expires != -1 and flow_expires < now
            ),
            google_sapisid_present=any(c.get("name") == "SAPISID" for c in cookies),
        )
        if flow_cookie is not None:
            return  # context loaded the session cookie — nothing to seed
        if not self._preread_flow_cookies:
            logger.warning("client.context_cookie_seed_unavailable")
            return
        try:
            await self._context.add_cookies(
                [
                    {"name": name, "value": value, "url": _FLOW_COOKIE_URL}
                    for name, value in self._preread_flow_cookies.items()
                ]
            )
            logger.info(
                "client.context_cookies_seeded",
                seeded_count=len(self._preread_flow_cookies),
                seeded_session=_FLOW_SESSION_COOKIE in self._preread_flow_cookies,
            )
        except Exception as exc:  # noqa: BLE001 — seeding must never break the launch
            logger.warning("client.context_cookie_seed_error", error=type(exc).__name__)

    async def _enter_setup(self) -> None:
        """Body of __aenter__ after the Playwright driver starts.

        Extracted so __aenter__ can wrap it in a partial-setup leak guard
        (a failed launch must not orphan a chrome process).
        """
        # Invariant: __aenter__ sets self._pw immediately before calling us.
        assert self._pw is not None
        # issue #222 (macOS): pre-read the profile's Flow cookies BEFORE launching
        # the headed context, so _ensure_context_session_cookie can seed them if the
        # headed context can't decrypt the on-disk store. Must run pre-launch — the
        # snapshot's fallback opens a headless context on the same profile, which
        # would deadlock on the singleton lock once the headed context holds it.
        await self._preread_flow_session_cookies()
        kwargs = self._persistent_context_kwargs()
        if self._recorder is not None:
            self._recorder.note_har_pre_launch(self.settings.har_path)
        # Own the profile BEFORE Chrome launches. Acquire here (not earlier):
        # _preread_flow_session_cookies above may itself momentarily own a
        # headless context on this profile (its cookie-decrypt fallback), which
        # takes and releases its own lease first — acquiring earlier would make
        # this a same-process double-acquire. Contention raises ProfileLockedError
        # (exit 11) here, before any Chrome process starts.
        try:
            # aacquire so a #478 opt-in wait polls with asyncio.sleep instead
            # of blocking the event loop (daemon tasks share it).
            self._lease = await ProfileLease(self.profile_dir).aacquire()
        except ProfileLockedError as exc:
            # Metadata-only incident BEFORE any Chrome exists (S07); the
            # partial-setup guard's _close_browser_resources finalizes it.
            if self._recorder is not None:
                ref = await self._recorder.capture_metadata_only(exc, phase="profile_lease")
                if ref is not None and exc.incident_ref is None:
                    exc.incident_ref = ref
            raise
        # Guard AFTER the lease (#478 review): during an opt-in lease wait the
        # holder rewrites 'Last Version' exactly as it releases, so a
        # pre-wait check would validate a stale value (TOCTOU). A raise here
        # is safe post-acquire: the partial-setup leak guard releases the
        # lease via _close_browser_resources.
        self._log_and_guard_launch(kwargs)
        # We own the profile now — one-time Windows DACL sweep for profiles
        # created before #472 (marker-gated stat afterwards; no-op off Windows).
        ensure_profile_hardened(self.profile_dir)
        self._context = await self._launch_persistent_context(kwargs)
        # Attach journal listeners BEFORE any navigation/submission that can
        # produce relevant traffic (design §6.2 step 2, S30).
        self._attach_recorder_context(self._context)
        # Hide the automation flag so reCAPTCHA Enterprise doesn't score
        # the session as a bot — navigator.webdriver=true causes low-score
        # tokens and HTTP 403 on batchGenerateImages.
        await self._context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})",
        )
        # issue #222: log whether the headed context loaded the Flow session
        # cookie and, if not (macOS can't decrypt the on-disk store), seed it
        # from the pre-launch snapshot.
        await self._ensure_context_session_cookie()
        # Open ``Settings.concurrency`` Pages inside the one persistent
        # BrowserContext. ``launch_persistent_context`` opens one Page by
        # default; reuse it as slot 0 to avoid an unused N+1 Page.
        n = max(1, self.settings.concurrency)
        self._pages = await self._open_page_pool(n)
        # Console/page-error listeners on every pooled page; the context-level
        # "page" event (attached above) covers pages observed later (S16).
        for pooled in self._pages:
            self._attach_recorder_page(pooled)
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
        await self._bootstrap_and_resolve_locale()

        # --- Step 2: Resolve and set up transport, passing the live Page so
        # S1 can share this context rather than opening its own.
        await self._setup_transport()

    async def _bootstrap_and_resolve_locale(self) -> None:
        """Navigate the bootstrap page and settle the account locale (#580, #587).

        The probe (#580) costs the full ``URL_SETTLE_TIMEOUT_MS`` on any account
        Flow does **not** redirect: ``wait_for_url`` never matches, so it runs to
        timeout on every command. That single case is the entire cost, and the
        cache answers exactly one question: *does this account redirect?*

        The navigation stays **bare, always** — never to a cached segment. Flow
        serves whatever segment it is asked for, so only a bare navigation makes
        it state the account's own answer; see the CHANGELOG entry for #587 and
        ``scripts/dev/spike_locale_poison.py``.

        The settle is skipped only on :data:`NOT_REDIRECTED`, which
        :func:`next_locale_state` reaches only after two runs agree — so a single
        transient timeout cannot disable it.
        """
        assert self._page is not None
        cached = read_account_locale(self.profile_dir)
        await self._page.goto(
            routes.EDITOR_BOOTSTRAP_URL,
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        # #639: NOT_REDIRECTED means "there is no redirect to wait for". It must not
        # ALSO mean "do not read the locale" — which is what returning here made it
        # mean, and that made the state ABSORBING: `_resolve_account_locale` is the
        # only site of the <html lang> recovery (#643) AND the only caller of
        # `next_locale_state`, so nothing could ever move a latched profile off it.
        # Every profile that saw two migrated loads on <=0.66.0 latched this way,
        # which is exactly the population #643 was written for. Measured 2026-09-03
        # on `ffroliva`: latched, and the page declares lang="en" on every load.
        settle = cached != NOT_REDIRECTED
        self._account_locale, from_url = await self._resolve_account_locale(
            self._page, settle=settle
        )
        if not settle:
            # Kept (not merged into account_locale_state) because field reports key
            # on this event to tell "the settle was skipped" from "it timed out".
            logger.info(
                "client.account_locale_cached",
                locale=self._account_locale,
                settle_skipped=True,
            )
            # Do NOT fold this observation into the cache. The cached state answers
            # ONE question — "does Flow redirect this account?" — and NOT_REDIRECTED
            # is a true answer here. Writing the <html lang> locale into it would
            # make `cached != NOT_REDIRECTED` on the next run, turning the settle
            # back on for good and reintroducing the 4 s URL_SETTLE_TIMEOUT_MS that
            # #587 removed. Measured on `ffroliva` when this return was missing:
            # two `transport.url_settle_gave_up` timeouts and a warm bootstrap of
            # 7.41 s, which `scripts/dev/measure_locale_probe.py` flagged as "warm
            # arm slower than cold". Re-deriving the locale costs one sub-ms
            # `page.evaluate` per run, which is strictly cheaper than persisting it.
            return
        # #639: fold ONLY the URL-derived segment. The cached state answers "does
        # Flow redirect this account?", and a locale read from `<html lang>` is no
        # evidence of a redirect — every account declares one. Folding it in made
        # `next_locale_state` record a segment for an account Flow serves bare,
        # which switched the settle on permanently and cost the full 4 s
        # URL_SETTLE_TIMEOUT_MS on every bootstrap thereafter. That shipped in
        # v0.66.1 with #643's `<html lang>` fallback and is measurable on any
        # non-redirecting account: `scripts/dev/measure_locale_probe.py` reports
        # two `transport.url_settle_gave_up` timeouts per run.
        state = next_locale_state(cached, from_url)
        if state != cached:
            logger.info("client.account_locale_state", was=cached, now=state)
        write_account_locale(self.profile_dir, state)

    async def _resolve_account_locale(
        self, page: Any, *, settle: bool = True
    ) -> tuple[str | None, str | None]:
        """Settle the bootstrap navigation and read the account's locale (#580).

        Flow redirects the editor to the ACCOUNT's locale, but that redirect
        lands after ``goto`` returns — settling is what makes it observable.
        ``None`` means "build bare URLs", which is never worse than the
        hardcoded ``en-US`` this replaces.

        Returns ``(locale, from_url)`` — the same segment twice when the URL
        answered, and ``(lang_segment, None)`` when only ``<html lang>`` did.
        **The two are not interchangeable** (#639): the caller uses ``locale`` to
        build URLs, but folds only ``from_url`` into the cached state, because
        that cache answers "does Flow redirect this account?" and a ``lang``
        attribute is no evidence of a redirect — every account has one.

        ``settle=False`` (#639) skips only the wait, not the read. A profile
        cached :data:`NOT_REDIRECTED` has nothing to wait for — measured 60/60 on
        `ffroliva` — but its page still declares a locale in ``<html lang>``, and
        skipping the whole function to save the wait is what made that cache an
        absorbing state.
        """
        settled = await await_url_settled(page) if settle else None
        segment = routes.locale_segment_from_url(settled or "")
        if segment is not None:
            logger.info("client.account_locale_resolved", locale=segment, url=settled)
            return segment, segment
        # #643: the migrated flow.google.com origin serves /project/<id> with no
        # locale segment, so the URL can never answer there — but Flow still
        # renders the account locale into <html lang>. Without this the resolver
        # returns None and `next_locale_state` DEMOTES an already-learned locale
        # to PROVISIONAL (measured: a pt account lost its "pt" on every migrated
        # load). Best-effort: a probe failure must never break navigation.
        lang = await self._settled_lang(page)
        segment = routes.locale_segment_from_lang_attr(lang)
        if segment is not None:
            logger.info("client.account_locale_resolved", locale=segment, source="html_lang")
            return segment, None
        logger.info("client.account_locale_unresolved", last_url=settled)
        return None, None

    #: How long to wait for `<html lang>` to leave the server-default shell value
    #: (#651). Measured on a pt account, two consecutive loads: the attribute reads
    #: `en` until ~1.9 s and flips to `pt` at 2.26 / 2.49 s. 4 s leaves margin
    #: without being open-ended; a slower network could still exceed it, which the
    #: `lang_unchanged` event makes visible rather than silent.
    _LANG_SETTLE_TIMEOUT_MS: ClassVar[float] = 4_000.0

    async def _settled_lang(self, page: Any) -> str | None:
        """Read ``<html lang>`` only after hydration has had a chance to set it (#651).

        Flow serves an **`en` shell** and the app rewrites ``lang`` during
        hydration, so a single early read returns ``en`` for every account. That is
        not a migrated-origin quirk — it was measured on the OLD host:

        ===========  ==========  ==============
        t (ms)       ``lang``    ``readyState``
        ===========  ==========  ==============
        887          ``en``      interactive
        1510         ``en``      **complete**
        2092         ``en``      complete
        **2488**     **``pt``**  complete
        ===========  ==========  ==============

        **``readyState`` is not a usable signal** — it reaches ``complete`` a full
        second *before* the flip, so the obvious "wait for complete, then read" is
        wrong. The DOM node count oscillates (136 → 300 → 249 → 251) and does not
        discriminate either. Nothing cheap predicts the flip, so this observes it.

        Cost: an account whose real locale IS the shell default never changes the
        attribute and pays the full timeout once per process. That is the price of
        not guessing, and it is bounded; the alternative shipped a wrong locale for
        every account whose URL could not answer.
        """
        try:
            first = await page.evaluate("() => document.documentElement.lang || ''")
        except Exception as exc:  # noqa: BLE001 - observation only
            logger.info("client.account_locale_lang_probe_failed", error=type(exc).__name__)
            return None
        try:
            await page.wait_for_function(
                "initial => (document.documentElement.lang || '') !== initial",
                arg=first,
                timeout=self._LANG_SETTLE_TIMEOUT_MS,
            )
            after = await page.evaluate("() => document.documentElement.lang || ''")
        except Exception as exc:  # noqa: BLE001 - a timeout is an ANSWER, not a failure
            # Either the account's locale genuinely equals the shell default, or the
            # page never hydrated within the window. Both leave the first read as the
            # best available answer — which is exactly the pre-#651 behaviour, so this
            # branch can never be worse than what it replaces.
            logger.info(
                "client.account_locale_lang_unchanged",
                lang=first,
                waited_ms=self._LANG_SETTLE_TIMEOUT_MS,
                reason=type(exc).__name__,
            )
            return first
        logger.info("client.account_locale_lang_settled", was=first, now=after)
        return after

    async def _launch_persistent_context(self, kwargs: JsonObject) -> BrowserContext:
        """Launch the persistent context; translate a launch-time crash into ProfileLockedError."""
        assert self._pw is not None
        try:
            return await self._pw.chromium.launch_persistent_context(**kwargs)
        except Exception as exc:
            if _is_target_closed(exc):
                # A TargetClosedError at LAUNCH usually means the profile dir
                # is held by another Chrome — most commonly a stale browser
                # leaked by a crashed prior run (issue #293) or a concurrent
                # gflow run. It CAN also be an unrelated startup crash, so
                # the original error is carried in the detail and the wording
                # hedges rather than asserts.
                first_line = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
                locked = ProfileLockedError(
                    detail=(
                        f"the browser exited immediately while launching on profile "
                        f"dir {self.profile_dir} — most likely another Chrome holds "
                        f"it (original error: {first_line})"
                    ),
                    remediation_hint=(
                        "Another gflow run — or a stale Chrome left by a crashed one "
                        "— may hold this profile. Close Chrome windows using this "
                        "profile (or kill the Chrome processes — chrome.exe on "
                        "Windows — whose command line names the profile dir) and "
                        "re-run, or use a different --profile. If no such process "
                        "exists, the browser crashed at startup for another reason; "
                        "see the original error above."
                    ),
                )
                # This launch-crash contention (#293's stale-Chrome case) is the
                # COMMON profile-lock path — it gets the same metadata-only
                # incident the lease-contention raise does (S07).
                if self._recorder is not None:
                    ref = await self._recorder.capture_metadata_only(locked, phase="browser_launch")
                    if ref is not None and locked.incident_ref is None:
                        locked.incident_ref = ref
                raise locked from exc
            raise

    async def _open_page_pool(self, n: int) -> list[Page]:
        """Open ``n`` Pages in the context, reusing the default Page as slot 0."""
        assert self._context is not None
        pages: list[Page] = []
        if self._context.pages:
            pages.append(self._context.pages[0])
            for _ in range(n - 1):
                pages.append(await self._context.new_page())
        else:
            for _ in range(n):
                pages.append(await self._context.new_page())
        return pages

    async def _setup_transport(self) -> None:
        """Resolve and set up ``self.transport``, passing the live Page so S1 can share it.

        Branch on the discriminating types (str, None) so pyright narrows
        the else-branch to FlowTransportStrategy. We deliberately avoid
        `@runtime_checkable` on the Protocol — that would freeze its
        public surface and constrain future evolution.
        """
        inp = self._transport_input
        config = self._build_transport_setup()
        if inp is None or isinstance(inp, str):
            # Standalone-only guard: bearer/sapisidhash (S2/S3) discard the shared
            # page and re-acquire the profile lease in their own setup(), so with
            # this client already holding self._lease they self-lock with an opaque
            # ProfileLockedError. Refuse fast with a clear message. Only fires when
            # the lease is actually held (i.e. inside a live client), so the same
            # transports stay usable standalone.
            if self._lease is not None:
                resolved = resolve_transport_name(inp)
                if resolved in STANDALONE_ONLY_TRANSPORTS:
                    raise ConfigurationError(
                        f"Transport {resolved!r} cannot run inside FlowApiClient: it "
                        "acquires its own profile lease during setup and would "
                        "self-lock against the client's lease. Use 'ui_automation' or "
                        f"'evaluate_fetch', or drive {resolved!r} standalone.",
                    )
            # Client-owned: resolve from factory, run full lifecycle.
            # Pass self._page so S1 can reuse the already-open context.
            # S2 and S3 accept and ignore the page= kwarg.
            self.transport = make_transport(inp)
            # Hand the transport its output/storage wiring through the public
            # typed seam so debug screenshots (#18) + video downloads land where
            # the caller expects. Transports that don't opt in are left alone.
            self._apply_transport_setup(self.transport, config)
            await self.transport.setup(self.profile_dir, page=self._page)
            self._owns_transport = True
        else:
            # Caller-owned: pre-initialized FlowTransportStrategy instance.
            # Do NOT call setup() — the caller already did that. Config is still
            # applied (it's plain wiring, not a lifecycle resource we own).
            self.transport = inp
            self._owns_transport = False
            self._apply_transport_setup(self.transport, config)
        if self._recorder is not None:
            self._recorder.transport = getattr(
                self.transport, "name", type(self.transport).__name__
            )

    def _build_transport_setup(self) -> TransportSetup:
        """Assemble the immutable output/storage wiring handed to the transport."""
        rec = self._recorder
        return TransportSetup(
            account_locale=self._account_locale,
            out_dir=self._out_dir,
            storage_uri=self.settings.storage_uri,
            output_dir=self.settings.output_dir,
            # #528: the transport sees request bodies the context-level network
            # listeners cannot decode. None when capture is off.
            record_generation_request=(
                rec.record_generation_request if rec is not None and rec.enabled else None
            ),
        )

    def _apply_transport_setup(
        self, transport: FlowTransportStrategy, config: TransportSetup
    ) -> None:
        """Pass typed setup through the public seam, if the transport opts in.

        Replaces the old ``hasattr``-guarded writes into ``transport.__dict__``:
        the ``isinstance`` gate keeps transports that need no such wiring
        untouched, exactly as the old guard did."""
        if isinstance(transport, SupportsTransportSetup):
            transport.apply_setup(config)

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
        the profile dir.

        Cancellation-complete (D4): each teardown step runs through
        :func:`run_teardown_step` (bounded + shielded), so a ``CancelledError``
        landing mid-context-close cannot skip the driver stop below and cannot
        skip the lease release in ``finally``. The original cancellation is
        captured and re-raised LAST, after every ownership-release step has run.
        Teardown order: (4) close context/browser -> stop driver;
        (6) release the profile lease.
        """
        # Stop accepting incident events and detach listeners BEFORE the
        # context closes — late callbacks become no-ops (design §6.2 step 7).
        self._detach_recorder()
        cancelled: BaseException | None = None
        # Cell (not a bare bool) so the wrapper coroutine below can record the
        # GRACEFUL-close outcome through run_teardown_step, which discards
        # coroutine results and returns only cancellation. "not cancelled" is
        # NOT "closed cleanly" — a timed-out/force-closed context must reach
        # the recorder as close_ok=False or a truncated HAR could be stamped
        # "complete" (har-honesty contract, design §5.6).
        close_result = [False]
        try:
            if self._context is not None:
                # Bounded close + force-close fallback (issue #293) — shared
                # with the transports' own-context teardowns via _engine.
                context = self._context

                async def _close_context_recording() -> None:
                    close_result[0] = await close_context_bounded(context, owner="client")

                cancelled = (
                    await run_teardown_step(
                        _close_context_recording(),
                        timeout=CONTEXT_TEARDOWN_TIMEOUT_S,
                        owner="client",
                        step="context_close",
                    )
                    or cancelled
                )
                # HAR files hold live auth cookies/bearer tokens — higher
                # sensitivity than the CDP lockfile the (now-removed) packaged
                # CDP lifecycle used to harden in browser_manager.py. Playwright
                # writes the HAR lazily on this close, so this is the earliest
                # point the file exists; best-effort only (never fail teardown
                # over a permission tweak). Belongs on the context-present path:
                # a context was launched, so the HAR was written here.
                if self.settings.har_path is not None:
                    try:
                        self.settings.har_path.chmod(0o600)
                    except OSError:
                        logger.warning("client.har_chmod_failed", exc_info=True)
            else:
                # No context was ever launched (metadata-only incidents):
                # nothing to flush, so finalization state is trivially clean.
                close_result[0] = True
            # Context close established the HAR state — finalize every staged
            # manifest now (design §6.2 step 8). Bounded + shielded like every
            # teardown step; finalize_all itself never raises, so it cannot
            # mask a close/driver/lease failure.
            if self._recorder is not None:
                cancelled = (
                    await run_teardown_step(
                        self._recorder.finalize_all(close_ok=close_result[0] and cancelled is None),
                        timeout=CONTEXT_TEARDOWN_TIMEOUT_S,
                        owner="client",
                        step="incident_finalize",
                    )
                    or cancelled
                )
            if self._pw is not None:
                # pw.stop() awaits the Node driver's exit with no deadline of
                # its own — a wedged driver would hang teardown forever, so it
                # is bounded here. It runs even when the context close above was
                # abandoned by cancellation (driver stop force-kills chrome).
                cancelled = (
                    await run_teardown_step(
                        self._pw.stop(),
                        timeout=DRIVER_STOP_TIMEOUT_S,
                        owner="client",
                        step="driver_stop",
                    )
                    or cancelled
                )
        finally:
            # Field resets must survive even cancellation (Ctrl-C mid-close),
            # or a reused client holds references to a dead BrowserContext.
            self._pages = []
            self._page_queue = None
            self._page = None
            self._context = None
            self._pw = None
            # Release the profile lease LAST — after the context is closed and
            # the driver stopped — so the profile dir is genuinely free before
            # another process can acquire it (D3 release ordering). release() is
            # idempotent and never unlinks the lock file.
            if self._lease is not None:
                self._lease.release()
                self._lease = None
        # Re-raise the original cancellation only after teardown completed, so a
        # cancelled close still stopped Playwright and released the lease.
        if cancelled is not None:
            raise cancelled

    # --- incident capture wiring (incident-diagnostics design §6.2) ---------

    def _attach_recorder_context(self, context: Any) -> None:
        """Context-level request/response/failure journal listeners plus the
        "page" hook for late pages. Handlers extract primitives synchronously
        and never retain Playwright objects (S17/S18)."""
        rec = self._recorder
        if rec is None or not rec.enabled or not rec.bookkeeping.mark_attached(id(context)):
            return

        def on_request(request: Any) -> None:
            try:
                rec.record_request(request_key=str(id(request)), monotonic_ts=time.monotonic())
            except Exception:  # noqa: BLE001, S110 — observation only, never break the app
                pass

        def on_response(response: Any) -> None:
            try:
                request = response.request
                rec.record_response(
                    url=response.url,
                    method=request.method,
                    resource_type=request.resource_type,
                    status=response.status,
                    request_key=str(id(request)),
                    monotonic_ts=time.monotonic(),
                )
            except Exception:  # noqa: BLE001, S110
                pass

        def on_request_failed(request: Any) -> None:
            try:
                rec.record_request_failed(
                    url=request.url,
                    method=request.method,
                    resource_type=request.resource_type,
                    failure=request.failure,
                    request_key=str(id(request)),
                    monotonic_ts=time.monotonic(),
                )
            except Exception:  # noqa: BLE001, S110
                pass

        def on_page(page: Any) -> None:
            self._attach_recorder_page(page)

        for event, handler in (
            ("request", on_request),
            ("response", on_response),
            ("requestfailed", on_request_failed),
            ("page", on_page),
        ):
            context.on(event, handler)  # pyright: ignore[reportCallIssue, reportUnknownMemberType, reportArgumentType]
            self._recorder_handlers.append((context, event, handler))

    def _attach_recorder_page(self, page: Any) -> None:
        """Console/page-error listeners; attach-at-most-once per page (S16)."""
        rec = self._recorder
        if rec is None or not rec.enabled or not rec.bookkeeping.mark_attached(id(page)):
            return

        def on_console(msg: Any) -> None:
            try:
                location: dict[str, Any] = msg.location or {}
                rec.record_console(
                    level=msg.type,
                    text=msg.text,
                    url=location.get("url"),
                    line=location.get("lineNumber"),
                    column=location.get("columnNumber"),
                )
            except Exception:  # noqa: BLE001, S110
                pass

        def on_page_error(error: Any) -> None:
            try:
                # playwright wraps every ordinary JS pageerror in its single
                # Error class; the JS constructor name (TypeError, ...) only
                # survives on the .name property.
                rec.record_page_error(
                    error_class=getattr(error, "name", None) or type(error).__name__,
                    message=str(error),
                )
            except Exception:  # noqa: BLE001, S110
                pass

        page.on("console", on_console)
        page.on("pageerror", on_page_error)
        self._recorder_handlers.append((page, "console", on_console))
        self._recorder_handlers.append((page, "pageerror", on_page_error))

    def _detach_recorder(self) -> None:
        """Freeze journals and detach every registered listener exactly once;
        anything arriving afterwards is a no-op (design §6.2 step 7)."""
        rec = self._recorder
        if rec is None:
            return
        rec.detach_and_freeze()
        for target, event, handler in self._recorder_handlers:
            rec.bookkeeping.mark_detached(id(target))
            try:
                target.remove_listener(event, handler)
            except Exception:  # noqa: BLE001, S110 — target may already be closed
                pass
        self._recorder_handlers.clear()

    async def _capture_incident(
        self, exc: BaseException, *, phase: str, route: str | None = None
    ) -> None:
        """Stage a private incident bundle while the page is still alive.

        Best-effort observation only: never raises, never masks the original
        error. Attaches the local IncidentRef to the exception (any type,
        best-effort) so the CLI error paths — typed AND unhandled — can
        surface the bundle path (S21 local half)."""
        rec = self._recorder
        if rec is None:
            return
        ref = await rec.capture_failure(exc, page=self._page, phase=phase, route=route)
        if ref is not None and getattr(exc, "incident_ref", None) is None:
            try:
                exc.incident_ref = ref  # type: ignore[attr-defined]
            except (AttributeError, TypeError):  # __slots__/immutable exceptions
                pass

    async def _raise_with_incident(self, e: Exception, *, phase: str) -> NoReturn:
        """Shared failure boundary for client entry points: map a closed
        Playwright target to the typed retryable error, stage the incident
        bundle while the page is alive, and re-raise (design §6.2 step 5)."""
        if _is_target_closed(e):
            wrapped = BrowserSessionClosedError()
            await self._capture_incident(wrapped, phase=phase)
            raise wrapped from e
        await self._capture_incident(e, phase=phase)
        raise e

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

    async def _request_headers(
        self,
        *,
        url: str,
        content_type: str | None,
        is_aisandbox: bool,
    ) -> dict[str, str]:
        """Build the outgoing header set for ONE ``page.request.*`` call.

        Single source of truth for both hosts, so a header a lane needs cannot
        go missing on only one verb — which is exactly how the labs tRPC lane
        lost ``origin``/``referer``: the headers were assembled inline behind an
        ``if is_aisandbox`` gate in each of ``_post_json``/``_patch_json``/
        ``_get_json``, and labs.google is not aisandbox.

        aisandbox-pa authenticates on a Bearer token (which already carries
        ``origin``); the labs.google BFF authenticates on cookies alone but
        still has to LOOK like the Flow SPA — see :data:`_LABS_BFF_HEADERS`.
        """
        headers: dict[str, str] = {}
        if content_type is not None:
            headers["content-type"] = content_type
        if is_aisandbox:
            headers.update(await self._aisandbox_auth_headers())
        elif url.startswith(_LABS_ORIGIN):
            # Keyed on the URL, not on `not is_aisandbox`. Google's auth checks are
            # origin-bound: an Origin naming a host you are NOT calling fails them.
            # routes.py defines only these two hosts today, so an `else` would be
            # correct by coincidence — a third host added later would silently
            # inherit a labs.google Origin.
            headers.update(_LABS_BFF_HEADERS)
        return headers

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
                headers = await self._request_headers(
                    url=url, content_type=content_type, is_aisandbox=is_aisandbox
                )
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
                headers = await self._request_headers(
                    url=url, content_type=_AISANDBOX_CONTENT_TYPE, is_aisandbox=is_aisandbox
                )
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
                headers = await self._request_headers(
                    url=url, content_type=None, is_aisandbox=is_aisandbox
                )
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

    async def rename_project(self, project_id: str, new_title: str) -> JsonObject:
        """Rename an existing Flow project.

        Maps to `POST .../trpc/project.renameProject`.
        """
        body = {"json": {"projectId": project_id, "projectTitle": new_title}}
        return await self._post_json(routes.RENAME_PROJECT, body, content_type=_APPLICATION_JSON)

    async def patch_agent_info(
        self,
        project_id: str,
        *,
        enabled: bool | None = None,
        cards: tuple[AgentInstruction, ...] | None = None,
    ) -> JsonObject:
        """Patch the agentic settings / instructions for a project.

        Maps to ``PATCH /v1/projects/{project_id}/agentInfo``. The endpoint
        accepts the default ``text/plain`` content-type used by ``_patch_json``
        (``application/json`` also works; ``application/json+protobuf`` is
        rejected 400 — see docs/AGENT_UI_RECON / the instructions spike).

        Returns the ``projectBrief`` echoed back in the PATCH response (Flow
        replies with the full updated ``agentInfo``). There is **no**
        ``GET /agentInfo`` route — this echo is the authoritative read-back, so
        callers (``gflow instructions list`` / enable / disable) confirm state
        from the return value rather than a follow-up GET. Returns ``{}`` when
        nothing was patched or the echo was empty.
        """
        from gflow_cli.api.image import build_agent_brief_cards

        body: dict[str, Any] = {"projectBrief": {}}
        masks: list[str] = []
        if enabled is not None:
            body["projectBrief"]["enabled"] = enabled
            masks.append("project_brief.enabled")

        if cards is not None:
            body["projectBrief"]["cards"] = build_agent_brief_cards(cards, project_id=project_id)
            masks.append("project_brief.cards")

        if not masks:
            return {}

        mask_str = ",".join(masks)
        url = f"https://aisandbox-pa.googleapis.com/v1/projects/{project_id}/agentInfo?updateMask={mask_str}"
        data = await self._patch_json(url, body)
        if isinstance(data, dict):
            agent_info = cast("JsonObject", data).get("agentInfo")
            if isinstance(agent_info, dict):
                brief = cast("JsonObject", agent_info).get("projectBrief")
                if isinstance(brief, dict):
                    return cast("JsonObject", brief)
        return {}

    async def get_agent_info(self, project_id: str) -> ProjectBrief:
        """Read a project's Agent brief (instruction cards + master switch).

        There is NO ``GET /v1/projects/{id}/agentInfo`` (it 404s). The brief is
        read from the ``agentInfo`` block of ``flow.projectInitialData`` — the
        same tRPC query ``list_characters`` uses. Session-cookie auth; FREE — no
        reCAPTCHA, no credit. This is the read half of the ``gflow instructions``
        read-modify-write cycle (the server is the source of truth).
        """
        from gflow_cli.api.image import ProjectBrief

        trpc_input = json.dumps({"json": {"projectId": project_id}}, separators=(",", ":"))
        url = f"{routes.PROJECT_INITIAL_DATA_URL}?input={quote(trpc_input, safe='')}"
        data = await self._get_json(url, route_name="projectInitialData")
        inner = _unwrap_trpc(data)
        agent_info = inner.get("agentInfo")
        typed = cast("JsonObject", agent_info) if isinstance(agent_info, dict) else None
        return ProjectBrief.from_agent_info(typed)

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
        await validate_image_file(image_path)
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

        # Fail loud if an image request downloaded video content. The agentic
        # gflow_generate_image path has no explicit image-mode toggle — Flow's
        # conversational agent infers image-vs-video from the prompt and can
        # produce a video, whose tile await_images then scrapes as if it were an
        # image. Saving those bytes with an image suffix is a silent corruption
        # that only surfaces far downstream (e.g. Flow 400-rejects the file as an
        # i2v frame -> #125 text-only fallback). Catch it at the download.
        if looks_like_video(body):
            raise WireFormatError(
                detail=(
                    "image download returned video content (ISO-BMFF/WebM magic "
                    "bytes) — the agentic conversational agent likely produced a "
                    "video instead of an image. The agentic arm is only bound "
                    "when asked for by name or by -i instructions, so drop "
                    "--ui-mode agentic / GFLOW_CLI_UI_MODE=agentic / -i "
                    "(auto resolves to classic), or rephrase the prompt."
                ),
                route=route,
            )

        # Resolve the write target: local Path or cloud UPath.
        # Compute a relative key from out_path so cloud keys mirror the local
        # directory structure (images/YYYY-MM-DD/media_id_N.ext).
        storage_uri = self.settings.storage_uri
        if storage_uri:
            key = _storage_key_from_path(out_path, self.settings.output_dir)
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

    async def capability_listing(self, project_id: str) -> JsonObject:
        """`projectInitialData` for *project_id*, fetched once per client session.

        The model catalogue and the account's tier cannot change mid-run, so a
        chained extend must not re-fetch per segment: at N=15 that is 15 extra
        requests to a WAF-scored host for a constant.
        """
        cached = self._extend_listing_cache.get(project_id)
        if cached is None:
            cached = await self.fetch_project_listing(project_id)
            self._extend_listing_cache[project_id] = cached
        return cached

    async def extend_video(
        self,
        *,
        media_id: str,
        project_id: str,
        scene_id: str,
        position: int,
        prompt: str,
        aspect: str = "16:9",
        seed: int | None = None,
        recaptcha_action: str = "VIDEO_GENERATION",
    ) -> ExtendStarted:
        """Continue an existing clip by another 8 seconds. Costs credits.

        Direct-wire ``POST /v1/video:batchAsyncGenerateVideoExtendVideo``, verified
        live 2026-08-31. Unlike T2V/I2V — which ride ``ui_automation_video`` and
        passively capture Flow's own request — this composes the body itself, so
        it also owns its polling (see :meth:`poll_video_status`).

        Returns as soon as Flow schedules the job. Poll the returned ``media_id``
        for the result.
        """
        listing = await self.capability_listing(project_id)
        service_tier = video_extend.account_service_tier(listing)
        model_key, unit_cost = video_extend.resolve_extend_model(
            listing, service_tier=service_tier, aspect=aspect
        )
        # When Flow moves the extend family again — it has moved once already —
        # this single line is the diagnosis. The raw creditMapping table is NOT
        # logged; only the decision and the inputs that produced it.
        logger.info(
            "extend_model_resolved",
            model_key=model_key,
            service_tier=service_tier,
            unit_cost=unit_cost,
            candidate_count=len(video_extend.extract_video_models(listing)),
        )
        req = video_extend.ExtendVideoRequest(
            media_id=media_id,
            project_id=project_id,
            scene_id=scene_id,
            position=position,
            prompt=prompt,
            model_key=model_key,
            aspect=aspect,
            seed=seed,
        )
        token = await self._mint_recaptcha_token(recaptcha_action)
        body = req.to_wire(
            session_id=f";{int(time.time() * 1000)}",
            token=token,
            batch_id=str(uuid.uuid4()),
        )
        resp = await self._post_json(
            routes.EXTEND_VIDEO, body, route_name="batchAsyncGenerateVideoExtendVideo"
        )
        data = cast("JsonObject", resp) if isinstance(resp, dict) else {}
        workflows = data.get("workflows")
        workflow_id = ""
        if isinstance(workflows, list) and workflows and isinstance(workflows[0], dict):
            workflow_id = str(cast("JsonObject", workflows[0]).get("name") or "")
        return ExtendStarted(
            media_id=media_name_from_generate_response(data),
            workflow_id=workflow_id,
            model_key=model_key,
            unit_cost=unit_cost,
        )

    async def poll_video_status(
        self,
        media_id: str,
        *,
        project_id: str,
        initial_delay_s: float = 90.0,
        poll_interval: float = 10.0,
        timeout_s: float = 900.0,
    ) -> VideoStatus:
        """Poll ``batchCheckAsyncVideoGenerationStatus`` until *media_id* is terminal.

        The **only** outbound video poller in the codebase. Production T2V/I2V
        does not poll: ``ui_automation_video`` passively scans Flow's own captured
        status traffic, which works because the SPA is on-screen polling for its
        own generation. A direct-wire submit (the extend route) gives Flow's UI no
        reason to poll our media id, so that mechanism sees nothing and would sit
        until its deadline. Hence this.

        Shaped after :meth:`_poll_concat_until_done`: every poll is its own
        ``_post_json``, so the Page is checked back in before each sleep. Holding
        a checked-out Page across a sleep self-deadlocks at the default
        ``concurrency=1``.

        ``initial_delay_s`` exists because the cheapest extend model takes ~110s;
        polling immediately spends requests against a WAF-scored host on a job
        that cannot possibly be done. ``poll_interval`` is floored at 5s for the
        same reason — at 2s a 15-segment run would fire ~825 status requests
        instead of ~75.

        Returns the terminal :class:`VideoStatus` on success. Raises
        :class:`ContentPolicyError` when the failure is a safety rejection,
        :class:`FlowApiError` on any other terminal failure, and
        :class:`TransportTimeoutError` on deadline breach. A failed segment has
        still been billed, so it is never returned as a success-shaped object.
        """
        interval = max(poll_interval, _MIN_VIDEO_POLL_INTERVAL_S)
        deadline = time.monotonic() + timeout_s
        if initial_delay_s > 0:
            await asyncio.sleep(initial_delay_s)

        while True:
            resp = await self._post_json(
                routes.CHECK_VIDEO_STATUS,
                {"media": [{"name": media_id, "projectId": project_id}]},
                route_name="batchCheckAsyncVideoGenerationStatus",
            )
            status = parse_video_status(
                cast("JsonObject", resp) if isinstance(resp, dict) else {}, media_id=media_id
            )
            if status.succeeded:
                return status
            if status.is_terminal:
                reasons = ", ".join(status.failure_reasons) or "no reason given"
                detail = f"video generation failed: {reasons}"
                if status.error_message:
                    detail = f"{detail} ({status.error_message})"
                logger.warning(
                    "video.generation_failed",
                    media_id=media_id,
                    failure_reasons=list(status.failure_reasons),
                )
                if any(r in CONTENT_SAFETY_REASONS for r in status.failure_reasons):
                    raise ContentPolicyError(detail, route="batchCheckAsyncVideoGenerationStatus")
                raise FlowApiError(detail, route="batchCheckAsyncVideoGenerationStatus")
            if time.monotonic() >= deadline:
                raise TransportTimeoutError(
                    f"video {media_id} did not finish within {timeout_s:.0f}s "
                    f"(last status: {status.status})"
                )
            await asyncio.sleep(interval)

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
            key = _storage_key_from_path(out_path, self.settings.output_dir)
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
        """Incident/typed-error boundary for the upscale path (WireFormat/WAF
        failures here are exactly the discovery evidence the recorder exists
        to keep — e.g. the open unexplained HTTP 400 class)."""
        try:
            return await self._upsample_image_impl(
                media_id=media_id,
                project_id=project_id,
                target_resolution=target_resolution,
                out_path=out_path,
                recaptcha_action=recaptcha_action,
            )
        except Exception as e:
            await self._raise_with_incident(e, phase="image_upscale")

    async def _upsample_image_impl(
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
            key = _storage_key_from_path(out_path, self.settings.output_dir)
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
            # #673: this runs BEFORE the UI transport and therefore before every
            # migration guard it carries. On a moved account the pool page is the
            # flow.google.com project grid (the bootstrap goto's client-side
            # handoff), which loads no recaptcha/enterprise.js — so without this
            # check the mint died as a bare RecaptchaError (exit 1, "unexpected")
            # instead of the distinct exit 36. Same one-line guard the transport
            # uses at every point it is about to spend time.
            raise_if_migrated(page, at="mint_recaptcha_token")
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
        on_checkpoint: GenerationCheckpointObserver | None = None,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> list[GeneratedImage]:
        """Mint a token, call the transport once, and return all images.

        ``req.count`` controls how many images Flow generates (1–4). The UI
        transport clicks the matching x{N} tab so one submission produces N
        images; other transports may fan-out internally, but that is their
        concern. This method is the single place reCAPTCHA minting happens.

        ``on_checkpoint`` (Task C1) receives a ``submit_attempted`` observation
        immediately before the credit-spending transport call, then a
        ``remote_started`` observation carrying the generated media/workflow
        UUIDs — the first point the image handle is observable.
        """
        if self.transport is None:
            msg = "FlowApiClient.transport is None — call generate_image inside 'async with client'"
            raise RuntimeError(
                msg,
            )
        token = await self._mint_recaptcha_token(recaptcha_action)
        req_with_token = _dc_replace(req, recaptcha_token=token)
        if on_checkpoint is not None:
            on_checkpoint(GenerationCheckpoint(phase="submit_attempted"))
        # Kwarg passed only when set: keeps duck-typed fakes/transports that
        # predate #546 working, while the Protocol documents the seam.
        resolver_kw: dict[str, Any] = (
            {} if name_resolver is None else {"name_resolver": name_resolver}
        )
        images: list[GeneratedImage] = []
        async for retrying in post_with_retry():
            with retrying:
                images = await self.transport.generate_images(
                    project_id=project_id,
                    request=req_with_token,
                    **resolver_kw,
                )
        if not images:
            raise ContentPolicyError(
                detail="empty media[]",
                instance=_make_instance(),
                route=routes.batch_generate_images_url(project_id),
            )
        if on_checkpoint is not None:
            on_checkpoint(
                GenerationCheckpoint(
                    phase="remote_started",
                    media_ids=tuple(img.media_name for img in images),
                    workflow_ids=tuple(img.workflow_id for img in images),
                ),
            )
        return images

    async def _drive_image_generation(
        self,
        *,
        project_id: str,
        req: GenerateImageRequest,
        recaptcha_action: str,
        on_checkpoint: GenerationCheckpointObserver | None = None,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> GeneratedImage:
        """Single-image shortcut — delegates to ``_drive_images_generation`` with count=1.

        Forces ``count=1`` on the request before delegating so that callers
        constructing a :class:`GenerateImageRequest` with ``count>1`` and then
        invoking the single-image API still receive exactly one image (no
        silent discard).

        When the transport returns more images than the requested ``count=1``
        (typically because the generation-settings panel was not found and Flow
        used its own default count, billing the account for the extra
        generations), a ``client.generate_image_extra_returned`` warning is
        logged so the caller can investigate.
        """
        req_one: GenerateImageRequest = _dc_replace(req, count=1)
        images = await self._drive_images_generation(
            project_id=project_id,
            req=req_one,
            recaptcha_action=recaptcha_action,
            on_checkpoint=on_checkpoint,
            name_resolver=name_resolver,
        )
        if len(images) > 1:
            logger.warning(
                "client.generate_image_extra_returned",
                requested=1,
                returned=len(images),
                extra_media_ids=[img.media_name for img in images[1:]],
                hint=(
                    "Flow generated more images than requested — the "
                    "generation-settings panel selector may have missed and Flow "
                    "used its own default count. The extra image(s) were billed "
                    "to your account but not saved. Use `gflow image t2i -n 2` to "
                    "request and save multiple images explicitly."
                ),
            )
        return images[0]

    async def generate_image(
        self,
        *,
        project_id: str | None = None,
        req: GenerateImageRequest,
        recaptcha_action: str = "imageGeneration",
        on_checkpoint: GenerationCheckpointObserver | None = None,
        name_resolver: Callable[[str], str | None] | None = None,
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
                on_checkpoint=on_checkpoint,
                name_resolver=name_resolver,
            )
        except Exception as e:
            await self._raise_with_incident(e, phase="image_generation")

    async def generate_images_batch(
        self,
        *,
        project_id: str | None = None,
        req: GenerateImageRequest,
        count: int = 1,
        recaptcha_action: str = "imageGeneration",
        on_checkpoint: GenerationCheckpointObserver | None = None,
        name_resolver: Callable[[str], str | None] | None = None,
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
                on_checkpoint=on_checkpoint,
                name_resolver=name_resolver,
            )
        except Exception as e:
            await self._raise_with_incident(e, phase="image_batch")

    async def generate_video(
        self,
        *,
        req: GenerateVideoRequest,
        project_id: str | None = None,
        out_dir: Path | None = None,
        poll_timeout_s: float = 600.0,
        download: bool = True,
        on_started: VideoStartedCallback | None = None,
        on_checkpoint: GenerationCheckpointObserver | None = None,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> VideoResult:
        """Generate a video via the transport's ``generate_video`` method.

        Routes all video generation through a single client boundary so the
        data-layer recorder (Task 8) can hook in at one place.

        ``on_checkpoint`` (Task C1) receives a ``submit_attempted`` observation
        immediately before the credit-spending transport call, then a
        ``remote_started`` observation (via the transport's ``on_started`` hook
        — the first point the video handle is observable). ``operation_id`` is
        best-effort/optional: it is captured when the generate response
        includes ``operations[0].operation.name``, and is ``None`` when that
        field is absent (observed on veo-lite too, not only omni-flash, live
        2026-07-21). ``media_id`` is the canonical handle used for polling and
        download.

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

        wrapped_on_started = on_started
        if on_checkpoint is not None:
            observer: GenerationCheckpointObserver = on_checkpoint
            observer(GenerationCheckpoint(phase="submit_attempted"))

            def _relay(started: VideoStarted) -> Awaitable[None] | None:
                observer(
                    GenerationCheckpoint(
                        phase="remote_started",
                        operation_id=started.flow_operation_id,
                        media_ids=(started.media_id,),
                    ),
                )
                return on_started(started) if on_started is not None else None

            wrapped_on_started = _relay

        try:
            # Kwarg passed only when set — same #546 compat rule as
            # _drive_images_generation.
            resolver_kw: dict[str, Any] = (
                {} if name_resolver is None else {"name_resolver": name_resolver}
            )
            return await self.transport.generate_video(
                request=req,
                project_id=project_id,
                out_dir=out_dir,
                poll_timeout_s=poll_timeout_s,
                download=download,
                on_started=wrapped_on_started,
                **resolver_kw,
            )

        except Exception as e:
            await self._raise_with_incident(e, phase="video_generation")

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

    async def fetch_project_listing(self, project_id: str) -> JsonObject:
        """Fetch the raw ``flow.projectInitialData`` listing for *project_id*.

        Maps to ``GET .../trpc/flow.projectInitialData?input=…`` with
        ``toolName="PINHOLE"`` — the Flow editor's own initial-data call.
        Live-verified 2026-08-16: ~0.5s, session-cookie auth, no page
        navigation, FREE (no reCAPTCHA, no credit). Returns the tRPC envelope
        verbatim; parsing lives in :mod:`gflow_cli.services.catalog_sync`
        (#543).

        Issued through the context's APIRequestContext (not a checked-out
        Page) so multi-project sync sweeps never contend on the page pool.

        Raises:
            ValueError: *project_id* is not a canonical UUID — harvested ids
                must never reach URL construction unvalidated.
            RuntimeError: client not entered (no browser context).
        """
        if not is_media_uuid(project_id):
            msg = f"Invalid project_id: {project_id!r}"
            raise ValueError(msg)
        ctx = self._context
        if ctx is None:
            msg = "FlowApiClient not entered — use `async with`"
            raise RuntimeError(msg)
        trpc_input = json.dumps(
            {"json": {"projectId": project_id, "toolName": "PINHOLE"}},
            separators=(",", ":"),
        )
        url = f"{routes.PROJECT_INITIAL_DATA_URL}?input={quote(trpc_input, safe='')}"
        route = "projectInitialData"

        async def attempt() -> Any:
            return await ctx.request.get(url, timeout=30_000)

        resp = await self._run_with_retry(attempt, route=route)
        text = await resp.text()
        _raise_for_non_retryable(resp, text, route=route)
        # Single-channel rule (docs/SECURITY.md): id + size only — never
        # display names / captions.
        logger.info(
            "client.project_listing_fetched",
            project_id=project_id,
            byte_count=len(text.encode("utf-8")),
        )
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            raise WireFormatError(
                detail=f"non-JSON projectInitialData response: {text[:200]}",
                status=resp.status,
                instance=_make_instance(),
                route=route,
                discovery=_build_wire_format_discovery(resp, text, route),
            ) from e
        if not isinstance(parsed, dict):
            raise WireFormatError(
                detail=(f"projectInitialData returned non-object JSON ({type(parsed).__name__})"),
                status=resp.status,
                instance=_make_instance(),
                route=route,
            )
        return cast("JsonObject", parsed)

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
        locale: str | None = None,
        format_prompt: bool = False,
    ) -> tuple[str, str, AnyPath | None]:
        """Incident/typed-error boundary for the character generation path —
        the same wrap+capture the generate_* methods get (a UI-driven path can
        equally hit WAF/wire-format failures or a closed target)."""
        try:
            return await self._generate_character_image_impl(
                project_id=project_id,
                entity_id=entity_id,
                req=req,
                image_reference_index=image_reference_index,
                locale=locale,
                format_prompt=format_prompt,
            )
        except Exception as e:
            await self._raise_with_incident(e, phase="character_generation")

    async def _generate_character_image_impl(
        self,
        *,
        project_id: str,
        entity_id: str,
        req: CharacterImageRequest,
        image_reference_index: int = 0,
        locale: str | None = None,
        format_prompt: bool = False,
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
            format_prompt: Whether to click Flow's prompt-format button before
                submitting (rewrites the prompt into Flow's character shape).

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
            # #580: caller override wins; otherwise the ACCOUNT's own locale.
            # Never "en-US" — a wrong segment bounces the character route, which
            # is how #395 presented.
            locale=locale if locale is not None else self._account_locale,
            format_prompt=format_prompt,
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
            # This guard is CORRECT and must stay strict: a missing/mismatched
            # parentEntityId means Flow accepted the generation but filed it as
            # an ordinary project image instead of binding it to the character
            # entity. Verified live 2026-07-27 — runs that tripped this left the
            # entity "Untitled Character" with a null thumbnail, while a bound
            # run the day before carried its thumbnail_media_id. Relaxing this
            # would report a hollow character as success.
            missing = "omitted it entirely" if parent is None else f"set it to {parent!r}"
            raise WireFormatError(
                detail=(
                    f"Flow did not bind this generation to character entity "
                    f"{entity_id!r} — the returned workflow {missing}. The image "
                    "was generated but filed as a plain project image, so the "
                    "character has no portrait. This is a Flow-side binding "
                    "failure, not a malformed response: retry, and if it "
                    "persists check whether Flow's character editor still opens "
                    "for this account."
                ),
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


def _extract_provider_error_message(body_text: str) -> str | None:
    """Extract human-readable error message from provider JSON response body if present."""
    if not body_text or not body_text.strip():
        return None
    try:
        raw: object = json.loads(body_text)
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    data = cast("JsonObject", raw)

    error_obj: object = data.get("error")
    if isinstance(error_obj, dict):
        err_dict = cast("JsonObject", error_obj)
        json_obj: object = err_dict.get("json")
        if isinstance(json_obj, dict):
            j_dict = cast("JsonObject", json_obj)
            msg: object = j_dict.get("message")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
        msg: object = err_dict.get("message")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()
    elif isinstance(error_obj, str) and error_obj.strip():
        return error_obj.strip()

    msg: object = data.get("message")
    if isinstance(msg, str) and msg.strip():
        return msg.strip()

    detail: object = data.get("detail")
    if isinstance(detail, str) and detail.strip():
        return detail.strip()

    return None


def _raise_for_non_retryable(resp: Any, body_text: str, *, route: str) -> None:
    """Classify a response that survived the retry loop.

    Called on responses that EITHER succeeded (2xx) OR fell through with a
    non-retryable 4xx (e.g. 400, 401, 403, 404, 422). Anything outside those
    ranges should have been raised inside the retry loop and never reach
    here. Side-effect-only: raises on 4xx, returns silently on 2xx.

    * 401 → :class:`AuthExpiredError`
    * 403 → :class:`WafRejectionError` (reCAPTCHA/WAF wall, not auth expiry)
    * 400 with content-safety reason → :class:`ContentPolicyError`
    * other 4xx → :class:`WireFormatError` with discovery payload so
      ``grep error_class=WireFormatError`` reveals what was unexpected.
    """
    if resp.status < 400:
        return
    instance = _make_instance()
    provider_msg = _extract_provider_error_message(body_text)
    sanitized_msg = redact_sensitive_text(provider_msg) if provider_msg else None

    if resp.status == 401:
        detail = sanitized_msg if sanitized_msg else f"HTTP {resp.status}"
        raise AuthExpiredError(
            detail=detail,
            status=resp.status,
            instance=instance,
            route=route,
        )
    if resp.status == 429:
        detail = sanitized_msg if sanitized_msg else f"HTTP {resp.status}"
        raise RateLimitError(
            detail=detail,
            status=resp.status,
            instance=instance,
            route=route,
        )
    if resp.status == 403:
        detail = sanitized_msg if sanitized_msg else f"HTTP {resp.status}"
        raise WafRejectionError(
            detail=detail,
            status=resp.status,
            instance=instance,
            route=route,
        )
    if resp.status == 400:
        safety_reason = classify_content_safety(body_text)
        if safety_reason is not None:
            base_detail = (
                f"HTTP 400: Flow refused the request on content-safety grounds "
                f"(reason={safety_reason})"
            )
            detail = f"{base_detail}: {sanitized_msg}" if sanitized_msg else base_detail
            raise ContentPolicyError(
                detail=detail,
                status=resp.status,
                instance=instance,
                route=route,
                remediation_hint=(
                    f"Flow rejected the request due to content-safety policy "
                    f"({safety_reason}). If using multiple human-face reference "
                    f"images, reduce to one primary face and retry; or adjust the "
                    f"prompt to be less people-dense."
                ),
            )
    if 400 <= resp.status < 500:
        detail = sanitized_msg if sanitized_msg else f"HTTP {resp.status} on 4xx fallthrough"
        raise WireFormatError(
            detail=detail,
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
