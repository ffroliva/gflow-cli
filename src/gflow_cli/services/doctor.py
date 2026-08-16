"""Read-only health checks for ``gflow doctor`` (#542).

Brew-doctor philosophy: diagnose, never heal. Every check inspects local
state (catalog DB, environment, auth files) and reports a :class:`Finding`;
nothing is migrated, repaired, or written. All DB access goes through
:meth:`DataStore.open_readonly`, and every check runs inside a guard that
converts unexpected exceptions into a "fail" finding — doctor must never
crash on a damaged database.

Severity model:

- ``pass`` — check ran, nothing to report.
- ``info`` — worth knowing, not a defect. Info findings do NOT flip
  ``overall_status``; only warn/fail do (``ok`` -> ``issues``).
- ``warn`` — actionable defect; remediation carries a copy-pasteable command.
- ``fail`` — broken state the doctor cannot examine further.

Thresholds: an operation in ``status='started'`` with no ``completed_at``,
or a queue task in ``status='processing'``, counts as *stuck* only once its
start/claim timestamp is older than 24 hours — recency, not status alone.

Redaction: findings identify rows by UUID only (never display-name values),
route filesystem paths through :func:`safe_path_text`, and strip C0/C1
control characters from all composed strings.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import structlog

# _cli_helpers is imported as a module (not `from ... import safe_path_text`)
# so tests patching gflow_cli._cli_helpers.safe_path_text reach this layer.
from gflow_cli import _cli_helpers, browser_manager, profile_store
from gflow_cli.data.store import DataStore, inspect_schema
from gflow_cli.errors import DataStoreError

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable, Iterable

    from gflow_cli.config import Settings

log = structlog.get_logger(__name__)

Severity = Literal["pass", "info", "warn", "fail"]

#: Frozen v1 check inventory — mirrored by tests/fixtures/doctor_env.py.
CHECK_IDS: tuple[str, ...] = (
    "catalog.display_name_missing",
    "catalog.local_file_missing",
    "catalog.sha256_null",
    "db.migration_drift",
    "db.wal_state",
    "operations.stuck_started",
    "queue.stuck_processing",
    "env.deprecated_vars",
    "env.browsers_missing",
    "auth.files_present",
)

_STUCK_THRESHOLD = timedelta(hours=24)
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


@dataclass(frozen=True)
class Finding:
    """One diagnostic result. Row identity is by UUID only — never values."""

    check: str
    severity: Severity
    summary: str
    remediation: str
    row_uuids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DoctorReport:
    """All findings from one run; overall status derives from severities."""

    findings: tuple[Finding, ...]

    @property
    def overall_status(self) -> Literal["ok", "issues"]:
        bad = any(f.severity in ("warn", "fail") for f in self.findings)
        return "issues" if bad else "ok"


def _scrub(text: str) -> str:
    """Strip C0/C1 control chars (ESC/BEL in tampered paths, etc.)."""
    return _CONTROL_CHARS.sub("", text)


def _finding(
    check: str,
    severity: Severity,
    summary: str,
    remediation: str = "",
    row_uuids: Iterable[str] = (),
) -> Finding:
    return Finding(
        check=check,
        severity=severity,
        summary=_scrub(summary),
        remediation=_scrub(remediation),
        row_uuids=tuple(_scrub(str(u)) for u in row_uuids),
    )


def _parse_ts(raw: object) -> datetime | None:
    """Parse a recorder-format ISO-8601 'Z' timestamp; bad input -> None."""
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Catalog checks (read-only SQL over the open connection)
# ---------------------------------------------------------------------------


def _profile_suffix(counts: Counter[str]) -> str:
    """Per-profile breakdown, appended only when rows span >1 profile.

    Profile names are local user-chosen identifiers (unlike emails) so they
    may appear in output; ``_finding`` still routes them through ``_scrub``.
    Ordered by count descending, then name, for a stable rendering.
    """
    if len(counts) < 2:
        return ""
    parts = ", ".join(
        f"{name}: {n}" for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    return f" ({parts})"


def _check_display_name_missing(conn: sqlite3.Connection, settings: Settings) -> list[Finding]:
    rows = conn.execute(
        "SELECT id, profile_name FROM assets WHERE metadata_json IS NULL"
        " OR json_extract(metadata_json, '$.display_name') IS NULL"
        " OR json_extract(metadata_json, '$.display_name') = ''",
    ).fetchall()
    if not rows:
        return []
    ids = [str(row["id"]) for row in rows]
    per_profile = Counter(str(row["profile_name"]) for row in rows)
    summary = f"{len(ids)} asset(s) have no display name{_profile_suffix(per_profile)}"
    if settings.history_prompts == "redacted":
        return [
            _finding(
                "catalog.display_name_missing",
                "info",
                summary,
                "GFLOW_CLI_HISTORY_PROMPTS=redacted suppresses name backfill;"
                " set GFLOW_CLI_HISTORY_PROMPTS=store, then run: gflow data sync --names",
                ids,
            )
        ]
    if len(per_profile) > 1:
        # Sync operates on one profile per run — suggest one command each.
        remediation = "; ".join(
            f"gflow data sync --names --profile {name}" for name in sorted(per_profile)
        )
    else:
        remediation = "gflow data sync --names"
    return [
        _finding(
            "catalog.display_name_missing",
            "warn",
            summary,
            remediation,
            ids,
        )
    ]


def _check_local_file_missing(conn: sqlite3.Connection) -> list[Finding]:
    rows = conn.execute(
        "SELECT asset_id, path, profile_name FROM local_files WHERE storage_provider IS NULL",
    ).fetchall()
    missing: list[str] = []
    per_profile: Counter[str] = Counter()
    example: str | None = None
    for row in rows:
        path = Path(str(row["path"]))
        if not path.exists():
            missing.append(str(row["asset_id"]))
            per_profile[str(row["profile_name"])] += 1
            if example is None:
                example = _cli_helpers.safe_path_text(path)
    if not missing:
        return []
    return [
        _finding(
            "catalog.local_file_missing",
            "warn",
            f"{len(missing)} cataloged local file(s) missing on disk"
            f" (e.g. {example}){_profile_suffix(per_profile)}",
            "gflow data prune --dry-run",
            missing,
        )
    ]


def _check_sha256_null(conn: sqlite3.Connection) -> list[Finding]:
    rows = conn.execute(
        "SELECT asset_id, profile_name FROM local_files"
        " WHERE storage_provider IS NULL AND sha256 IS NULL",
    ).fetchall()
    if not rows:
        return []
    ids = [str(row["asset_id"]) for row in rows]
    per_profile = Counter(str(row["profile_name"]) for row in rows)
    return [
        _finding(
            "catalog.sha256_null",
            "warn",
            f"{len(ids)} local file(s) have no recorded sha256{_profile_suffix(per_profile)}",
            # No command recomputes hashes yet (deferred `doctor fix` scope) —
            # be honest rather than point at sync, which never touches sha256.
            "Re-download or re-generate the asset to refresh its recorded file;"
            " if the file is gone from disk, remove the dead row"
            " (preview with `gflow data prune --dry-run`).",
            ids,
        )
    ]


# ---------------------------------------------------------------------------
# DB integrity checks
# ---------------------------------------------------------------------------


def _check_migration_drift(db_path: Path) -> list[Finding]:
    try:
        inspection = inspect_schema(db_path)
    except (ValueError, DataStoreError) as exc:
        # A tampered schema_migrations row (non-numeric version) raises a raw
        # ValueError from inspect_schema — degrade, never crash.
        return [
            _finding(
                "db.migration_drift",
                "fail",
                f"schema inspection failed ({type(exc).__name__})",
                "Re-run with GFLOW_CLI_LOG_LEVEL=DEBUG for details, or file an issue.",
            )
        ]
    if inspection.newer_than_binary:
        return [
            _finding(
                "db.migration_drift",
                "fail",
                f"database schema (user_version {inspection.user_version})"
                " is newer than this gflow-cli build",
                "uv tool upgrade gflow-cli",
            )
        ]
    if inspection.drift:
        return [
            _finding(
                "db.migration_drift",
                "warn",
                f"database schema (user_version {inspection.user_version})"
                " does not match the packaged migrations",
                "Run any writing gflow command (e.g. `gflow data sync --names`) to"
                " apply migrations, or restore the database from backup if tampered.",
            )
        ]
    return []


def _db_header_is_wal(db_path: Path) -> bool | None:
    """True/False from the SQLite header's write-version byte; None if unreadable.

    The header (offset 18, 1=rollback-journal, 2=WAL) is the only reliable
    signal: ``PRAGMA journal_mode`` reports 'wal' whenever a ``-wal`` sidecar
    exists — even next to a rollback-mode DB — so querying it would make the
    stale-sidecar check vacuous.
    """
    try:
        with db_path.open("rb") as fh:
            header = fh.read(20)
    except OSError:
        return None
    if len(header) < 20 or not header.startswith(b"SQLite format 3\x00"):
        return None
    return header[18] == 2  # noqa: PLR2004 — SQLite file-format write version


def _check_wal_state(
    conn: sqlite3.Connection | None,
    stale_sidecars: tuple[str, ...],
    header_is_wal: bool | None,
) -> list[Finding]:
    findings: list[Finding] = []
    # Sidecar presence is sampled BEFORE the read-only open. For a WAL-mode
    # database sidecars are NORMAL (any connection — including doctor's own
    # read-only open — creates them and may leave them behind), so they only
    # count as stale when the DB header says the journal mode is NOT WAL.
    # An unreadable header (None) with sidecars still warns — we cannot
    # prove they are benign.
    if stale_sidecars and header_is_wal is not True:
        findings.append(
            _finding(
                "db.wal_state",
                "warn",
                f"stale WAL sidecar file(s) next to the database: {', '.join(stale_sidecars)}",
                "close other gflow processes, then re-run: gflow doctor",
            )
        )
    if conn is not None:
        # quick_check, never integrity_check — same corruption coverage minus
        # the expensive index-content scan.
        rows = conn.execute("PRAGMA quick_check").fetchall()
        results = [str(row[0]) for row in rows]
        if results != ["ok"]:
            findings.append(
                _finding(
                    "db.wal_state",
                    "fail",
                    f"PRAGMA quick_check reported {len(results)} anomaly(ies)",
                    "Restore the database from a backup, or export what you can and"
                    " rebuild (`gflow data prune --dry-run` to inspect).",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Stuck-work checks (24h threshold — see module docstring)
# ---------------------------------------------------------------------------


def _stuck_ids(rows: Iterable[sqlite3.Row], id_col: str, ts_col: str) -> list[str]:
    now = datetime.now(UTC)
    stuck: list[str] = []
    for row in rows:
        ts = _parse_ts(row[ts_col])
        if ts is not None and now - ts > _STUCK_THRESHOLD:
            stuck.append(str(row[id_col]))
    return stuck


def _check_stuck_started(conn: sqlite3.Connection) -> list[Finding]:
    rows = conn.execute(
        "SELECT id, started_at FROM operations WHERE status = 'started' AND completed_at IS NULL",
    ).fetchall()
    stuck = _stuck_ids(rows, "id", "started_at")
    if not stuck:
        return []
    return [
        _finding(
            "operations.stuck_started",
            "warn",
            f"{len(stuck)} operation(s) stuck in 'started' for over 24h",
            "gflow data errors prune",
            stuck,
        )
    ]


def _check_stuck_processing(conn: sqlite3.Connection) -> list[Finding]:
    rows = conn.execute(
        "SELECT task_id, claimed_at FROM generation_queue WHERE status = 'processing'",
    ).fetchall()
    stuck = _stuck_ids(rows, "task_id", "claimed_at")
    if not stuck:
        return []
    return [
        _finding(
            "queue.stuck_processing",
            "warn",
            f"{len(stuck)} queue task(s) stuck in 'processing' for over 24h",
            "gflow data prune --dry-run",
            stuck,
        )
    ]


# ---------------------------------------------------------------------------
# Environment / auth checks
# ---------------------------------------------------------------------------


def _check_deprecated_vars(settings: Settings) -> list[Finding]:
    successors = {
        "GFLOW_CLI_PREFER_CLASSIC": "GFLOW_CLI_UI_MODE",
        "GFLOW_CLI_FORCE_AGENT_UI": "GFLOW_CLI_UI_MODE",
        "GEMINI_API_KEY": None,
    }
    findings = [
        _finding(
            "env.deprecated_vars",
            "warn",
            f"deprecated environment variable {var} is set",
            f"unset {var} and use {successor} instead"
            if successor
            else f"unset {var} (no longer read by gflow-cli)",
        )
        for var, successor in successors.items()
        if os.environ.get(var)
    ]
    env_db = os.environ.get("GFLOW_CLI_DB_PATH")
    if env_db and Path(env_db).expanduser().resolve() != settings.resolved_db_path().resolve():
        findings.append(
            _finding(
                "env.deprecated_vars",
                "warn",
                "GFLOW_CLI_DB_PATH disagrees with the resolved settings database path",
                "unset GFLOW_CLI_DB_PATH or point it at"
                f" {_cli_helpers.safe_path_text(settings.resolved_db_path())}",
            )
        )
    return findings


def _check_browsers_missing() -> list[Finding]:
    if browser_manager.installed_chromium_version() is not None:
        return []
    return [
        _finding(
            "env.browsers_missing",
            "warn",
            "Playwright Chromium is not installed",
            "playwright install chromium",
        )
    ]


def _check_auth_files_present() -> list[Finding]:
    profiles = profile_store.list_profiles()
    if not profiles:
        return [
            _finding(
                "auth.files_present",
                "warn",
                "no auth profiles found",
                "gflow auth login",
            )
        ]
    # ponytail: counts only — profile emails/names never appear in findings.
    missing = sum(1 for profile in profiles if not profile.cookies_present)
    if missing:
        return [
            _finding(
                "auth.files_present",
                "warn",
                f"{missing} of {len(profiles)} profile(s) have no saved cookies",
                "gflow auth login",
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _require(conn: sqlite3.Connection | None) -> sqlite3.Connection:
    if conn is None:
        msg = "database could not be opened read-only"
        raise DataStoreError(msg)
    return conn


def _guarded(check_id: str, fn: Callable[[], list[Finding]]) -> list[Finding]:
    try:
        results = fn()
    except Exception as exc:  # noqa: BLE001 — doctor must never crash on a damaged DB
        log.debug("doctor_check_failed", check=check_id, exc_info=True)
        return [
            _finding(
                check_id,
                "fail",
                f"check could not run ({type(exc).__name__})",
                "Re-run with GFLOW_CLI_LOG_LEVEL=DEBUG for details, or file an issue.",
            )
        ]
    return results or [_finding(check_id, "pass", "ok")]


def run_all(db_path: Path, settings: Settings) -> DoctorReport:
    """Run every check read-only against ``db_path`` and the environment."""
    stale_sidecars = tuple(
        suffix for suffix in ("-wal", "-shm") if db_path.with_name(db_path.name + suffix).exists()
    )
    header_is_wal = _db_header_is_wal(db_path)
    store: DataStore | None = None
    try:
        store = DataStore.open_readonly(db_path)
    except DataStoreError:
        store = None  # per-DB checks degrade to fail findings via _require

    try:
        conn = store.conn if store is not None else None
        checks: tuple[tuple[str, Callable[[], list[Finding]]], ...] = (
            (
                "catalog.display_name_missing",
                lambda: _check_display_name_missing(_require(conn), settings),
            ),
            ("catalog.local_file_missing", lambda: _check_local_file_missing(_require(conn))),
            ("catalog.sha256_null", lambda: _check_sha256_null(_require(conn))),
            ("db.migration_drift", lambda: _check_migration_drift(db_path)),
            ("db.wal_state", lambda: _check_wal_state(conn, stale_sidecars, header_is_wal)),
            ("operations.stuck_started", lambda: _check_stuck_started(_require(conn))),
            ("queue.stuck_processing", lambda: _check_stuck_processing(_require(conn))),
            ("env.deprecated_vars", lambda: _check_deprecated_vars(settings)),
            ("env.browsers_missing", _check_browsers_missing),
            ("auth.files_present", _check_auth_files_present),
        )
        findings: list[Finding] = []
        for check_id, fn in checks:
            findings.extend(_guarded(check_id, fn))
    finally:
        if store is not None:
            store.close()
    return DoctorReport(findings=tuple(findings))
