"""Session state and primitives every surface uses: page pool, recorder hooks, incidents, access
token, JSON verbs, retry.

Split out of ``client.py`` (#1249); ``FlowApiClient`` composes it. Method
bodies are unchanged.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, NoReturn, cast

import structlog
from playwright.async_api import BrowserContext, Page, Playwright

from gflow_cli.api._engine import retryable_engine_errors
from gflow_cli.api._retry import MAX_ATTEMPTS, parse_retry_after, post_with_retry
from gflow_cli.api.transports.base import (
    FlowTransportStrategy,
)
from gflow_cli.config import Settings
from gflow_cli.diagnostics import IncidentRecorder
from gflow_cli.errors import (
    AisandboxAuthError,
    AuthExpiredError,
    AuthMissingError,
    BrowserSessionClosedError,
    ContentPolicyError,
    NetworkError,
    RateLimitError,
    WafRejectionError,
    WireFormatError,
    classify_content_safety,
)
from gflow_cli.profile_lease import ProfileLease
from gflow_cli.redaction import redact_sensitive_text
from gflow_cli.storage import AnyPath, storage_path

if TYPE_CHECKING:
    from pathlib import Path


logger = structlog.get_logger(__name__)

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


#: Flow's own words when a labs tRPC route is retired. Measured 2026-09-20 on
#: ``projectInitialData`` (profile denon82, healthy session, 57 context cookies):
#: HTTP 404, body ``{"error":{"json":{"message":"Flow RPCs have been deprecated and
#: disabled. Flow has migrated to https://flow.google.com.","code":-32004,...``
#: Keyed on THAT MESSAGE — never on which host an account is served, and never on a
#: route allowlist, so a route we have not observed yet is covered the day Flow
#: retires it (#875, AGENTS.md "Host-Membership Discipline").
_LABS_RPC_RETIRED_MARKER = "flow rpcs have been deprecated"


def _labs_rpc_retired_hint(body_text: str, *, route: str) -> str | None:
    """Remediation for a labs tRPC route Flow has retired, or ``None``.

    ``WireFormatError``'s class default tells the user to check the payload, retry
    with a simpler prompt and file a bug. On this response all three are wrong in
    the same way #803 and #789 were wrong — gflow asserting something nothing
    measured. The route is gone, ``gflow character list`` has no prompt to simplify,
    and Flow documents the condition in the very body we are classifying.
    """
    if _LABS_RPC_RETIRED_MARKER not in body_text.lower():
        return None
    # Deliberately suggests no setting. GFLOW_CLI_FLOW_HOST=flow.google.com was
    # MEASURED not to help (2026-09-20, denon82, `character list`): the same 404
    # comes back, because this read has no migrated arm to route to. Naming it
    # here would repeat the mistake this hint exists to fix — advice nothing
    # measured, on the field a user checks to find out what to do next.
    return (
        f"Flow has retired this labs route ({route}) — the request payload is fine, "
        f"there is no prompt to simplify, and there is no bug to file. Flow has moved "
        f"to flow.google.com, and gflow has not ported this read to that frontend yet, "
        f"so the command has no working path today. Track "
        f"https://github.com/ffroliva/gflow-cli/issues/639 for the port."
    )


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
            # None keeps the class default for every other 4xx (#875).
            remediation_hint=_labs_rpc_retired_hint(body_text, route=route),
            discovery=_build_wire_format_discovery(resp, body_text, route),
        )


def _billed_not_resent(cause: str, route: str, status: int | None = None) -> NetworkError:
    """A billed POST failed after it may have reached Flow: reconcile, never resend."""
    return NetworkError(
        detail=f"{cause}; not resent",
        status=status,
        route=route,
        next_action="reconcile",
        remediation_hint=(
            "Flow may have accepted this billed request before the error, "
            "so gflow did not resend it. Check the project in Flow for the "
            "new clip or image before running the command again."
        ),
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
            recaptcha_dict["token"] = "<redacted>"  # noqa: S105 - not a password: a redaction marker or a JS snippet name


class FlowSession:
    """Session state and primitives every surface uses: page pool, recorder hooks, incidents,
    access token, JSON verbs, retry.
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
        # Per-worker Page pool. All Pages live inside ONE
        # persistent BrowserContext and therefore SHARE cookies + auth state
        # at the Context level — this is intentional and matches Playwright's
        # per-worker-Page recommendation. If per-user isolation is ever
        # needed, separate Contexts (one per user) would be required.
        self._pages: list[Page] = []
        self._page_queue: asyncio.Queue[Page] | None = None
        # The first pool Page. Code across the surfaces still reads ``self._page``
        # directly rather than ``_checkout_page()`` / ``_checkin_page()``.
        self._page: Page | None = None
        # issue #222: Flow cookies read from the profile BEFORE the headed
        # generation context launches, used to seed that context if it cannot
        # decrypt the on-disk cookie store (macOS Keychain vs basic-store
        # mismatch). Populated by _preread_flow_session_cookies().
        self._preread_flow_cookies: dict[str, str] = {}
        # projectInitialData per project — see capability_listing.
        self._extend_listing_cache: dict[str, JsonObject] = {}

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
        # A billed POST's reconcile error names the closed target in its detail; re-wrapping
        # it as the retryable close would invite a re-bill (#1274, CodeRabbit on #1278).
        if _is_target_closed(e) and getattr(e, "next_action", None) != "reconcile":
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
            # #803: a non-JSON body is an interstitial, a consent page or a
            # redirect — not an expired credential. Re-authenticating cannot
            # change the shape of this reply, so the class default would send
            # the user somewhere that costs them their session for nothing.
            raise AisandboxAuthError(
                detail="non-JSON /auth/session response",
                status=resp.status,
                instance=_make_instance(),
                route="auth/session",
                remediation_hint=(
                    "Flow's session endpoint returned a non-JSON response — an "
                    "interstitial or a redirect, not an expired cookie. Re-run with "
                    "GFLOW_CLI_LOG_LEVEL=DEBUG and check which host Flow served this "
                    "account from; re-authenticating will not change a non-JSON "
                    "reply. See issue #803."
                ),
            ) from exc
        data = cast("JsonObject", parsed) if isinstance(parsed, dict) else {}
        token = data.get("access_token")
        if not token:
            # #803/#795: labs answering 200 with no token is the migrated-account
            # shape, not an expired session. aisandbox-pa has not been contacted
            # yet, so nothing has rejected anything — and SAPISID is what made
            # this probe answer at all. (`gflow credits` no longer uses this token:
            # it reads flow.google.com's `nzlxg` reply, #795.)
            raise AisandboxAuthError(
                detail="no access_token in /fx/api/auth/session",
                status=resp.status,
                instance=_make_instance(),
                route="auth/session",
                remediation_hint=(
                    "Flow's labs.google session carries no API token. Since 2026-10-22 "
                    "its session URL answers 410 for every account, including those Flow "
                    "serves from flow.google.com (#1023), so this is expected and "
                    "re-authenticating will not help — it can also roll this "
                    "profile's Chrome-strategy marker back and start the #791 "
                    "re-login loop. Generation still works; the routes that need this "
                    "token do not. See issue #803."
                ),
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
        billed: bool = False,
    ) -> Any:
        """Run ``attempt`` under the retry policy; on an aisandbox 401, re-fetch
        the access token once and retry, then raise ``AisandboxAuthError``.

        ``billed`` (#1260): one attempt on a 5xx/429. The 401 resend stays, because a
        401 is refused at auth, before anything is billed.

        Shared by ``_post_json`` and ``_patch_json`` so the auth-refresh policy
        lives in one place.
        """
        resp = await self._run_with_retry(attempt, route=route, billed=billed)
        if is_aisandbox and resp.status == 401:
            # Token may have expired mid-session — re-fetch once and retry.
            self._access_token = None
            await self._ensure_access_token()
            resp = await self._run_with_retry(attempt, route=route, billed=billed)
            if resp.status == 401:
                # #803: the token was minted and then refused, so the Google
                # sign-in is the one thing here that demonstrably works. Name
                # the route — it is what tells the user which capability is
                # refused rather than implying the whole session is broken.
                raise AisandboxAuthError(
                    detail="aisandbox-pa returned 401 after token refresh",
                    status=401,
                    instance=_make_instance(),
                    route=route,
                    remediation_hint=(
                        "Flow's labs.google session issued an API token and "
                        f"aisandbox-pa rejected it on route {route}. Your Google "
                        "sign-in is not the problem — minting that token is what "
                        "proves it works. Most commonly Flow now serves this account "
                        "from flow.google.com, where these read routes have not "
                        "answered for us. See issue #803."
                    ),
                )
        return resp

    async def _post_json(
        self,
        url: str,
        body: JsonObject,
        *,
        content_type: str = _AISANDBOX_CONTENT_TYPE,
        route_name: str | None = None,
        billed: bool = False,
    ) -> Any:
        """POST a JSON body with retry + typed-error classification.

        ``billed=True`` for a POST that spends credits with a single-use reCAPTCHA
        token in its body (#1260): it is sent once. A 5xx, or a timeout / reset /
        closed browser on the POST itself (#1274), can arrive after Flow took the
        job, so it raises ``NetworkError`` with ``next_action="reconcile"``. An
        engine error before the POST (e.g. the token fetch) surfaces unchanged.

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
                try:
                    return await page.request.post(url, data=body_str, headers=headers)
                except retryable_engine_errors() as exc:
                    if not billed:
                        raise
                    # A timeout or reset on the POST can come after Flow took the job
                    # (#1274). First line only: keeps Playwright's call log out.
                    cause = str(exc).partition("\n")[0]
                    raise _billed_not_resent(
                        f"{type(exc).__name__} on a billed request ({cause})", route
                    ) from exc
            finally:
                self._checkin_page(page)

        resp = await self._run_with_aisandbox_retry(
            attempt, route=route, is_aisandbox=is_aisandbox, billed=billed
        )
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

    async def _run_with_retry(self, attempt: Any, *, route: str, billed: bool = False) -> Any:
        """Execute ``attempt()`` under the tenacity retry policy.

        Inside the retry block we ONLY classify retryable failures
        (429 → RateLimitError, 5xx → NetworkError) so tenacity can act.
        Non-retryable 4xx fallthrough is classified outside this helper via
        ``_raise_for_non_retryable`` to keep retry-vs-classify concerns
        separate.

        ``billed=True`` (#1260) makes one attempt, and a 5xx raises the reconcile
        ``NetworkError``. Engine errors (timeout, reset) pass through unchanged here:
        only ``_post_json`` knows whether the POST itself was sent (#1274).
        """
        response: Any = None
        async for retrying in post_with_retry(max_attempts=1 if billed else MAX_ATTEMPTS):
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
                    if billed:
                        # Flow may have taken the job before failing; resending the
                        # same single-use token would re-bill or be refused (#1260).
                        raise _billed_not_resent(
                            f"HTTP {response.status} on a billed request", route, response.status
                        )
                    raise NetworkError(
                        detail=f"HTTP {response.status}",
                        status=response.status,
                        route=route,
                    )
        assert response is not None  # tenacity reraise=True guarantees this
        return response

    def _write_target(self, out_path: Path) -> AnyPath:
        """``out_path``, or its key under the configured cloud ``storage_uri``."""
        storage_uri = self.settings.storage_uri
        if storage_uri:
            key = _storage_key_from_path(out_path, self.settings.output_dir)
            return storage_path(storage_uri, self.settings.output_dir, key)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        return out_path
