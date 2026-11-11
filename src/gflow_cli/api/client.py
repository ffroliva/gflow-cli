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
import json
import sys
import time
from typing import TYPE_CHECKING, Any, Self, cast
from urllib.parse import quote

import structlog
from playwright.async_api import BrowserContext, Page, async_playwright

from gflow_cli.api import routes
from gflow_cli.api._engine import (
    CONTEXT_TEARDOWN_TIMEOUT_S,
    DRIVER_STOP_TIMEOUT_S,
    active_engine,
    close_context_bounded,
    log_engine_selected,
    resolve_async_playwright,
    run_teardown_step,
)
from gflow_cli.api.character import Character, CharacterImageRequest, parse_characters
from gflow_cli.api.dto import (
    GeneratedImage,
)
from gflow_cli.api.transports import (
    STANDALONE_ONLY_TRANSPORTS,
    make_transport,
    resolve_transport_name,
)
from gflow_cli.api.transports.base import (
    FlowTransportStrategy,
    SupportsTransportSetup,
    TransportSetup,
)
from gflow_cli.browser_manager import (
    browser_proxy_kwargs,
    channel_for_profile,
    disable_features_args,
    profile_in_use_error,
    window_position_args,
)
from gflow_cli.config import BrowserEngine
from gflow_cli.diagnostics import IncidentRecorder, run_retention, validated_incidents_root
from gflow_cli.errors import (
    ConfigurationError,
    FlowApiError,  # re-exported via gflow_cli.api.__init__
    ProfileAccessError,
    ProfileLockedError,
    WireFormatError,
)
from gflow_cli.errors import (
    WafRejectionError as WafRejectionError,  # read here by scripts/dev/spike_patchright.py
)
from gflow_cli.paths import character_output_path
from gflow_cli.profile_lease import ProfileLease
from gflow_cli.storage import AnyPath
from gflow_cli.winsec import ensure_profile_hardened

if TYPE_CHECKING:
    pass
from gflow_cli.api._flow_bootstrap import AccountBootstrap
from gflow_cli.api._flow_media_edit import MAX_UPSAMPLE_B64_LEN as MAX_UPSAMPLE_B64_LEN
from gflow_cli.api._flow_media_edit import MediaEditSurface
from gflow_cli.api._flow_session import (
    _AISANDBOX_CONTENT_TYPE as _AISANDBOX_CONTENT_TYPE,  # pyright: ignore[reportPrivateUsage]
)
from gflow_cli.api._flow_session import (
    _APPLICATION_JSON,  # pyright: ignore[reportPrivateUsage]
    _LABS_ORIGIN,  # pyright: ignore[reportPrivateUsage]
    JsonObject,
)
from gflow_cli.api._flow_session import (
    _extract_provider_error_message as _extract_provider_error_message,  # pyright: ignore[reportPrivateUsage]
)
from gflow_cli.api._flow_session import (
    _is_target_closed as _is_target_closed,  # pyright: ignore[reportPrivateUsage]
)
from gflow_cli.api._flow_session import (
    _raise_for_non_retryable as _raise_for_non_retryable,  # pyright: ignore[reportPrivateUsage]
)
from gflow_cli.api._flow_session import (
    _storage_key_from_path as _storage_key_from_path,  # pyright: ignore[reportPrivateUsage]
)
from gflow_cli.api._flow_workspace import MAX_CONCAT_B64_LEN as MAX_CONCAT_B64_LEN
from gflow_cli.api._flow_workspace import MAX_IMAGE_BYTES as MAX_IMAGE_BYTES
from gflow_cli.api._flow_workspace import (
    _default_project_title as _default_project_title,  # pyright: ignore[reportPrivateUsage]
)
from gflow_cli.api._flow_workspace import (
    _is_supported_image_header as _is_supported_image_header,  # pyright: ignore[reportPrivateUsage]
)
from gflow_cli.api._flow_workspace import (
    _unwrap_trpc as _unwrap_trpc,  # pyright: ignore[reportPrivateUsage]
)
from gflow_cli.api._flow_workspace import validate_image_file as validate_image_file


def _is_profile_access_denied(exc: BaseException) -> bool:
    """True when Chrome's ProcessSingleton failed because the profile is read-only.

    Matches the Win32 code (ERROR_ACCESS_DENIED = 5), never the message text: Chrome
    embeds the OS's *localized* description (``拒绝访问。 (0x5)``, ``Acesso negado.
    (0x5)``), so only the code is the same on every Windows display language.
    """
    message = str(exc)
    return "ProcessSingleton" in message and ("(0x5)" in message or "Error code: 5" in message)


# Silence "imported but unused" — FlowApiError is re-exported from this module
# via ``gflow_cli.api.__init__`` for back-compat with Phase 3 call sites.
__all__ = ["FlowApiClient", "FlowApiError"]

logger = structlog.get_logger(__name__)
# issue #222: the NextAuth Flow session cookie + the URL used to seed it.
_FLOW_SESSION_COOKIE = "__Secure-next-auth.session-token"
_FLOW_COOKIE_URL = _LABS_ORIGIN


class FlowApiClient(MediaEditSurface, AccountBootstrap):
    """Async context-managed client for Flow's REST surface.

    Holds a Playwright persistent context and a single page (used as the HTTP
    transport via `page.request`). Auth = whatever cookies the profile dir
    has from a prior `gflow auth login`.
    """

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
                *window_position_args(self.settings.browser_window_position),
                *disable_features_args(),
            ],
            **browser_proxy_kwargs(self.settings.browser_proxy),
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
        proxy_spec: dict[str, str] = kwargs.get("proxy") or {}
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
            # #1036: scheme://host:port (credentials are refused by Settings).
            proxy=proxy_spec.get("server"),
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
                "Install Google Chrome in its default location, "
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

    async def _launch_persistent_context(self, kwargs: JsonObject) -> BrowserContext:
        """Launch the context and classify profile access versus contention failures."""
        assert self._pw is not None
        try:
            return await self._pw.chromium.launch_persistent_context(**kwargs)
        except Exception as exc:
            # Before _is_target_closed: an access denial can arrive as a
            # TargetClosedError too, and must not be reported as contention.
            if _is_profile_access_denied(exc):
                raise ProfileAccessError(
                    detail=(
                        f"Chrome cannot create runtime files under {self.profile_dir}; "
                        "the directory is not writable by this process"
                    ),
                ) from exc
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
            # Last: a generic Error that names the profile as in use (#984, the user's
            # own Chrome). A read-only profile and #293's TargetClosedError share the
            # header and are handled above, with their own hints and incident.
            if (in_use := profile_in_use_error(exc, self.profile_dir)) is not None:
                raise in_use from exc
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
                # #690: ".google" alone covered only the OLD host (labs.google).
                # A migrated account sits on flow.google.com, which ends in
                # ".com" and is not "google.com", so a perfectly healthy page
                # was reported unhealthy. The leading dot is load-bearing —
                # it is what keeps "evilgoogle.com" out.
                return hostname == "google.com" or hostname.endswith((".google", ".google.com"))
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

    async def _workflow_primary_media_id(self, project_id: str, workflow_id: str) -> str | None:
        """Flow's own ``metadata.primaryMediaId`` for *workflow_id*, or ``None``.

        Read from the project listing rather than inferred, because inferring it
        is what corrupted a body workflow: the entity exposes one thumbnail id,
        and reusing it across slots wrote the portrait's media onto the body.
        Best-effort — a listing fault leaves the caller to fall back.
        """
        try:
            listing = await self.fetch_project_listing(project_id)
        except Exception:  # noqa: BLE001 - diagnostic read, never fatal
            logger.warning("character.workflow_media_lookup_failed", exc_info=True)
            return None
        payload = _unwrap_trpc(listing)
        contents = payload.get("projectContents")
        if not isinstance(contents, dict):
            return None
        raw_workflows = cast("JsonObject", contents).get("workflows")
        if not isinstance(raw_workflows, list):
            return None
        workflows = cast("list[object]", raw_workflows)
        for entry in workflows:
            if not isinstance(entry, dict):
                continue
            record = cast("JsonObject", entry)
            if record.get("name") != workflow_id:
                continue
            metadata = record.get("metadata")
            if not isinstance(metadata, dict):
                continue
            media = cast("JsonObject", metadata).get("primaryMediaId")
            if isinstance(media, str) and media:
                return media
        return None

    async def _character_slot_from_entity(
        self,
        *,
        project_id: str,
        entity_id: str,
        image_reference_index: int,
    ) -> tuple[str, str, AnyPath | None] | None:
        """Read a generated slot's ids back off the entity, or ``None``.

        Returns ``(workflow_id, primary_media_id, local_path)`` in the shape
        :meth:`_generate_character_image_impl` returns, with ``local_path``
        always ``None`` — this path binds ids, it does not download a file.

        ``None`` means "the backend has nothing for this slot", which is the
        honest answer when a generation really did fail: the caller re-raises
        the original timeout rather than inventing a success. A fault in the
        read-back itself is swallowed for the same reason — it is a rescue
        attempt, and it must never displace the failure the user came to see.
        """
        try:
            character = await self.get_character(project_id, entity_id=entity_id)
        except Exception:  # noqa: BLE001 - a rescue must not mask the real error
            logger.warning(
                "character.result_read_back_failed",
                entity_id=entity_id,
                exc_info=True,
            )
            return None
        workflow_ids = tuple(character.workflow_ids or ())
        if len(workflow_ids) <= image_reference_index:
            logger.info(
                "character.result_read_back_empty",
                entity_id=entity_id,
                bound_workflows=len(workflow_ids),
                image_reference_index=image_reference_index,
            )
            return None
        workflow_id = workflow_ids[image_reference_index]
        # Each workflow carries its OWN primaryMediaId; the entity carries only the
        # thumbnail. Handing the thumbnail to every slot made the body slot claim
        # the face's media, and the saga's commit_workflow then PATCHed that id
        # onto the body workflow — corrupting it (observed live 2026-09-06).
        # Reading Flow's value instead makes that commit a no-op.
        media_id = await self._workflow_primary_media_id(project_id, workflow_id) or ""
        if not media_id and image_reference_index == 0:
            # Slot 0 IS the thumbnail by definition, so it is a safe last resort.
            media_id = character.thumbnail_media_id or ""

        # The ids are the character; the file is a convenience. Download it so
        # `--output` and `image_paths` behave the same on both hosts, but never
        # let a CDN hiccup cost the user a generation they already spent quota
        # on — a failed fetch degrades to `None`, it does not raise.
        #
        # `media.getMediaUrlRedirect` is a labs route, and migrated_composer
        # records it 404-ing for a migrated media id. That was measured on a
        # VIDEO id and does not generalise: probed live 2026-09-06 against this
        # path's own portrait id, it answered HTTP 200 with 696 767 bytes of
        # JFIF. Re-probe before assuming either way for a new media kind.
        local_path: AnyPath | None = None
        if media_id:
            try:
                local_path = await self.download(
                    media_id,
                    character_output_path(
                        self.settings.output_dir,
                        entity_id=entity_id,
                        slot=image_reference_index,
                    ),
                )
            except Exception:  # noqa: BLE001 - a missing file never loses the ids
                logger.warning(
                    "character.result_read_back_download_failed",
                    entity_id=entity_id,
                    slot=image_reference_index,
                    exc_info=True,
                )
        return workflow_id, media_id, local_path

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

        try:
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
        except TimeoutError:
            # The passive capture listens for `flowMedia:batchGenerateImages`,
            # which is the LABS wire. flow.google.com generates the portrait
            # over its own `batchexecute` (rpcid ogiZ0b) and never calls it, so
            # the listener times out on work that SUCCEEDED — measured live
            # 2026-09-06 on ci-probe, where the entity carried a workflow id and
            # a thumbnail media id the moment the timeout fired.
            #
            # Ask the backend instead of decoding an undocumented envelope. The
            # ids come off the entity itself, so the binding this method exists
            # to verify is true by construction — strictly stronger than the
            # labs path's self-reported parentEntityId.
            bound = await self._character_slot_from_entity(
                project_id=project_id,
                entity_id=entity_id,
                image_reference_index=image_reference_index,
            )
            if bound is None:
                raise
            logger.info(
                "character.result_read_back_from_entity",
                entity_id=entity_id,
                workflow_id=bound[0],
                image_reference_index=image_reference_index,
            )
            return bound
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
