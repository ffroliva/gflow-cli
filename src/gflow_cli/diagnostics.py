"""Private incident diagnostics (design: 2026-07-22-private-incident-diagnostics).

Sanitization primitives every automatic incident artifact is built from. The
session-scoped ``IncidentRecorder`` (journals, bundle filesystem, retention)
grows in this module in later plan tasks.

Contract (S01–S03, S29, S31): outputs contain only allowlisted primitives —
never raw URLs/queries/fragments, titles, prompts, tokens, arbitrary upstream
key names, unknown hosts/routes, or unsalted digests of low-entropy text.
Unknown hosts and routes reduce to the literal ``"other"``; raw payload
inspection stays on the explicit opt-in HAR escalation path.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections import deque
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlsplit

__all__ = [
    "CommandHasher",
    "ConsoleRecord",
    "ErrorBodySummary",
    "IncidentJournal",
    "JournalSnapshot",
    "ListenerBookkeeping",
    "NetworkRecord",
    "PageErrorRecord",
    "RequestTimingMap",
    "SanitizedUrl",
    "TextSummary",
    "TitleClass",
    "classify_title",
    "reduce_error_body",
    "sanitize_url",
    "text_summary",
]


class CommandHasher:
    """Per-command HMAC identity for values that need equality correlation.

    The key is random per instance, held only in memory, and never persisted —
    an unsalted digest of a low-entropy value (title, account, profile name)
    would be rainbow-reversible, so equality inside one command is the only
    supported use.
    """

    __slots__ = ("_key",)

    def __init__(self) -> None:
        self._key = secrets.token_bytes(32)

    def identity(self, value: str) -> str:
        digest = hmac.new(self._key, value.encode("utf-8", "surrogatepass"), hashlib.sha256)
        return digest.hexdigest()[:16]

    def __repr__(self) -> str:  # never expose key material
        return "CommandHasher()"


@dataclass(frozen=True, slots=True)
class SanitizedUrl:
    host_category: str
    route: str


@dataclass(frozen=True, slots=True)
class TitleClass:
    category: str
    length: int


@dataclass(frozen=True, slots=True)
class TextSummary:
    category: str
    length: int


@dataclass(frozen=True, slots=True)
class ErrorBodySummary:
    error_code: int | None
    status_enum: str | None
    has_error: bool
    has_message: bool
    has_status: bool
    has_details: bool
    unknown_key_count: int
    message_length: int
    content_safety_signature: bool


# Host allowlist — exact hostname, or suffix match for entries starting with a
# dot. Everything else is ``other`` and its raw host/path is never persisted.
_HOST_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("labs.google", "flow_app"),
    ("aisandbox-pa.googleapis.com", "aisandbox"),
    ("accounts.google.com", "google_auth"),
    ("storage.googleapis.com", "google_cdn"),
    ("flow-content.google", "google_cdn"),
    (".googleusercontent.com", "google_cdn"),
    (".gstatic.com", "google_static"),
    ("www.google.com", "google_web"),
)

# Known Flow endpoints (see api/routes.py) → stable canonical route. ``None``
# keeps the matched path itself (the pattern proves it is a safe literal).
# Colon-method segments would otherwise be mangled by the generic id reducer.
_ROUTE_PATTERNS: tuple[tuple[re.Pattern[str], str | None], ...] = (
    (
        re.compile(r"/v1/projects/[^/]+/flowMedia:batchGenerateImages"),
        "/v1/projects/{id}/flowMedia:batchGenerateImages",
    ),
    (re.compile(r"/v1/flow/projects/[^/]+/scenes"), "/v1/flow/projects/{id}/scenes"),
    (re.compile(r"/v1/flow/scene/[^/]+/workflows"), "/v1/flow/scene/{id}/workflows"),
    (re.compile(r"/v1/flow/scene/sceneWorkflows:update"), None),
    (re.compile(r"/v1/flowWorkflows/[^/]+"), "/v1/flowWorkflows/{id}"),
    (re.compile(r"/v1/flow/uploadImage"), None),
    (re.compile(r"/v1/flow/upsampleImage"), None),
    (re.compile(r"/v1/flow/entities"), None),
    (re.compile(r"/v1/flow:batchDeleteAssets"), None),
    (re.compile(r"/v1/video:batchAsyncGenerateVideoText"), None),
    (re.compile(r"/v1/video:batchCheckAsyncVideoGenerationStatus"), None),
    (re.compile(r"/v1:runVideoFxConcatenation"), None),
    (re.compile(r"/v1:runVideoFxCheckConcatenationStatus"), None),
    (re.compile(r"/fx/api/trpc/[A-Za-z]+\.[A-Za-z]+"), None),
    (re.compile(r"/fx/api/auth/session"), None),
    (
        re.compile(r"/fx(?:/[a-z]{2,3})?/tools/flow/project/[^/]+/character/[^/]+"),
        "/fx/tools/flow/project/{id}/character/{id}",
    ),
    (
        re.compile(r"/fx(?:/[a-z]{2,3})?/tools/flow/project/[^/]+"),
        "/fx/tools/flow/project/{id}",
    ),
    (re.compile(r"/fx(?:/[a-z]{2,3})?/tools/flow"), None),
)

_SAFE_SEGMENT_RE = re.compile(r"[A-Za-z0-9._-]{1,15}")
_MAX_ROUTE_SEGMENTS = 8


def _host_category(host: str) -> str:
    for entry, category in _HOST_CATEGORIES:
        if entry.startswith("."):
            if host.endswith(entry):
                return category
        elif host == entry:
            return category
    return "other"


def _is_identifier_like(segment: str) -> bool:
    """Conservative: over-reduction is privacy-safe, under-reduction is not."""
    if not _SAFE_SEGMENT_RE.fullmatch(segment):
        return True  # long, or carries chars outside the safe literal alphabet
    digits = sum(c.isdigit() for c in segment)
    return len(segment) >= 6 and digits / len(segment) >= 0.3


def sanitize_url(url: str, hasher: CommandHasher) -> SanitizedUrl:
    """Query/fragment-free host category + canonical route (design §5.2/§5.3)."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return SanitizedUrl(host_category="other", route="other")
    category = _host_category(host)
    if category == "other":
        return SanitizedUrl(host_category="other", route="other")
    path = parts.path or "/"
    for pattern, canonical in _ROUTE_PATTERNS:
        if pattern.fullmatch(path):
            return SanitizedUrl(host_category=category, route=canonical or path)
    segments = [s for s in path.split("/") if s][:_MAX_ROUTE_SEGMENTS]
    reduced = [f"id-{hasher.identity(s)[:8]}" if _is_identifier_like(s) else s for s in segments]
    return SanitizedUrl(host_category=category, route="/" + "/".join(reduced))


def classify_title(title: str) -> TitleClass:
    """Classified page title — the raw string is never persisted (§5.2).

    ``application error`` is Next.js's hardcoded error-page title, the same
    signature the ``FlowAppError`` raise site keys on.
    """
    lower = title.lower()
    if "application error" in lower:
        category = "flow_app_crash"
    elif "flow" in lower:
        category = "flow"
    else:
        category = "other"
    return TitleClass(category=category, length=len(title))


def text_summary(text: str, category: str) -> TextSummary:
    """The ONLY form in which console/page-error/message text is retained (§5.4)."""
    return TextSummary(category=category, length=len(text))


_KNOWN_TOP_KEYS = frozenset({"error"})
_KNOWN_ERROR_KEYS = frozenset({"code", "message", "status", "details"})
_STATUS_ENUM_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
# Mirrors api/client.py::_CONTENT_SAFETY_REASONS (client imports would cycle:
# FlowApiClient owns the IncidentRecorder, so diagnostics must stay leaf-level).
_CONTENT_SAFETY_REASONS = frozenset(
    {
        "PUBLIC_ERROR_UNSAFE_GENERATION",
        "PUBLIC_ERROR_UNSAFE_CONTENT",
        "PUBLIC_ERROR_UNSAFE_FACE",
        "PUBLIC_ERROR_UNSAFE_IDENTITY",
    }
)


def reduce_error_body(parsed: object) -> ErrorBodySummary:
    """Allowlisted discovery for an already-retained upstream error body (§5.3).

    Numeric code, enum-shaped status, known-key booleans, an unknown-key count,
    and the message *length* — never key names, message text, or raw values
    (S02/S29). Non-dict input degrades to the all-absent summary.
    """
    if not isinstance(parsed, dict):
        return ErrorBodySummary(
            error_code=None,
            status_enum=None,
            has_error=False,
            has_message=False,
            has_status=False,
            has_details=False,
            unknown_key_count=0,
            message_length=0,
            content_safety_signature=False,
        )
    top = cast("dict[str, object]", parsed)
    unknown = sum(1 for key in top if key not in _KNOWN_TOP_KEYS)
    error_raw = top.get("error")
    error_obj = cast("dict[str, object]", error_raw) if isinstance(error_raw, dict) else None
    error_code: int | None = None
    status_enum: str | None = None
    message_length = 0
    safety = False
    if error_obj is not None:
        unknown += sum(1 for key in error_obj if key not in _KNOWN_ERROR_KEYS)
        code = error_obj.get("code")
        if isinstance(code, int) and not isinstance(code, bool):
            error_code = code
        status = error_obj.get("status")
        if isinstance(status, str) and _STATUS_ENUM_RE.fullmatch(status):
            status_enum = status
        message = error_obj.get("message")
        if isinstance(message, str):
            message_length = len(message)
        details = error_obj.get("details")
        if isinstance(details, list):
            for item in cast("list[object]", details):
                if not isinstance(item, dict):
                    continue
                reason = cast("dict[str, object]", item).get("reason")
                if isinstance(reason, str) and reason in _CONTENT_SAFETY_REASONS:
                    safety = True
                    break
    return ErrorBodySummary(
        error_code=error_code,
        status_enum=status_enum,
        has_error=error_obj is not None,
        has_message=error_obj is not None and isinstance(error_obj.get("message"), str),
        has_status=error_obj is not None and "status" in error_obj,
        has_details=error_obj is not None and "details" in error_obj,
        unknown_key_count=unknown,
        message_length=message_length,
        content_safety_signature=safety,
    )


# --- bounded journals (design §5.3/§5.4, §6.2) -----------------------------
#
# Records are frozen primitive-only dataclasses: listener callbacks build them
# synchronously and never retain a Playwright Request/Response/ConsoleMessage.

_NETWORK_RING_CAP = 100
_CONSOLE_RING_CAP = 100
_PAGE_ERROR_RING_CAP = 50


@dataclass(frozen=True, slots=True)
class NetworkRecord:
    ts_monotonic: float
    ts_utc: str
    method: str
    host_category: str
    route: str
    resource_type: str
    status_or_failure: str
    duration_ms: float | None


@dataclass(frozen=True, slots=True)
class ConsoleRecord:
    ts_utc: str
    level: str
    category: str
    length: int
    source_category: str
    line: int | None
    column: int | None


@dataclass(frozen=True, slots=True)
class PageErrorRecord:
    ts_utc: str
    error_class: str
    length: int


@dataclass(frozen=True, slots=True)
class JournalSnapshot:
    network: tuple[NetworkRecord, ...]
    console: tuple[ConsoleRecord, ...]
    page_errors: tuple[PageErrorRecord, ...]


class IncidentJournal:
    """Fixed-size event rings. ``freeze()`` runs before context close; every
    ``add_*`` afterwards is a no-op so late callbacks cannot mutate evidence
    mid-finalization (S17)."""

    __slots__ = ("_console", "_frozen", "_network", "_page_errors")

    def __init__(self) -> None:
        self._network: deque[NetworkRecord] = deque(maxlen=_NETWORK_RING_CAP)
        self._console: deque[ConsoleRecord] = deque(maxlen=_CONSOLE_RING_CAP)
        self._page_errors: deque[PageErrorRecord] = deque(maxlen=_PAGE_ERROR_RING_CAP)
        self._frozen = False

    def freeze(self) -> None:
        self._frozen = True

    def add_network(self, rec: NetworkRecord) -> None:
        if not self._frozen:
            self._network.append(rec)

    def add_console(self, rec: ConsoleRecord) -> None:
        if not self._frozen:
            self._console.append(rec)

    def add_page_error(self, rec: PageErrorRecord) -> None:
        if not self._frozen:
            self._page_errors.append(rec)

    def snapshot(self) -> JournalSnapshot:
        return JournalSnapshot(
            network=tuple(self._network),
            console=tuple(self._console),
            page_errors=tuple(self._page_errors),
        )


class RequestTimingMap:
    """Primitive-only in-flight request timings (design §6.2).

    Keys are caller-derived strings, values are monotonic start seconds —
    never a retained Playwright object. Capped at 256 live entries with a
    ten-minute expiry; when correlation is unsafe (expired, overflow, unknown)
    the duration is simply omitted (S18).
    """

    __slots__ = ("_entries",)

    _MAX_ENTRIES = 256
    _EXPIRY_S = 600.0

    def __init__(self) -> None:
        self._entries: dict[str, float] = {}

    def start(self, key: str, monotonic_ts: float) -> None:
        self._purge(monotonic_ts)
        if len(self._entries) >= self._MAX_ENTRIES:
            return  # drop the newcomer — never evict a live in-flight entry
        self._entries[key] = monotonic_ts

    def finish(self, key: str, monotonic_ts: float) -> float | None:
        started = self._entries.pop(key, None)
        if started is None or monotonic_ts - started > self._EXPIRY_S:
            return None
        return (monotonic_ts - started) * 1000.0

    def size(self) -> int:
        return len(self._entries)

    def _purge(self, now: float) -> None:
        expired = [key for key, ts in self._entries.items() if now - ts > self._EXPIRY_S]
        for key in expired:
            del self._entries[key]


class ListenerBookkeeping:
    """Attach-at-most-once / detach-exactly-once accounting per target id (S16)."""

    __slots__ = ("_attached",)

    def __init__(self) -> None:
        self._attached: set[int] = set()

    def mark_attached(self, target_id: int) -> bool:
        if target_id in self._attached:
            return False
        self._attached.add(target_id)
        return True

    def mark_detached(self, target_id: int) -> bool:
        if target_id not in self._attached:
            return False
        self._attached.discard(target_id)
        return True
