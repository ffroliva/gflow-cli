"""Red spec for the `gflow data sync` CLI surface (#543, Task S1 -> S5).

Contract pinned here:

- ``sync`` lives under the ``data`` group; bare ``gflow data sync`` (no
  ``--names`` scope) is a Click usage error (exit 2). Sync WRITES BY DEFAULT
  (locked decision) — ``--dry-run`` is the opt-in preview, there is no
  ``--apply``.
- flags: ``--names``, ``--project`` (repeatable), ``--limit``, ``--since``,
  ``--all``, ``--max-projects`` (default 50), ``--dry-run``, ``--json``,
  ``--profile``.
- the command binds ``run_sync`` in its own module namespace — tests
  monkeypatch ``gflow_cli.cli_data.run_sync`` (same pattern as
  ``cli_doctor.run_all`` in test_cli_doctor.py).
- ``history_prompts=redacted`` -> exit 11 with remediation naming
  ``GFLOW_CLI_HISTORY_PROMPTS``.
- ``SyncPartialError`` -> exit 34, retryable.
- progress/summary: a summary line reaches the output — formatting is NOT
  pinned beyond the counts appearing.

Red reason (by design, STRICT TDD): ``SyncPartialError`` does not exist in
``gflow_cli.errors`` yet — the module-top import fails, so this whole file is
red at collection until the errors change lands (S5).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from gflow_cli import cli_data
from gflow_cli.cli import main
from gflow_cli.config import reset_settings
from gflow_cli.data.store import DataStore
from gflow_cli.errors import EXIT_CODE_MAP, SyncPartialError, is_retryable
from tests.fixtures.listing_payload import listing_payload, named_pair


def _summary(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "projects_visited": 7,
        "names_written": 42,
        "ghosts_marked": 1,
        "rows_still_nameless": 3,
        "failures": (),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class _RunSyncSpy:
    def __init__(self, result: Any = None, raises: Exception | None = None) -> None:
        self.result = result if result is not None else _summary()
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return self.result


def _install(monkeypatch: pytest.MonkeyPatch, spy: _RunSyncSpy) -> None:
    monkeypatch.setattr(cli_data, "run_sync", spy)


def test_bare_data_sync_is_usage_error_exit_2() -> None:
    result = CliRunner().invoke(main, ["data", "sync"])
    assert result.exit_code == 2, result.output


def test_sync_names_runs_writes_by_default_and_prints_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _RunSyncSpy()
    _install(monkeypatch, spy)
    result = CliRunner().invoke(main, ["data", "sync", "--names"])
    assert result.exit_code == 0, result.output
    assert len(spy.calls) == 1
    assert spy.calls[0]["dry_run"] is False  # write by default — no --apply
    assert spy.calls[0]["max_projects"] == 50
    # A summary line reaches the output (counts only — format not pinned).
    assert "42" in result.output


def test_sync_dry_run_flag_passes_dry_run_true(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _RunSyncSpy()
    _install(monkeypatch, spy)
    result = CliRunner().invoke(main, ["data", "sync", "--names", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert spy.calls[0]["dry_run"] is True


def test_sync_max_projects_flag_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _RunSyncSpy()
    _install(monkeypatch, spy)
    result = CliRunner().invoke(main, ["data", "sync", "--names", "--max-projects", "5"])
    assert result.exit_code == 0, result.output
    assert spy.calls[0]["max_projects"] == 5


def test_sync_accepts_scoping_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag inventory: --profile / --project (repeatable) / --limit / --since
    and --all must all be accepted (pass-through semantics are pinned at the
    repository layer, not here)."""
    spy = _RunSyncSpy()
    _install(monkeypatch, spy)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "data",
            "sync",
            "--names",
            "--profile",
            "default",
            "--project",
            "11111111-1111-4111-8111-111111111111",
            "--project",
            "22222222-2222-4222-8222-222222222222",
            "--limit",
            "3",
            "--since",
            "2026-08-01",
        ],
    )
    assert result.exit_code == 0, result.output
    scoped = spy.calls[0]
    assert scoped["profile_name"] == "default"
    assert scoped["project_ids"] == (
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
    )
    assert scoped["limit"] == 3
    # --since crosses the Click boundary as a real datetime (S5 bridge pin) —
    # the repo layer takes datetime, never a raw string.
    assert scoped["since"] == datetime(2026, 8, 1)
    result = runner.invoke(main, ["data", "sync", "--names", "--all"])
    assert result.exit_code == 0, result.output
    assert len(spy.calls) == 2
    # --all is the explicit spelling of the default scope: no narrowing.
    full = spy.calls[1]
    assert full["project_ids"] is None
    assert full["limit"] is None
    assert full["since"] is None


def test_sync_json_outputs_parseable_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _RunSyncSpy()
    _install(monkeypatch, spy)
    result = CliRunner().invoke(main, ["data", "sync", "--names", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["projects_visited"] == 7
    assert payload["names_written"] == 42


def test_sync_redacted_exits_11_naming_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """Privacy gate end-to-end: no mocking — the refusal must fire from the
    real settings BEFORE any transport/client work, mapping to exit 11 (the
    ConfigurationError code) with remediation naming the env var."""
    monkeypatch.setenv("GFLOW_CLI_HISTORY_PROMPTS", "redacted")
    reset_settings()
    result = CliRunner().invoke(main, ["data", "sync", "--names"])
    assert result.exit_code == 11, result.output
    assert "GFLOW_CLI_HISTORY_PROMPTS" in result.output


def test_sync_partial_error_maps_to_exit_34(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _RunSyncSpy(raises=SyncPartialError("2 of 3 projects failed"))
    _install(monkeypatch, spy)
    result = CliRunner().invoke(main, ["data", "sync", "--names"])
    assert result.exit_code == 34, result.output


def test_sync_real_bridge_opens_store_in_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the live-caught ``sqlite3.ProgrammingError`` (#543).

    ``run_sync`` executes in a worker thread (``asyncio.to_thread``) while the
    sqlite connection defaults to ``check_same_thread=True`` — so the store
    MUST be opened inside that worker thread. This test exercises the REAL
    ``run_sync`` + REAL store through the real bridge (no monkeypatched
    ``run_sync``); only the remote listing fetch is canned, so no browser
    launches. On the broken code (store opened on the main thread) it fails
    with exit 1 / ProgrammingError instead of exit 0.
    """
    media_id, media, workflow = named_pair("Restored name")
    project_id = str(uuid.uuid4())
    payload = listing_payload(media=[media], workflows=[workflow])
    monkeypatch.setattr(
        cli_data._ThreadSafeListingClient,
        "fetch_project_listing",
        lambda self, pid: payload,
    )

    # Seed one nameless asset row in the isolated per-test DB (autouse
    # ``_isolate_settings`` already points GFLOW_CLI_DB_PATH at tmp).
    db = Path(os.environ["GFLOW_CLI_DB_PATH"])
    now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    with DataStore.open(db) as store:
        store.conn.execute(
            "INSERT INTO profiles(name, first_seen_at) VALUES (?, ?)", ("default", now)
        )
        store.conn.execute(
            "INSERT INTO assets(id, profile_name, flow_project_id, flow_media_id,"
            " kind, status, created_at, metadata_json)"
            " VALUES (?, 'default', ?, ?, 'image', 'ready', ?, NULL)",
            (str(uuid.uuid4()), project_id, media_id, now),
        )

    result = CliRunner().invoke(
        main, ["data", "sync", "--names", "--project", project_id, "--profile", "default"]
    )
    assert result.exit_code == 0, result.output

    with DataStore.open(db) as store:
        row = store.conn.execute(
            "SELECT metadata_json FROM assets WHERE flow_media_id = ?", (media_id,)
        ).fetchone()
    meta = json.loads(row["metadata_json"])
    assert meta["display_name"] == "Restored name"
    assert meta["sync"]["source"] == "sync"
    assert meta["sync"]["named_at"]


def test_sync_partial_error_exit_code_and_retryability_pinned() -> None:
    assert EXIT_CODE_MAP[SyncPartialError] == 34
    assert is_retryable(SyncPartialError("partial"))
