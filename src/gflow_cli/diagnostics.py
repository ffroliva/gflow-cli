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

import asyncio
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import stat as stat_module
import sys
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlsplit

import structlog

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

if TYPE_CHECKING:
    from gflow_cli.config import Settings

_log = structlog.get_logger(__name__)

__all__ = [
    "BundleDir",
    "CommandHasher",
    "IncidentRecorder",
    "IncidentRef",
    "build_manifest",
    "emit_capture_completed",
    "emit_capture_failed",
    "emit_capture_started",
    "emit_capture_suppressed",
    "emit_owner_evidence_read",
    "emit_retention_pruned",
    "resolve_correlation_id",
    "ConsoleRecord",
    "ErrorBodySummary",
    "IncidentJournal",
    "JournalSnapshot",
    "ListenerBookkeeping",
    "NetworkRecord",
    "PageErrorRecord",
    "RequestTimingMap",
    "STRUCTURAL_DOM_JS",
    "SanitizedUrl",
    "TextSummary",
    "TitleClass",
    "classify_title",
    "reduce_error_body",
    "run_retention",
    "sanitize_url",
    "text_summary",
    "validate_structural_dom",
    "validated_incidents_root",
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


# --- bundle filesystem (design §5, §7, §8) ---------------------------------

_PENDING_MARKER_SCHEMA = "gflow-incident-pending-v1"
_PENDING_MARKER_CAP_BYTES = 4096
_CREATE_RETRIES = 3
_SAFE_INCIDENT_ID_RE = re.compile(r"[\w\-]{1,80}")
_SAFE_ARTIFACT_RE = re.compile(r"[\w\-.]{1,64}(?:/[\w\-.]{1,64})?")


def _is_reparse_point(path: Path) -> bool:
    """Symlink on every platform; junction / any reparse point on Windows."""
    if path.is_symlink():
        return True
    if sys.platform == "win32":
        try:
            attrs = os.lstat(path).st_file_attributes
        except OSError:
            return False
        return bool(attrs & stat_module.FILE_ATTRIBUTE_REPARSE_POINT)
    return False


def _kernel_lock_nonblocking(fd: int) -> None:
    """Advisory lock on byte 0; raises OSError on contention.

    Deliberately mirrors ``profile_lease._lock_nonblocking`` instead of
    importing it: profile_lease gains a ``diagnostics.CommandHasher`` import
    for owner-evidence identities, so diagnostics must stay leaf-level.
    """
    if sys.platform == "win32":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _kernel_unlock(fd: int) -> None:
    if sys.platform == "win32":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _write_new_file(path: Path, payload: bytes) -> None:
    """Exclusive creation with restrictive mode from the first byte (S28)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)


def validated_incidents_root(home: Path) -> Path | None:
    """``<home>/incidents``, created ``0o700`` — or ``None`` when the root (or
    home) is a symlink/junction/reparse point or escapes home (S27). Callers
    treat ``None`` as capture-unavailable; nothing is ever written through a
    link."""
    try:
        home_resolved = home.resolve()
        root = home / "incidents"
        if _is_reparse_point(home) or _is_reparse_point(root):
            return None
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if _is_reparse_point(root):  # re-check post-mkdir (pre-existing link)
            return None
        if not root.resolve().is_relative_to(home_resolved):
            return None
    except OSError:
        return None
    return root


class BundleDir:
    """One incident bundle directory: exclusive creation, a locked ``.pending``
    marker while staged, and atomic manifest-last finalization (§5, §8)."""

    __slots__ = ("_marker_fd", "path")

    def __init__(self, path: Path, marker_fd: int) -> None:
        self.path = path
        self._marker_fd = marker_fd

    @classmethod
    def create_exclusive(
        cls, root: Path, incident_id: str, *, now: datetime | None = None
    ) -> BundleDir:
        """``<root>/<YYYY-MM-DD>/<UTCstamp>-<incident_id>-<rand>/`` with a
        collision-resistant random component and ``os.mkdir`` exclusivity —
        a clock rollback or duplicate id cannot overwrite a bundle (S40)."""
        if not _SAFE_INCIDENT_ID_RE.fullmatch(incident_id):
            msg = f"unsafe incident id: {incident_id!r}"
            raise ValueError(msg)
        stamp_dt = now or datetime.now(UTC)
        day_dir = root / stamp_dt.date().isoformat()
        day_dir.mkdir(mode=0o700, exist_ok=True)
        if _is_reparse_point(day_dir):
            msg = f"incident day directory is a reparse point: {day_dir}"
            raise OSError(msg)
        stamp = stamp_dt.strftime("%Y%m%dT%H%M%SZ")
        last_error: OSError | None = None
        for _ in range(_CREATE_RETRIES):
            candidate = day_dir / f"{stamp}-{incident_id}-{secrets.token_hex(3)}"
            if not candidate.resolve().parent.is_relative_to(root.resolve()):
                msg = f"incident bundle path escapes root: {candidate}"
                raise OSError(msg)
            try:
                os.mkdir(candidate, mode=0o700)
            except FileExistsError as exc:
                last_error = exc
                continue
            marker_fd = cls._create_locked_marker(candidate)
            return cls(candidate, marker_fd)
        msg = f"could not create a unique incident directory under {day_dir}"
        raise OSError(msg) from last_error

    @staticmethod
    def _create_locked_marker(bundle_path: Path) -> int:
        marker = bundle_path / ".pending"
        payload = json.dumps(
            {
                "schema": _PENDING_MARKER_SCHEMA,
                "pid": os.getpid(),
                "created_utc": datetime.now(UTC).isoformat(),
            }
        ).encode("utf-8")[:_PENDING_MARKER_CAP_BYTES]
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        fd = os.open(marker, flags, 0o600)
        try:
            # Byte 0 carries a sentinel so the locked region exists on Windows;
            # the JSON body starts at offset 1 (same layout rule as the profile
            # lease — never write metadata into the locked byte).
            os.write(fd, b"\0")
            _kernel_lock_nonblocking(fd)
            os.lseek(fd, 1, os.SEEK_SET)
            os.write(fd, payload)
        except Exception:
            os.close(fd)
            raise
        return fd

    def write_artifact(self, name: str, payload: bytes) -> None:
        """Write ``<bundle>/<name>`` (one optional subdir level, e.g.
        ``sensitive/screenshot.png``) exclusively at mode 0600."""
        if not _SAFE_ARTIFACT_RE.fullmatch(name) or ".." in name:
            msg = f"unsafe artifact name: {name!r}"
            raise ValueError(msg)
        target = self.path / name
        if not target.resolve().is_relative_to(self.path.resolve()):
            msg = f"artifact path escapes bundle: {name!r}"
            raise ValueError(msg)
        if target.parent != self.path:
            target.parent.mkdir(mode=0o700, exist_ok=True)
        _write_new_file(target, payload)

    def finalize(self, manifest: dict[str, object]) -> None:
        """Write ``manifest.json`` last and atomically; then release the pending
        lock and remove the marker. The manifest's presence is the marker that
        a directory is a complete gflow-created bundle (§5)."""
        payload = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
        tmp = self.path / "manifest.json.tmp"
        _write_new_file(tmp, payload)
        os.replace(tmp, self.path / "manifest.json")
        fd, self._marker_fd = self._marker_fd, -1
        if fd >= 0:
            try:
                _kernel_unlock(fd)
            finally:
                os.close(fd)
            (self.path / ".pending").unlink(missing_ok=True)


# --- correlation, stable events, manifest (design §5.1, §9) ----------------


def resolve_correlation_id() -> str:
    """The command's bound correlation id, or a fresh short id when absent.

    Callers bind the result ONCE per command/task (S42): every event,
    directory name, and manifest in that command must reuse the same value.
    """
    bound = structlog.contextvars.get_contextvars().get("correlation_id", "")
    return str(bound) if bound else uuid.uuid4().hex[:12]


# Fixed-field constructors (S41): each event exposes exactly these parameters —
# no **kwargs — so raw URLs, paths, owner metadata, exception text, prompts, or
# browser objects cannot ride along as arbitrary logging fields.


def emit_capture_started(incident_id: str) -> None:
    _log.info("incident.capture_started", incident_id=incident_id)


def emit_capture_completed(
    incident_id: str, *, status: str, artifact_kinds: list[str], duration_ms: int
) -> None:
    _log.info(
        "incident.capture_completed",
        incident_id=incident_id,
        status=status,
        artifact_kinds=artifact_kinds,
        duration_ms=duration_ms,
    )


def emit_capture_failed(incident_id: str, *, exc_class: str, artifact_kind: str) -> None:
    """S25: the exception CLASS only — never raw capture-exception text."""
    _log.warning(
        "incident.capture_failed",
        incident_id=incident_id,
        exc_class=exc_class,
        artifact_kind=artifact_kind,
    )


def emit_capture_suppressed(incident_id: str, *, count: int) -> None:
    _log.info("incident.capture_suppressed", incident_id=incident_id, count=count)


def emit_retention_pruned(*, complete_count: int, pending_count: int, bytes_freed: int) -> None:
    _log.info(
        "incident.retention_pruned",
        complete_count=complete_count,
        pending_count=pending_count,
        bytes_freed=bytes_freed,
    )


def emit_owner_evidence_read(*, valid: bool) -> None:
    """Valid/invalid only — owner values never enter structured logs (§6.4)."""
    _log.info("profile_lease.owner_evidence_read", valid=valid)


_MANIFEST_NOTICE = (
    "Private local diagnostics. Never uploaded or auto-shared by gflow-cli. "
    "Review before sharing; sensitive artifacts may contain account or media data."
)


def build_manifest(
    *,
    incident_id: str,
    settings: Settings,
    created_utc: str,
    finalized_utc: str | None,
    cli_version: str,
    exc_class: str,
    problem_type: str,
    exit_code: int,
    retryable: bool,
    route: str,
    phase: str,
    command: str | None,
    transport: str | None,
    artifacts: dict[str, str],
    artifact_status: dict[str, str],
    har_state: str,
    suppressed_count: int,
    ui_mode: str | None = None,
    model_alias: str | None = None,
    aspect: str | None = None,
    count: int | None = None,
    locale: str | None = None,
    has_profile: bool | None = None,
    has_project: bool | None = None,
    media_input_count: int | None = None,
) -> dict[str, object]:
    """Assemble ``manifest.json`` from an explicit allowlist (§5.1).

    ``settings`` is consulted ONLY for named boolean/enum scalars — never
    ``Settings.model_dump()``, which would serialize API keys, daemon tokens,
    storage URIs, profile paths, and HAR paths (S01).
    """
    return {
        "schema": "gflow-incident-v1",
        "incident_id": incident_id,
        "created_utc": created_utc,
        "finalized_utc": finalized_utc,
        "cli_version": cli_version,
        "python_version": platform.python_version(),
        "os_family": platform.system(),
        "environment": {
            "headless": settings.headless,
            "provider": settings.provider.value,
            "incident_capture": settings.incident_capture,
        },
        "command": command,
        "transport": transport,
        "request": {
            "ui_mode": ui_mode,
            "model_alias": model_alias,
            "aspect": aspect,
            "count": count,
            "locale": locale,
            "has_profile": has_profile,
            "has_project": has_project,
            "media_input_count": media_input_count,
        },
        "error": {
            "class": exc_class,
            "problem_type": problem_type,
            "exit_code": exit_code,
            "retryable": retryable,
            "route": route,
            "phase": phase,
        },
        "artifacts": dict(artifacts),
        "artifact_status": dict(artifact_status),
        "har_state": har_state,
        "suppressed_count": suppressed_count,
        "notice": _MANIFEST_NOTICE,
    }


# --- IncidentRecorder (design §4.2, §5.2, §6.1–§6.2) -----------------------


@dataclass(frozen=True, slots=True)
class IncidentRef:
    """Local capture result. ``id``/``capture_status`` are remote-safe; ``path``
    and ``artifacts`` are CLI-local only (S21)."""

    id: str
    capture_status: str
    path: Path | None
    artifacts: tuple[str, ...]


class _CapturePage(Protocol):
    async def evaluate(self, script: str, /) -> object: ...

    async def screenshot(self, *, path: str, full_page: bool = ...) -> object: ...


@dataclass(slots=True)
class _StagedBundle:
    ref: IncidentRef
    bundle: BundleDir
    exc_class: str
    problem_type: str
    exit_code: int
    retryable: bool
    route: str
    phase: str
    artifacts: dict[str, str]
    artifact_status: dict[str, str]
    created_utc: str
    suppressed: int = 0
    finalized: bool = False


_MAX_BUNDLES_PER_COMMAND = 3
_CAPTURE_BUDGET_S = 8.0
_DOM_TIMEOUT_S = 3.0
_SCREENSHOT_TIMEOUT_S = 4.0
_MAX_OVERLAYS = 10
_LIGATURE_RE = re.compile(r"[a-z0-9_]{1,40}")
_OVERLAY_TAG_RE = re.compile(r"[a-z]{1,16}")
_OVERLAY_ROLE_RE = re.compile(r"[a-z\-]{1,32}")
_NET_FAILURE_RE = re.compile(r"net::[A-Z0-9_]{1,64}")

# Structural-only DOM probe: allowlisted signals, counts, geometry, and
# Material-Symbol ligatures. Raw title/url cross into Python memory only and
# are reduced by classify_title/sanitize_url before persistence; every other
# raw string is dropped by validate_structural_dom (S12). Public: this is the
# ONE DOM engine (design §6.3) — the legacy ui_automation capture wrapper
# consumes it too.
STRUCTURAL_DOM_JS = r"""() => {
  const syms = [...document.querySelectorAll('i.google-symbols')]
    .map(e => (e.textContent || '').trim()).filter(Boolean);
  const overlays = [...document.querySelectorAll(
      '[role="dialog"],[aria-modal="true"],dialog')].slice(0, 10).map(el => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return {
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || '',
      ariaModal: el.getAttribute('aria-modal') === 'true',
      visible: r.width > 0 && r.height > 0 && cs.visibility !== 'hidden',
      rect: {x: r.x, y: r.y, width: r.width, height: r.height},
      zIndex: parseInt(cs.zIndex, 10) || 0,
      pointerEvents: cs.pointerEvents,
      ligatures: [...el.querySelectorAll('i.google-symbols')]
        .map(e => (e.textContent || '').trim()).filter(Boolean),
    };
  });
  const tags = {};
  for (const t of ['div','button','input','textarea','dialog','iframe','video','img']) {
    tags[t] = document.getElementsByTagName(t).length;
  }
  return {
    url: location.href,
    title: document.title,
    ligatures: [...new Set(syms)].sort(),
    ligatureCount: syms.length,
    cropPresent: syms.some(l => l.startsWith('crop')),
    textboxes: document.querySelectorAll(
      'textarea,[contenteditable="true"],[role="textbox"],div[data-slate-editor]').length,
    tagCounts: tags,
    viewport: {width: window.innerWidth, height: window.innerHeight},
    scroll: {x: window.scrollX, y: window.scrollY},
    overlays,
  };
}"""


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return default


def _clean_ligatures(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    items = cast("list[object]", value)
    return [s for s in items if isinstance(s, str) and _LIGATURE_RE.fullmatch(s)][:100]


class IncidentRecorder:
    """Session-scoped private incident capture (one per ``FlowApiClient``).

    Observation-only: apart from read-only DOM evaluation and screenshots it
    never navigates, clicks, types, submits, retries, mints tokens, downloads,
    or mutates queue state (S15). Capture failures are swallowed and reduced
    to ``incident.capture_failed`` — the original exception always wins (S23).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.enabled: bool = settings.incident_capture
        self.correlation_id: str = resolve_correlation_id()
        self.hasher = CommandHasher()
        self.journal = IncidentJournal()
        self.timing = RequestTimingMap()
        self.bookkeeping = ListenerBookkeeping()
        self.command: str | None = None
        self.transport: str | None = None
        self._staged: dict[str, _StagedBundle] = {}
        self._lock = asyncio.Lock()
        self._frozen = False
        self._har_path: Path | None = settings.har_path
        self._har_snapshot: tuple[int, int] | None = None
        self._har_snapshot_taken = False

    # -- trigger classification (design §4.2) -------------------------------

    def should_capture(self, exc: BaseException) -> bool:
        from gflow_cli.errors import (
            AuthExpiredError,
            ContentPolicyError,
            GFlowError,
            ProfileLockedError,
        )

        if not self.enabled:
            return False
        if not isinstance(exc, Exception):
            return False  # cancellation/KeyboardInterrupt/SystemExit are not incidents
        if isinstance(exc, (ContentPolicyError, AuthExpiredError)):
            return False  # deterministic operator remediation; DOM adds nothing
        if isinstance(exc, ProfileLockedError):
            return True  # metadata-only incident
        if isinstance(exc, _capture_triggers()):
            return True
        if isinstance(exc, GFlowError):
            return False  # usage/config/etc.
        return True  # unexpected exception — runtime evidence changes diagnosis

    @staticmethod
    def _screenshot_wanted(exc: BaseException) -> bool:
        return isinstance(exc, _screenshot_triggers())

    # -- listener-facing primitives (called with extracted primitives only) --

    def record_request(
        self, *, url: str, method: str, resource_type: str, request_key: str, monotonic_ts: float
    ) -> None:
        if self._frozen:
            return
        # URL is intentionally unused here: requests only start timing;
        # journaled records are responses/failures (§5.3).
        del url
        self.timing.start(request_key, monotonic_ts)

    def record_response(
        self,
        *,
        url: str,
        method: str,
        resource_type: str,
        status: int,
        request_key: str,
        monotonic_ts: float,
    ) -> None:
        if self._frozen:
            return
        duration = self.timing.finish(request_key, monotonic_ts)
        s = sanitize_url(url, self.hasher)
        self.journal.add_network(
            NetworkRecord(
                ts_monotonic=monotonic_ts,
                ts_utc=datetime.now(UTC).isoformat(),
                method=method.upper()[:8],
                host_category=s.host_category,
                route=s.route,
                resource_type=resource_type[:20],
                status_or_failure=str(_as_int(status)),
                duration_ms=duration,
            )
        )

    def record_request_failed(
        self,
        *,
        url: str,
        method: str,
        resource_type: str,
        failure: str | None,
        request_key: str,
        monotonic_ts: float,
    ) -> None:
        if self._frozen:
            return
        self.timing.finish(request_key, monotonic_ts)
        s = sanitize_url(url, self.hasher)
        failure_category = (
            failure
            if failure is not None and _NET_FAILURE_RE.fullmatch(failure)
            else "failed_other"
        )
        self.journal.add_network(
            NetworkRecord(
                ts_monotonic=monotonic_ts,
                ts_utc=datetime.now(UTC).isoformat(),
                method=method.upper()[:8],
                host_category=s.host_category,
                route=s.route,
                resource_type=resource_type[:20],
                status_or_failure=failure_category,
                duration_ms=None,
            )
        )

    def record_console(
        self, *, level: str, text: str, url: str | None, line: int | None, column: int | None
    ) -> None:
        if self._frozen or level not in ("warning", "error"):
            return
        source = sanitize_url(url, self.hasher).host_category if url else "unknown"
        summary = text_summary(text, f"console_{level}")
        self.journal.add_console(
            ConsoleRecord(
                ts_utc=datetime.now(UTC).isoformat(),
                level=level,
                category=summary.category,
                length=summary.length,
                source_category=source,
                line=line,
                column=column,
            )
        )

    def record_page_error(self, *, error_class: str, message: str) -> None:
        if self._frozen:
            return
        self.journal.add_page_error(
            PageErrorRecord(
                ts_utc=datetime.now(UTC).isoformat(),
                error_class=error_class[:64],
                length=len(message),
            )
        )

    # -- HAR honesty (design §5.6) ------------------------------------------

    def note_har_pre_launch(self, har_path: Path | None) -> None:
        self._har_path = har_path
        self._har_snapshot = self._stat_har(har_path) if har_path is not None else None
        self._har_snapshot_taken = True

    @staticmethod
    def _stat_har(path: Path) -> tuple[int, int] | None:
        try:
            st = os.stat(path)
        except OSError:
            return None
        return (st.st_size, st.st_mtime_ns)

    def resolve_har_state(self, *, close_ok: bool) -> str:
        if self._har_path is None:
            return "disabled"
        if not self._har_snapshot_taken:
            return "pending_flush"  # configured but the session never launched
        if not close_ok:
            return "possibly_incomplete"
        current = self._stat_har(self._har_path)
        if current is None:
            return "possibly_incomplete"
        if self._har_snapshot is None or current != self._har_snapshot:
            return "complete"  # demonstrably created/changed by this session
        return "possibly_incomplete"  # mere pre-existing file is not proof

    # -- capture orchestration ----------------------------------------------

    def detach_and_freeze(self) -> None:
        """Stop accepting events before context close; late callbacks no-op."""
        self._frozen = True
        self.journal.freeze()

    async def capture_failure(
        self,
        exc: BaseException,
        *,
        page: _CapturePage | None,
        phase: str,
        route: str | None = None,
    ) -> IncidentRef | None:
        """Stage an incident bundle while the page is alive. Never raises
        (except re-raising cancellation); never finalizes the manifest."""
        if not self.should_capture(exc):
            return None
        fingerprint = self._fingerprint(exc, route, phase)
        incident_id = f"{self.correlation_id}-{fingerprint}"
        try:
            async with self._lock:
                existing = self._staged.get(fingerprint)
                if existing is not None:
                    existing.suppressed += 1
                    emit_capture_suppressed(incident_id, count=existing.suppressed)
                    return existing.ref
                if len(self._staged) >= _MAX_BUNDLES_PER_COMMAND:
                    emit_capture_suppressed(incident_id, count=1)
                    return None
                staged = await asyncio.wait_for(
                    self._stage_new(
                        exc, incident_id=incident_id, page=page, phase=phase, route=route
                    ),
                    timeout=_CAPTURE_BUDGET_S,
                )
                if staged is not None:
                    self._staged[fingerprint] = staged
                    return staged.ref
                return None
        except asyncio.CancelledError:
            raise
        except Exception as capture_exc:  # noqa: BLE001 — original error must win (S23)
            emit_capture_failed(
                incident_id, exc_class=type(capture_exc).__name__, artifact_kind="bundle"
            )
            return None

    async def capture_metadata_only(self, exc: BaseException, *, phase: str) -> IncidentRef | None:
        """Bundle with no page-derived artifacts — profile contention before
        Chrome launches, or partial setup with no page (S07/S34)."""
        return await self.capture_failure(exc, page=None, phase=phase)

    async def finalize_all(self, *, close_ok: bool) -> None:
        """Atomically finalize every staged manifest after context close has
        established the HAR state. Best-effort per bundle; never raises."""
        try:
            async with self._lock:
                har_state = self.resolve_har_state(close_ok=close_ok)
                for staged in self._staged.values():
                    if staged.finalized:
                        continue
                    try:
                        staged.bundle.finalize(self._manifest_for(staged, har_state))
                        staged.finalized = True
                        emit_capture_completed(
                            staged.ref.id,
                            status=staged.ref.capture_status,
                            artifact_kinds=sorted(staged.artifacts),
                            duration_ms=0,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 — best-effort (S23)
                        emit_capture_failed(
                            staged.ref.id, exc_class=type(exc).__name__, artifact_kind="manifest"
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            emit_capture_failed("unknown", exc_class=type(exc).__name__, artifact_kind="manifest")

    # -- internals ----------------------------------------------------------

    def _fingerprint(self, exc: BaseException, route: str | None, phase: str) -> str:
        problem_type = str(getattr(exc, "problem_type", "unexpected"))
        raw = f"{type(exc).__name__}|{problem_type}|{route or ''}|{phase}"
        return hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()[:10]

    def _cli_version(self) -> str:
        try:
            from importlib.metadata import version

            return version("gflow-cli")
        except Exception:  # noqa: BLE001 — manifest metadata is best-effort
            return "unknown"

    async def _stage_new(
        self,
        exc: BaseException,
        *,
        incident_id: str,
        page: _CapturePage | None,
        phase: str,
        route: str | None,
    ) -> _StagedBundle | None:
        root = validated_incidents_root(self._settings.home)
        if root is None:
            emit_capture_failed(incident_id, exc_class="RootUnavailable", artifact_kind="root")
            return None
        bundle = BundleDir.create_exclusive(root, incident_id)
        emit_capture_started(incident_id)
        artifacts: dict[str, str] = {}
        status: dict[str, str] = {}

        if page is not None:
            await self._stage_ui_json(bundle, page, incident_id, artifacts, status)
            if self._screenshot_wanted(exc):
                await self._stage_screenshot(bundle, page, incident_id, artifacts, status)

        self._stage_journals(bundle, incident_id, artifacts, status)

        overall = "complete"
        if any(v != "complete" for v in status.values()):
            overall = "partial" if any(v == "complete" for v in status.values()) else "failed"
        ref = IncidentRef(
            id=incident_id,
            capture_status=overall,
            path=bundle.path,
            artifacts=tuple(sorted(artifacts)),
        )
        from gflow_cli.errors import GFlowError, is_retryable
        from gflow_cli.json_output import exit_code_for

        return _StagedBundle(
            ref=ref,
            bundle=bundle,
            exc_class=type(exc).__name__,
            problem_type=str(getattr(exc, "problem_type", "unexpected")),
            exit_code=exit_code_for(exc) if isinstance(exc, GFlowError) else 1,
            retryable=is_retryable(exc) if isinstance(exc, GFlowError) else False,
            route=route or "",
            phase=phase,
            artifacts=artifacts,
            artifact_status=status,
            created_utc=datetime.now(UTC).isoformat(),
        )

    async def _stage_ui_json(
        self,
        bundle: BundleDir,
        page: _CapturePage,
        incident_id: str,
        artifacts: dict[str, str],
        status: dict[str, str],
    ) -> None:
        try:
            raw = await asyncio.wait_for(page.evaluate(STRUCTURAL_DOM_JS), _DOM_TIMEOUT_S)
            validated = validate_structural_dom(raw, self.hasher)
            bundle.write_artifact(
                "ui.json", json.dumps(validated, indent=2, ensure_ascii=False).encode("utf-8")
            )
            artifacts["ui.json"] = "automatic"
            status["ui.json"] = "complete"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — per-artifact best-effort
            status["ui.json"] = "failed"
            emit_capture_failed(incident_id, exc_class=type(exc).__name__, artifact_kind="dom")

    async def _stage_screenshot(
        self,
        bundle: BundleDir,
        page: _CapturePage,
        incident_id: str,
        artifacts: dict[str, str],
        status: dict[str, str],
    ) -> None:
        target = bundle.path / "sensitive" / "screenshot.png"
        target.parent.mkdir(mode=0o700, exist_ok=True)
        name = "sensitive/screenshot.png"
        try:
            await asyncio.wait_for(
                page.screenshot(path=str(target), full_page=True), _SCREENSHOT_TIMEOUT_S
            )
            artifacts[name] = "sensitive"
            status[name] = "complete"
            return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — fall back once to viewport (S13)
            pass
        try:
            await asyncio.wait_for(
                page.screenshot(path=str(target), full_page=False), _SCREENSHOT_TIMEOUT_S
            )
            artifacts[name] = "sensitive"
            status[name] = "partial"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            status[name] = "failed"
            emit_capture_failed(
                incident_id, exc_class=type(exc).__name__, artifact_kind="screenshot"
            )

    def _stage_journals(
        self,
        bundle: BundleDir,
        incident_id: str,
        artifacts: dict[str, str],
        status: dict[str, str],
    ) -> None:
        snap = self.journal.snapshot()
        payloads = {
            "network.json": {"records": [asdict(r) for r in snap.network]},
            "browser.json": {
                "console": [asdict(r) for r in snap.console],
                "page_errors": [asdict(r) for r in snap.page_errors],
            },
        }
        for name, payload in payloads.items():
            try:
                bundle.write_artifact(
                    name, json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
                )
                artifacts[name] = "automatic"
                status[name] = "complete"
            except Exception as exc:  # noqa: BLE001
                status[name] = "failed"
                emit_capture_failed(incident_id, exc_class=type(exc).__name__, artifact_kind=name)

    def _manifest_for(self, staged: _StagedBundle, har_state: str) -> dict[str, object]:
        return build_manifest(
            incident_id=staged.ref.id,
            settings=self._settings,
            created_utc=staged.created_utc,
            finalized_utc=datetime.now(UTC).isoformat(),
            cli_version=self._cli_version(),
            exc_class=staged.exc_class,
            problem_type=staged.problem_type,
            exit_code=staged.exit_code,
            retryable=staged.retryable,
            route=staged.route,
            phase=staged.phase,
            command=self.command,
            transport=self.transport,
            artifacts=staged.artifacts,
            artifact_status=staged.artifact_status,
            har_state=har_state,
            suppressed_count=staged.suppressed,
        )

    # Structural validation is module-level (validate_structural_dom) so the
    # legacy ui_automation capture wrapper shares the ONE engine (§6.3).


# --- retention (design §8) — a security boundary (S37–S39) -----------------

_RETENTION_MAX_COMPLETE = 50
_RETENTION_MAX_COMPLETE_BYTES = 250 * 1024 * 1024
_RETENTION_MAX_PENDING = 20
_RETENTION_MAX_PENDING_BYTES = 100 * 1024 * 1024
_RETENTION_MAX_PENDING_AGE_S = 24 * 3600.0
_MANIFEST_PARSE_CAP_BYTES = 64 * 1024
# The exact artifact universe a gflow bundle may contain. ANY other entry
# marks the directory unknown → untouched.
_ALLOWED_TOP_FILES = frozenset(
    {"manifest.json", "ui.json", "network.json", "browser.json", ".pending"}
)
_ALLOWED_SENSITIVE_FILES = frozenset({"screenshot.png"})


def run_retention(
    root: Path,
    *,
    max_complete: int = _RETENTION_MAX_COMPLETE,
    max_complete_bytes: int = _RETENTION_MAX_COMPLETE_BYTES,
    max_pending: int = _RETENTION_MAX_PENDING,
    max_pending_bytes: int = _RETENTION_MAX_PENDING_BYTES,
    max_pending_age_s: float = _RETENTION_MAX_PENDING_AGE_S,
) -> None:
    """Prune old complete bundles and stale recorder-owned pending directories.

    Holds a non-blocking incidents-root retention lock — if another process
    owns it, returns silently. Deletes ONLY direct-grandchild directories that
    are either (a) valid ``gflow-incident-v1`` bundles with an exactly
    allowlisted artifact set, or (b) recorder-owned pending directories whose
    marker lock is acquirable and which are stale/over-cap. Unknown content,
    invalid/oversized manifests, and anything behind a link is never touched.
    Best-effort: any OSError degrades to skipping, never raising.
    """
    try:
        lock_fd = os.open(
            root / ".retention", os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600
        )
    except OSError:
        return
    try:
        if os.fstat(lock_fd).st_size == 0:
            os.write(lock_fd, b"\0")
            os.lseek(lock_fd, 0, os.SEEK_SET)
        try:
            _kernel_lock_nonblocking(lock_fd)
        except OSError:
            return  # another process owns retention this round
        try:
            _run_retention_locked(
                root,
                max_complete=max_complete,
                max_complete_bytes=max_complete_bytes,
                max_pending=max_pending,
                max_pending_bytes=max_pending_bytes,
                max_pending_age_s=max_pending_age_s,
            )
        finally:
            _kernel_unlock(lock_fd)
    except OSError:
        return
    finally:
        os.close(lock_fd)


@dataclass(frozen=True, slots=True)
class _RetentionCandidate:
    path: Path
    size: int
    sort_key: str  # timestamped name — lexicographic == chronological
    marker_mtime: float


def _run_retention_locked(
    root: Path,
    *,
    max_complete: int,
    max_complete_bytes: int,
    max_pending: int,
    max_pending_bytes: int,
    max_pending_age_s: float,
) -> None:
    complete: list[_RetentionCandidate] = []
    pending: list[_RetentionCandidate] = []
    root_resolved = root.resolve()
    for day in sorted(p for p in root.iterdir() if p.is_dir()):
        if _is_reparse_point(day):
            continue
        for bundle in sorted(p for p in day.iterdir() if p.is_dir()):
            try:
                _classify_for_retention(bundle, root_resolved, complete, pending)
            except OSError:
                continue

    freed = 0
    pruned_complete = 0
    pruned_pending = 0

    # Complete bundles: keep the newest within BOTH caps, prune the rest.
    complete.sort(key=lambda c: c.sort_key, reverse=True)  # newest first
    kept_bytes = 0
    for index, cand in enumerate(complete):
        kept_bytes += cand.size
        if index < max_complete and kept_bytes <= max_complete_bytes:
            continue
        got = _safe_delete_bundle(cand.path, root_resolved)
        if got:
            freed += got
            pruned_complete += 1

    # Pending (unlocked, no valid manifest): stale by age, then over-cap oldest-first.
    import time

    now = time.time()
    pending.sort(key=lambda c: c.marker_mtime)  # oldest first
    survivors: list[_RetentionCandidate] = []
    for cand in pending:
        if now - cand.marker_mtime > max_pending_age_s:
            got = _safe_delete_bundle(cand.path, root_resolved)
            if got:
                freed += got
                pruned_pending += 1
                continue
        survivors.append(cand)
    total_pending_bytes = sum(c.size for c in survivors)
    while survivors and (len(survivors) > max_pending or total_pending_bytes > max_pending_bytes):
        cand = survivors.pop(0)
        got = _safe_delete_bundle(cand.path, root_resolved)
        if got:
            freed += got
            pruned_pending += 1
            total_pending_bytes -= cand.size

    if pruned_complete or pruned_pending:
        emit_retention_pruned(
            complete_count=pruned_complete, pending_count=pruned_pending, bytes_freed=freed
        )


def _classify_for_retention(
    bundle: Path,
    root_resolved: Path,
    complete: list[_RetentionCandidate],
    pending: list[_RetentionCandidate],
) -> None:
    if _is_reparse_point(bundle) or not bundle.resolve().is_relative_to(root_resolved):
        return
    marker = bundle / ".pending"
    if marker.exists():
        stale_complete = False
        fd = os.open(marker, os.O_RDWR | getattr(os, "O_BINARY", 0))
        try:
            try:
                _kernel_lock_nonblocking(fd)
            except OSError:
                return  # ACTIVE recorder owns it — never inspect or prune
            try:
                if _valid_manifest(bundle):
                    stale_complete = True
                else:
                    pending.append(_candidate(bundle, marker_mtime=marker.stat().st_mtime))
            finally:
                _kernel_unlock(fd)
        finally:
            os.close(fd)
        if stale_complete:
            # Crash-left stale marker on a finalized bundle: clean the marker
            # (after the fd is closed — Windows refuses unlink on open handles)
            # and keep the bundle as complete.
            marker.unlink(missing_ok=True)
            complete.append(_candidate(bundle))
        return
    if _valid_manifest(bundle):
        complete.append(_candidate(bundle))
    # else: unknown directory — untouched (S37)


def _candidate(bundle: Path, *, marker_mtime: float = 0.0) -> _RetentionCandidate:
    size = 0
    for dirpath, _dirnames, filenames in os.walk(bundle, followlinks=False):
        for name in filenames:
            try:
                size += os.lstat(Path(dirpath) / name).st_size
            except OSError:
                continue
    return _RetentionCandidate(
        path=bundle, size=size, sort_key=str(bundle), marker_mtime=marker_mtime
    )


def _valid_manifest(bundle: Path) -> bool:
    """Bounded parse + exact schema/artifact-set validation (S37)."""
    manifest = bundle / "manifest.json"
    try:
        st = os.lstat(manifest)
    except OSError:
        return False
    if stat_module.S_ISLNK(st.st_mode) or st.st_size > _MANIFEST_PARSE_CAP_BYTES:
        return False
    try:
        parsed: object = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if (
        not isinstance(parsed, dict)
        or cast("dict[str, object]", parsed).get("schema") != "gflow-incident-v1"
    ):
        return False
    # Exact artifact universe: any unknown entry → not ours → untouched.
    try:
        for entry in bundle.iterdir():
            if entry.is_dir():
                if entry.name != "sensitive" or _is_reparse_point(entry):
                    return False
                for sub in entry.iterdir():
                    if not sub.is_file() or sub.name not in _ALLOWED_SENSITIVE_FILES:
                        return False
            elif entry.name not in _ALLOWED_TOP_FILES:
                return False
    except OSError:
        return False
    return True


def _safe_delete_bundle(bundle: Path, root_resolved: Path) -> int:
    """Delete a validated bundle without ever following a link. Returns bytes
    freed, or 0 when refused/failed (the bundle is then left as-is)."""
    try:
        if _is_reparse_point(bundle) or not bundle.resolve().is_relative_to(root_resolved):
            return 0
        files: list[Path] = []
        dirs: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(bundle, followlinks=False):
            here = Path(dirpath)
            if _is_reparse_point(here):
                return 0
            for name in dirnames:
                if _is_reparse_point(here / name):
                    return 0
            dirs.append(here)
            files.extend(here / name for name in filenames)
        freed = 0
        for file in files:
            freed += os.lstat(file).st_size
            os.unlink(file)
        for directory in sorted(dirs, reverse=True):
            os.rmdir(directory)
    except OSError:
        return 0
    return freed


def validate_structural_dom(raw: object, hasher: CommandHasher) -> dict[str, object]:
    """Rebuild the DOM probe result from the allowlist — any unexpected key or
    non-primitive value is dropped, never persisted (S12). The ONE validation
    used by both the recorder and the legacy ui_automation capture wrapper."""
    if not isinstance(raw, dict):
        return {"invalid": True}
    data = cast("dict[str, object]", raw)
    ligatures = _clean_ligatures(data.get("ligatures"))
    out: dict[str, object] = {
        "ligatures": ligatures,
        "ligature_count": _as_int(data.get("ligatureCount"), default=len(ligatures)),
        "signals": {
            "crop_present": bool(data.get("cropPresent")),
            "textboxes": _as_int(data.get("textboxes")),
        },
    }
    url = data.get("url")
    if isinstance(url, str):
        out["url"] = asdict(sanitize_url(url, hasher))
    title = data.get("title")
    if isinstance(title, str):
        out["title"] = asdict(classify_title(title))
    tag_counts = data.get("tagCounts")
    if isinstance(tag_counts, dict):
        counts = cast("dict[str, object]", tag_counts)
        out["tag_counts"] = {
            tag: _as_int(counts.get(tag))
            for tag in ("div", "button", "input", "textarea", "dialog", "iframe", "video", "img")
            if tag in counts
        }
    for key in ("viewport", "scroll"):
        geom = data.get(key)
        if isinstance(geom, dict):
            g = cast("dict[str, object]", geom)
            out[key] = {
                axis: _as_int(g.get(axis)) for axis in ("x", "y", "width", "height") if axis in g
            }
    overlays = data.get("overlays")
    if isinstance(overlays, list):
        out["overlays"] = [
            _validate_overlay(cast("dict[str, object]", o))
            for o in cast("list[object]", overlays)[:_MAX_OVERLAYS]
            if isinstance(o, dict)
        ]
    return out


def _validate_overlay(raw: dict[str, object]) -> dict[str, object]:
    tag = raw.get("tag")
    role = raw.get("role")
    pointer = raw.get("pointerEvents")
    rect_raw = raw.get("rect")
    rect: dict[str, int] = {}
    if isinstance(rect_raw, dict):
        r = cast("dict[str, object]", rect_raw)
        rect = {axis: _as_int(r.get(axis)) for axis in ("x", "y", "width", "height")}
    return {
        "tag": tag if isinstance(tag, str) and _OVERLAY_TAG_RE.fullmatch(tag) else "other",
        "role": role if isinstance(role, str) and _OVERLAY_ROLE_RE.fullmatch(role) else "other",
        "aria_modal": bool(raw.get("ariaModal")),
        "visible": bool(raw.get("visible")),
        "rect": rect,
        "zIndex": _as_int(raw.get("zIndex")),
        "pointer_events": pointer if pointer in ("auto", "none") else "other",
        "ligatures": _clean_ligatures(raw.get("ligatures")),
    }


def _capture_triggers() -> tuple[type[BaseException], ...]:
    from gflow_cli.errors import (
        BrowserSessionClosedError,
        FlowAgentUiError,
        FlowAppError,
        NetworkError,
        TransportTimeoutError,
        UiModeUnavailableError,
        UiSelectorDriftError,
        WafRejectionError,
        WireFormatError,
    )

    return (
        FlowAppError,
        FlowAgentUiError,
        UiModeUnavailableError,
        UiSelectorDriftError,
        TransportTimeoutError,
        BrowserSessionClosedError,
        WireFormatError,
        WafRejectionError,
        NetworkError,
    )


def _screenshot_triggers() -> tuple[type[BaseException], ...]:
    from gflow_cli.errors import (
        FlowAgentUiError,
        FlowAppError,
        TransportTimeoutError,
        UiModeUnavailableError,
        UiSelectorDriftError,
    )

    return (
        FlowAppError,
        FlowAgentUiError,
        UiModeUnavailableError,
        UiSelectorDriftError,
        TransportTimeoutError,
    )
