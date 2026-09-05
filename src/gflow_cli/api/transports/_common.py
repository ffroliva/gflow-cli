"""Shared constants and helpers for image transport strategies.

Per spec § 5.4, all strategies share: the Flow URL, per-call timeout,
batch_id minting, and response interpretation. Extracted here to avoid
duplication across evaluate_fetch.py, bearer.py, and sapisidhash.py.

Council edit (Claude, 2026-05-11): _FLOW_URL was about to be triplicated
across B.1/B.2/B.3 and interpret_response() duplicated in B.2/B.3.
Extracted before strategies are written so the duplication never lands.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from urllib.parse import urlsplit

import structlog

from gflow_cli.api import routes
from gflow_cli.api.dto import GeneratedImage
from gflow_cli.data.redaction import redact_error_detail
from gflow_cli.errors import (
    AuthExpiredError,
    ContentPolicyError,
    FlowApiError,
    FlowHostMigratedError,
    NetworkError,
    RateLimitError,
    WafRejectionError,
    WireFormatError,
    classify_content_safety,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

log = structlog.get_logger(__name__)

FLOW_URL: str = "https://labs.google/fx/tools/flow?hl=en"
PER_CALL_TIMEOUT_S: int = 30
BEARER_DEFAULT_TTL_S: int = 3600
REFRESH_SAFETY_MARGIN_S: int = 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def mint_batch_id() -> str:
    """Return a fresh UUID4 string for use as a batch request identifier."""
    return str(uuid.uuid4())


# Flow's own origins. Google is migrating accounts off labs.google onto
# flow.google.com (issue #639). The handoff is a server-assigned per-account
# boolean that the labs.google app acts on client-side after a fully
# authenticated load (spike 2026-09-04-migrated-host-handoff-mechanism) -- it is
# a one-way rollout, not a flap (5/5 and 7/7 on a flagged account). Both hosts
# stay in this map because the fleet is mid-rollout.
_FLOW_HOSTS: dict[str, str] = {
    "labs.google": "labs",
    "flow.google.com": "migrated",
}


def flow_host_kind(url: object) -> str | None:
    """Classify a URL's origin as ``"labs"``, ``"migrated"``, or ``None``.

    Exact host match over a parsed https URL — never a substring test, which any
    foreign URL satisfies just by mentioning the host in its path or query.

    Total by construction: both callers read this straight off ``page.url`` on
    best-effort paths where a probe error must never displace the real failure,
    so anything unparseable — or not even a string — classifies as ``None``.
    """
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
        if parts.scheme != "https":
            return None
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    return _FLOW_HOSTS.get(host)


def migrated_route(url: object, flow_host: str, *, prefer_migrated: bool = False) -> str:
    """Which driver a page gets: ``"labs"``, ``"migrated"`` or ``"blocked"``.

    ``flow_host`` is ``Settings.flow_host``. ``flow.google.com`` forces the migrated
    composer; ``labs.google`` refuses it, so a moved account keeps exit 36
    (``blocked``). ``auto`` — the default — makes flow.google.com the default host
    for every request it can serve (``prefer_migrated``, decided by the caller from
    the request: t2v with a project today), on moved and unmoved accounts alike;
    anything else follows the served host, so an unmoved account keeps the labs
    driver for the features the new host has not been ported for. An unreadable
    URL with nothing to prefer routes to the labs driver, exactly as before.
    """
    if flow_host == "flow.google.com":
        return "migrated"
    kind = flow_host_kind(url)
    if kind == "migrated":
        return "blocked" if flow_host == "labs.google" else "migrated"
    return "migrated" if prefer_migrated and flow_host == "auto" else "labs"


def raise_if_migrated(page: object, *, at: str) -> None:
    """Abort now if this page is on the migrated ``flow.google.com`` origin (#639).

    The migrated frontend renders none of the controls gflow drives, so every probe
    after this point is doomed. Call it wherever the run is **about to spend time**,
    and never behind a wait of its own: ``page.url`` is a cached property and
    :func:`flow_host_kind` is one parse plus a dict lookup, so a call costs the host
    that still works nothing at all. Playwright assigns ``frame._url`` *before* it
    emits ``framenavigated`` for the hop and ``page.url`` is ``main_frame.url``, so
    the property is never behind the event: a listener cannot see the hop earlier
    than a read at the same instant can. The fix is *where* the URL is read, not
    *how* -- a first cut of #663 shipped a ``framenavigated`` watch for nothing.

    **Once, at entry, is not enough** — that was v0.66.1's defect.
    ``routes.project_editor_url`` only ever builds a ``labs.google`` URL, and the hop
    to the migrated origin is a *post-``goto``* redirect that neither settle path
    waits for (no locale → ``_settle_if_redirecting`` returns immediately; a known
    locale → :func:`await_url_settled` short-circuits because the labs URL already
    matches :data:`FLOW_LOCALISED_URL_RE`). An entry-only check therefore reads a
    pre-redirect URL and declines. Measured in the field on three consecutive runs:
    exit 36 arrived at ~57 s, through the slow selector-probe path, with the bail
    event absent from the timeline entirely.

    ``at`` names the call site in the log event, so a field timeline SHOWS where the
    host became knowable instead of leaving it to be inferred — which is exactly how
    a fast path that never fired survived a release.
    """
    url = getattr(page, "url", None)
    if flow_host_kind(url) != "migrated":
        return
    log.info("ui_driver.migrated_host_bail", at=at, url=url)
    raise FlowHostMigratedError(
        detail=(
            "Flow handed this session to flow.google.com — the origin Google is "
            "migrating accounts onto — whose frontend renders none of the controls "
            "gflow drives. This is not selector drift, and it is not transient: the "
            "handoff is a per-account setting the labs.google app applies on every "
            "load, so once your account is flagged, retrying will not land the old "
            "frontend."
        )
    )


PROJECT_URL_FRAGMENT = "/project/"


def extract_project_id(url: str) -> str | None:
    """Pull the project UUID out of a Flow editor URL, or None if absent.

    Handles both ``/project/<uuid>`` and ``/project/<uuid>?query`` forms.
    """
    if PROJECT_URL_FRAGMENT not in url:
        return None
    try:
        return url.split(PROJECT_URL_FRAGMENT)[1].split("?", maxsplit=1)[0]
    except (IndexError, ValueError):
        return None


def interpret_response(strategy_name: str, resp: Any) -> list[GeneratedImage]:
    """Map an httpx-like response (status_code + text) to images or raise.

    The strategy_name is included in every error message for traceability
    across S1/S2/S3 stack traces.

    Exception mapping:
      200 + valid non-empty media[]  → list[GeneratedImage]
      200 + empty media[]            → ContentPolicyError
      200 + missing/invalid media    → WireFormatError
      200 + non-JSON body            → WireFormatError (chained from JSONDecodeError)
      401                            → AuthExpiredError (caller handles refresh+retry)
      403                            → WafRejectionError (fingerprint/auth mismatch)
      429                            → RateLimitError
      >=500                          → NetworkError
      other                          → WireFormatError
    """
    status: int = resp.status_code
    text: str = resp.text or ""

    if status == 200:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            msg = f"{strategy_name}: non-JSON response body: {_redacted_snippet(text)}"
            raise WireFormatError(msg) from exc

        media = payload.get("media")
        if not isinstance(media, list):
            msg = (
                f"{strategy_name}: missing or invalid 'media' list in response: "
                f"{_redacted_snippet(text)}"
            )
            raise WireFormatError(
                msg,
            )
        if not media:
            msg = f"{strategy_name}: empty media[] — content policy rejection"
            raise ContentPolicyError(msg)
        return GeneratedImage.from_response_dict(payload)

    if status == 401:
        msg = f"{strategy_name}: HTTP 401 from Flow API — session expired"
        raise AuthExpiredError(msg)
    if status == 403:
        msg = (
            f"{strategy_name}: HTTP 403 — likely WAF/fingerprint mismatch: "
            f"{_redacted_snippet(text)}"
        )
        raise WafRejectionError(
            msg,
        )
    if status == 429:
        msg = f"{strategy_name}: HTTP 429 — rate limit hit: {_redacted_snippet(text)}"
        raise RateLimitError(msg)
    if status >= 500:
        msg = f"{strategy_name}: HTTP {status} server error: {_redacted_snippet(text)}"
        raise NetworkError(msg)

    msg = f"{strategy_name}: unexpected HTTP status {status}: {_redacted_snippet(text)}"
    raise WireFormatError(msg)


GENERATION_POLICY_HINT: str = (
    "Flow refused this generation (HTTP 400 on the generation route). This is "
    "almost always a content-policy rejection, not a malformed request — on this "
    "path Flow's own web app composes the request body. Most common causes, in "
    "order: (a) more than ONE face-bearing reference in the same request — reduce "
    "to a single --reference-entity OR a single portrait --ref and carry other "
    "people in prose; (b) an age-explicit person descriptor in the prompt ('a "
    "young woman in her early 20s', 'a man of about thirty') — use a relational "
    "or role noun instead ('his adult granddaughter', 'an estate agent'); (c) a "
    "real-person likeness or a frontal close-up face. Shortening the prompt does "
    "NOT help."
)


#: Bounded because this is paid on a path that often has nothing to wait for.
#: Measured: when Flow does redirect, it lands well inside this window; when it
#: does not (an `en` account is served the bare URL and never redirected), the
#: full timeout is dead time. 8 s made `ffroliva` setup take 11.2 s.
URL_SETTLE_TIMEOUT_MS: float = 4_000.0

#: Flow's canonical settled editor shape. Reuses the routes matcher rather than
#: restating it: `_resolve_account_locale` waits on THIS and then parses with
#: `routes.locale_segment_from_url`, so two independent copies could drift apart
#: and silently switch locale resolution off while both log lines looked healthy.
FLOW_LOCALISED_URL_RE = routes.LOCALE_SEGMENT_RE


async def await_url_settled(page: Any) -> str | None:
    """Wait for Flow's locale redirect to land; return the settled URL or ``None``.

    ``page.goto(wait_until="domcontentloaded")`` returns BEFORE the redirect —
    measured at 591-797 ms with the redirect arriving after. Any DOM work started
    in that window runs against a page about to be navigated away, which is how
    the #395 "character-route bounce" presents.

    **Waits for the destination SHAPE, not for stability.** An earlier version
    polled for two consecutive identical samples 200 ms apart and returned
    immediately — before the redirect had begun — reporting a URL that was merely
    not-yet-changed as "settled". The e2e gate caught it; unit tests could not,
    because the bug is purely about real-world timing.

    Two callers with independent purposes share this primitive: the client settles
    the bootstrap navigation to LEARN the account locale (preventing the redirect
    thereafter), the transport settles each editor navigation to TOLERATE a
    redirect it did not predict. Prevention and tolerance stay independent; only
    the act of observing "settled" is shared.

    Best-effort: never raises. Returns ``None`` on timeout (already-localised URLs
    match immediately, so a timeout means no locale form ever appeared).
    """
    # Short-circuit: if the URL is ALREADY the localised shape there is nothing to
    # wait for. Measured: without this, every project navigation on a
    # resolved-locale account burned the full timeout, because wait_for_url does
    # not reliably return early for an already-matching current URL.
    try:
        current = str(page.url)
    except Exception:  # noqa: BLE001
        current = ""
    if current and FLOW_LOCALISED_URL_RE.match(current):
        return current
    # #643: on the migrated origin the localised shape can NEVER appear — the path
    # is /project/<id>, with no /fx/<locale>/tools/flow segment. Waiting for it
    # burned the full timeout (measured 4018 ms on every migrated navigation) to
    # return the None we can return now.
    if flow_host_kind(current) == "migrated":
        return None

    try:
        await page.wait_for_url(FLOW_LOCALISED_URL_RE, timeout=URL_SETTLE_TIMEOUT_MS)
        return str(page.url)
    except Exception as exc:  # noqa: BLE001 — observation only, never break navigation
        # Distinguish "no localised URL appeared" (expected on accounts Flow does
        # not redirect) from "the wait itself is broken" (e.g. a renamed
        # Playwright method). Collapsing both into a silent None would let a
        # permanently broken settle read as healthy forever — the caller logs
        # `url_stable_after_goto` on a None return.
        log.info(
            "transport.url_settle_gave_up",
            exc_class=type(exc).__name__,
            timeout_ms=URL_SETTLE_TIMEOUT_MS,
        )
        return None


def generation_error(*, status: int, route: str, body: object) -> FlowApiError:
    """Classify a non-2xx status on a Flow **generation** route (issue #528).

    Returns the exception rather than raising it: the single-prompt paths do
    ``raise generation_error(...)``, while ``generate_images_batch`` needs the
    object to hand back inside a per-prompt ``BatchSubmissionResult``.

    Callers reach here only after 401/403/429 have been branched off, and only
    when no image/media survived — so this decides between "Flow refused the
    content" and "we genuinely do not understand this response".

    * 400 → :class:`ContentPolicyError`. Named ``reason`` when Flow sent one,
      but a bare 400 counts too: on the ``ui_automation`` path the generation
      request body is composed by Flow's own web app, so a 400 there cannot be
      our malformation. Before #528 these surfaced as ``WireFormatError``
      telling operators to "retry with a simpler prompt text", which never works.
    * any other status → :class:`WireFormatError`, unchanged.
    """
    if status == 400:
        reason = classify_content_safety(body)
        detail = f"HTTP 400 on {route or 'the generation route'}: Flow refused the request " + (
            f"on content-safety grounds (reason={reason})"
            if reason
            else "— no reason field returned (see remediation for the usual causes)"
        )
        return ContentPolicyError(
            detail=detail,
            status=400,
            route=route,
            remediation_hint=GENERATION_POLICY_HINT,
        )
    return WireFormatError(
        detail=f"generation route returned HTTP {status}",
        status=status,
        route=route,
    )


def _redacted_snippet(text: str) -> str:
    """Redact-then-truncate a response body for an exception message.

    Redaction runs BEFORE truncation (same rationale as
    ``client._build_wire_format_discovery``, audit gap #11): a token clipped at
    char 200 is still a partial secret, and since #341 these messages persist
    to the catalog DB as ``error_detail``. Called only at raise sites so the
    success path pays nothing.
    """
    return redact_error_detail(text)[:200]


# ---------------------------------------------------------------------------
# Model-picker primitives, shared by the image and video transports.
#
# Both arms drive a Radix `[role='menu']` of `[role='menuitem']` entries through
# the SAME trigger selector, and both were bitten by the same two hazards:
# `has-text` is a SUBSTRING match (so one label can be a prefix of another), and
# a raw `count()` includes mounted-but-hidden nodes. Keeping one copy means a
# fix on one arm cannot silently skip the other.
# ---------------------------------------------------------------------------

READ_MENU_ITEM_LABELS = r"""
() => Array.from(document.querySelectorAll("[role='menuitem']"))
    .map(e => (e.innerText || '').replace(/\s+/g, ' ').trim())
    .filter(Boolean)
"""


async def offered_menu_labels(page: Any) -> list[str]:
    """What the picker is rendering RIGHT NOW — read while the menu is open.

    Included in every failure message: a bare "model not found" is unactionable,
    whereas the actual list tells an operator immediately whether Flow renamed an
    entry, removed it, or added a near-duplicate. Best-effort — diagnostics must
    never mask the error they describe.
    """
    try:
        return list(await page.evaluate(READ_MENU_ITEM_LABELS))
    except Exception:  # noqa: BLE001
        return []


async def close_menu(page: Any) -> None:
    """Leave no stray open UI behind after a refusal.

    TWO Escapes, deliberately: the model menu and the generation-settings panel
    beneath it. Raising out of a model select skips the caller's own panel-close,
    so a single Escape leaves the panel open. In a batch that then toggles the
    panel SHUT on the next prompt's open attempt, turning one drifted selector
    into a whole-batch failure.
    """
    for _ in range(2):
        try:
            await page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001, S110 — cleanup only
            return


async def count_visible(page: Any, selector: str) -> tuple[int, Any]:
    """Number of VISIBLE matches for *selector*, plus the first of them.

    `count()` alone counts mounted-but-hidden nodes. Radix keeps menus mounted,
    so a stale or offscreen menu inflates the count and either forces a false
    AMBIGUOUS or resolves to a node that cannot be clicked.
    """
    loc = page.locator(selector)
    total = await loc.count()
    matches = 0
    first: Any = None
    for i in range(total):
        nth = loc.nth(i)
        if await nth.is_visible():
            matches += 1
            if first is None:
                first = nth
    return matches, first
