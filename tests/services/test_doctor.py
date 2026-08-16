"""Red spec for the `gflow doctor` service layer (#542, Task D1).

These tests ARE the contract for `gflow_cli.services.doctor` (Task D2):

- ``Finding`` — frozen dataclass ``(check, severity, summary, remediation,
  row_uuids)`` with severity in {"pass", "info", "warn", "fail"}.
- ``DoctorReport`` — ``.findings`` (per-check entries, every check id present)
  and ``.overall_status`` ("ok" when nothing is warn/fail, else "issues").
- ``run_all(db_path, settings) -> DoctorReport`` — read-only, never migrates.

Env-shaped checks are pinned to their source modules: tests monkeypatch
``gflow_cli.browser_manager.installed_chromium_version`` and
``gflow_cli.profile_store.list_profiles``, so the implementation must call
them through their module namespaces (no ``from x import y`` re-binding).
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from gflow_cli.services.doctor import DoctorReport, Finding, run_all

from gflow_cli.config import get_settings, reset_settings
from gflow_cli.data.store import DataStore
from gflow_cli.profile_store import ProfileMeta

if TYPE_CHECKING:
    from collections.abc import Callable

# Frozen v1 check inventory — any rename/addition/removal must update this
# list AND the plan (docs/superpowers/plans/2026-08-16-doctor-and-catalog-sync).
CHECK_IDS = (
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

SEVERITIES = frozenset({"pass", "info", "warn", "fail"})


def _ts(dt: datetime) -> str:
    """Timestamp in the recorder's on-disk format (see data/recorder.py)."""
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


_NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Fixture DB builders — real migrations via DataStore.open, rows via .conn
# ---------------------------------------------------------------------------


def _new_db(tmp_path: Path) -> Path:
    db = tmp_path / "doctor.db"
    with DataStore.open(db) as store:
        store.conn.execute(
            "INSERT INTO profiles(name, first_seen_at) VALUES ('default', ?)",
            (_ts(_NOW),),
        )
    return db


def _insert_asset(conn: sqlite3.Connection, *, display_name: str | None) -> str:
    asset_id = str(uuid.uuid4())
    metadata = json.dumps({"display_name": display_name}) if display_name else None
    conn.execute(
        "INSERT INTO assets(id, profile_name, flow_media_id, kind, status, created_at,"
        " metadata_json) VALUES (?, 'default', ?, 'image', 'ready', ?, ?)",
        (asset_id, str(uuid.uuid4()), _ts(_NOW), metadata),
    )
    return asset_id


def _insert_local_file(
    conn: sqlite3.Connection,
    *,
    asset_id: str,
    path: Path,
    sha256: str | None,
) -> None:
    conn.execute(
        "INSERT INTO local_files(id, profile_name, asset_id, path, sha256, created_at)"
        " VALUES (?, 'default', ?, ?, ?, ?)",
        (str(uuid.uuid4()), asset_id, str(path), sha256, _ts(_NOW)),
    )


def _insert_operation(
    conn: sqlite3.Connection,
    *,
    status: str,
    started_at: datetime,
    completed_at: datetime | None = None,
) -> str:
    op_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO operations(id, profile_name, mode, status, started_at, completed_at)"
        " VALUES (?, 'default', 't2i', ?, ?, ?)",
        (op_id, status, _ts(started_at), _ts(completed_at) if completed_at else None),
    )
    return op_id


def _insert_queue_task(
    conn: sqlite3.Connection,
    *,
    status: str,
    claimed_at: datetime | None = None,
) -> str:
    task_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO generation_queue(task_id, profile_name, task_type, payload_json,"
        " status, claimant, claimed_at, created_at, updated_at)"
        " VALUES (?, 'default', 'image', '{}', ?, ?, ?, ?, ?)",
        (
            task_id,
            status,
            "worker-1" if claimed_at else None,
            _ts(claimed_at) if claimed_at else None,
            _ts(_NOW),
            _ts(_NOW),
        ),
    )
    return task_id


def _healthy_asset(conn: sqlite3.Connection, tmp_path: Path) -> str:
    """Asset with display_name, an existing local file, and a sha256."""
    asset_id = _insert_asset(conn, display_name="good asset")
    good = tmp_path / "good.png"
    good.write_bytes(b"png")
    _insert_local_file(conn, asset_id=asset_id, path=good, sha256="ab" * 32)
    return asset_id


def _clean_db(tmp_path: Path) -> Path:
    """DB where every check should come back severity 'pass'.

    Includes a RECENT started operation and a RECENT processing queue task:
    recency, not status alone, is what makes them 'stuck'.
    """
    db = _new_db(tmp_path)
    with DataStore.open(db) as store:
        _healthy_asset(store.conn, tmp_path)
        _insert_operation(
            store.conn,
            status="succeeded",
            started_at=_NOW - timedelta(minutes=5),
            completed_at=_NOW,
        )
        _insert_operation(store.conn, status="started", started_at=_NOW - timedelta(minutes=1))
        _insert_queue_task(store.conn, status="completed")
        _insert_queue_task(store.conn, status="processing", claimed_at=_NOW - timedelta(minutes=1))
    return db


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the env-shaped checks pass: browsers installed, auth present."""
    for var in ("GFLOW_CLI_PREFER_CLASSIC", "GFLOW_CLI_FORCE_AGENT_UI", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "gflow_cli.browser_manager.installed_chromium_version",
        lambda: "139.0.7258.5",
    )
    monkeypatch.setattr(
        "gflow_cli.profile_store.list_profiles",
        lambda: [
            ProfileMeta(
                name="default",
                profile_dir=Path("profile_default"),
                cookies_present=True,
                last_used_at=None,
                is_default=True,
            )
        ],
    )


def _flagged(report: DoctorReport, check: str) -> list[Finding]:
    return [f for f in report.findings if f.check == check and f.severity in ("warn", "fail")]


# ---------------------------------------------------------------------------
# Finding / DoctorReport shape
# ---------------------------------------------------------------------------


def test_finding_is_frozen_with_expected_fields() -> None:
    finding = Finding(
        check="catalog.sha256_null",
        severity="warn",
        summary="1 local file has no sha256",
        remediation="run gflow data sync",
        row_uuids=("a" * 36,),
    )
    assert finding.check == "catalog.sha256_null"
    assert finding.severity in SEVERITIES
    assert isinstance(finding.row_uuids, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        finding.severity = "pass"  # type: ignore[misc]


def test_clean_run_reports_every_check_as_pass(
    tmp_path: Path,
    clean_env: None,
) -> None:
    report = run_all(_clean_db(tmp_path), get_settings())
    by_check = {f.check for f in report.findings}
    assert by_check == set(CHECK_IDS)
    assert all(f.severity == "pass" for f in report.findings), [
        (f.check, f.severity) for f in report.findings if f.severity != "pass"
    ]
    assert report.overall_status == "ok"


def test_any_warn_or_fail_flips_overall_status(tmp_path: Path, clean_env: None) -> None:
    db = _new_db(tmp_path)
    with DataStore.open(db) as store:
        _insert_asset(store.conn, display_name=None)
    report = run_all(db, get_settings())
    assert report.overall_status == "issues"


# ---------------------------------------------------------------------------
# DB-shaped checks: each flags its seeded defect, stays silent on a clean DB
# ---------------------------------------------------------------------------


def _seed_display_name_missing(conn: sqlite3.Connection, tmp_path: Path) -> str:
    return _insert_asset(conn, display_name=None)


def _seed_local_file_missing(conn: sqlite3.Connection, tmp_path: Path) -> str:
    asset_id = _insert_asset(conn, display_name="has a name")
    _insert_local_file(conn, asset_id=asset_id, path=tmp_path / "gone.png", sha256="cd" * 32)
    return asset_id


def _seed_sha256_null(conn: sqlite3.Connection, tmp_path: Path) -> str:
    asset_id = _insert_asset(conn, display_name="has a name")
    present = tmp_path / "present.png"
    present.write_bytes(b"png")
    _insert_local_file(conn, asset_id=asset_id, path=present, sha256=None)
    return asset_id


def _seed_stuck_started(conn: sqlite3.Connection, tmp_path: Path) -> str:
    return _insert_operation(conn, status="started", started_at=_NOW - timedelta(days=2))


def _seed_stuck_processing(conn: sqlite3.Connection, tmp_path: Path) -> str:
    return _insert_queue_task(conn, status="processing", claimed_at=_NOW - timedelta(days=2))


DB_SEEDERS: list[tuple[str, Callable[[sqlite3.Connection, Path], str]]] = [
    ("catalog.display_name_missing", _seed_display_name_missing),
    ("catalog.local_file_missing", _seed_local_file_missing),
    ("catalog.sha256_null", _seed_sha256_null),
    ("operations.stuck_started", _seed_stuck_started),
    ("queue.stuck_processing", _seed_stuck_processing),
]


@pytest.mark.parametrize(("check_id", "seeder"), DB_SEEDERS, ids=[c for c, _ in DB_SEEDERS])
def test_db_check_flags_seeded_defect(
    tmp_path: Path,
    clean_env: None,
    check_id: str,
    seeder: Callable[[sqlite3.Connection, Path], str],
) -> None:
    db = _new_db(tmp_path)
    with DataStore.open(db) as store:
        seeded_uuid = seeder(store.conn, tmp_path)
    report = run_all(db, get_settings())
    flagged = _flagged(report, check_id)
    assert flagged, f"{check_id} did not flag its seeded defect"
    assert seeded_uuid in {u for f in flagged for u in f.row_uuids}


# ---------------------------------------------------------------------------
# db.migration_drift
# ---------------------------------------------------------------------------


def _forget_last_migration(db: Path) -> None:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY CAST(version AS INTEGER) DESC LIMIT 1"
        ).fetchone()
        conn.execute("DELETE FROM schema_migrations WHERE version = ?", (row[0],))
        conn.execute(f"PRAGMA user_version = {int(row[0]) - 1}")
        conn.commit()
    finally:
        conn.close()


def test_migration_drift_flags_stale_schema(tmp_path: Path, clean_env: None) -> None:
    db = _clean_db(tmp_path)
    _forget_last_migration(db)
    assert _flagged(run_all(db, get_settings()), "db.migration_drift")


def test_migration_drift_flags_db_newer_than_binary(tmp_path: Path, clean_env: None) -> None:
    db = _clean_db(tmp_path)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO schema_migrations(version, filename, checksum, applied_at)"
            " VALUES ('9999', '9999_future.sql', 'deadbeef', ?)",
            (_ts(_NOW),),
        )
        conn.execute("PRAGMA user_version = 9999")
        conn.commit()
    finally:
        conn.close()
    assert _flagged(run_all(db, get_settings()), "db.migration_drift")


def test_doctor_never_writes_the_database(tmp_path: Path, clean_env: None) -> None:
    """Byte-identical DB before/after a run against a stale-schema DB.

    Doctor must diagnose drift, not heal it — no migration applied, no WAL
    sidecars created. Fixture uses journal_mode=DELETE so the main file is the
    whole database.
    """
    db = _clean_db(tmp_path)
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.commit()
    finally:
        conn.close()
    _forget_last_migration(db)

    before = db.read_bytes()
    report = run_all(db, get_settings())
    assert _flagged(report, "db.migration_drift")
    assert db.read_bytes() == before
    assert not db.with_name(db.name + "-wal").exists()
    assert not db.with_name(db.name + "-shm").exists()


# ---------------------------------------------------------------------------
# db.wal_state
# ---------------------------------------------------------------------------


def test_wal_state_flags_stale_sidecar(tmp_path: Path, clean_env: None) -> None:
    # ponytail: quick_check-failure seeding (a corrupted page) is left to D2's
    # implementation tests; a stale sidecar is the deterministic seedable defect.
    db = _clean_db(tmp_path)
    db.with_name(db.name + "-wal").write_bytes(b"\x00" * 128)
    assert _flagged(run_all(db, get_settings()), "db.wal_state")


# ---------------------------------------------------------------------------
# env.deprecated_vars
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("var", "successor"),
    [
        ("GFLOW_CLI_PREFER_CLASSIC", "GFLOW_CLI_UI_MODE"),
        ("GFLOW_CLI_FORCE_AGENT_UI", "GFLOW_CLI_UI_MODE"),
        ("GEMINI_API_KEY", None),
    ],
)
def test_deprecated_env_var_flags(
    tmp_path: Path,
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
    var: str,
    successor: str | None,
) -> None:
    db = _clean_db(tmp_path)
    monkeypatch.setenv(var, "1")
    flagged = _flagged(run_all(db, get_settings()), "env.deprecated_vars")
    assert flagged
    text = " ".join(f.summary + " " + f.remediation for f in flagged)
    assert var in text
    if successor is not None:
        assert successor in text


def test_db_path_env_settings_disagreement_flags(
    tmp_path: Path,
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _clean_db(tmp_path)
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    reset_settings()
    settings = get_settings()  # resolved_db_path() == db
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(tmp_path / "somewhere_else.db"))
    assert _flagged(run_all(db, settings), "env.deprecated_vars")


# ---------------------------------------------------------------------------
# env.browsers_missing / auth.files_present
# ---------------------------------------------------------------------------


def test_browsers_missing_flags_when_no_chromium(
    tmp_path: Path,
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("gflow_cli.browser_manager.installed_chromium_version", lambda: None)
    flagged = _flagged(run_all(_clean_db(tmp_path), get_settings()), "env.browsers_missing")
    assert flagged
    assert flagged[0].severity == "warn"


def test_auth_files_present_flags_profile_without_cookies(
    tmp_path: Path,
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "gflow_cli.profile_store.list_profiles",
        lambda: [
            ProfileMeta(
                name="default",
                profile_dir=tmp_path / "profile_default",
                cookies_present=False,
                last_used_at=None,
                is_default=True,
            )
        ],
    )
    assert _flagged(run_all(_clean_db(tmp_path), get_settings()), "auth.files_present")


# ---------------------------------------------------------------------------
# Redaction rules (service layer)
# ---------------------------------------------------------------------------


def test_findings_use_uuids_never_display_name_values(
    tmp_path: Path,
    clean_env: None,
) -> None:
    db = _new_db(tmp_path)
    with DataStore.open(db) as store:
        asset_id = _insert_asset(store.conn, display_name="TOP-SECRET-CLIENT-NAME")
        _insert_local_file(
            store.conn,
            asset_id=asset_id,
            path=tmp_path / "gone.png",
            sha256="ef" * 32,
        )
    report = run_all(db, get_settings())
    all_text = " ".join(f.summary + " " + f.remediation for f in report.findings)
    assert "TOP-SECRET-CLIENT-NAME" not in all_text
    assert asset_id in {u for f in report.findings for u in f.row_uuids}


def test_display_name_missing_is_info_under_redacted_privacy(
    tmp_path: Path,
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _new_db(tmp_path)
    with DataStore.open(db) as store:
        _insert_asset(store.conn, display_name=None)
    monkeypatch.setenv("GFLOW_CLI_HISTORY_PROMPTS", "redacted")
    reset_settings()
    report = run_all(db, get_settings())
    findings = [f for f in report.findings if f.check == "catalog.display_name_missing"]
    assert findings
    assert findings[0].severity == "info"
    assert "suppress" in findings[0].remediation.lower()
