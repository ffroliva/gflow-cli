"""Tests for Task 5: build_report, record_tool, gflow telemetry status|show (#1164)."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gflow_cli import telemetry, update_check
from gflow_cli.cli import main
from gflow_cli.data.models import (
    OperationKind,
    OperationRecord,
    OperationStatus,
    ProjectRecord,
)
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.tools.spec import ToolConfig, ToolSpec

ALLOWED = {
    "schema",
    "report_id",
    "week",
    "version",
    "python",
    "os",
    "arch",
    "installer",
    "install_age",
    "storage",
    "commands",
    "flags",
    "exits",
    "durations",
    "transitions",
    "models",
    "modes",
    "aspects",
    "outcomes",
    "mcp_clients",
    "crashes",
    "tools",
}


@pytest.fixture(autouse=True)
def _tele_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Test isolation: clean GFLOW_CLI_HOME, unset kill switches, reset cache."""
    monkeypatch.delenv("GFLOW_CLI_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("TZ", "America/New_York")
    monkeypatch.setattr(update_check, "_in_ci", lambda: False)
    monkeypatch.setattr(telemetry, "consent_region", lambda: False)
    home = tmp_path / "gflow_home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("GFLOW_CLI_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    telemetry.killed.cache_clear()
    yield home
    telemetry.killed.cache_clear()


@pytest.fixture
def empty_catalog_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "catalog.db"
    store = DataStore.open(db)
    store.close()
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    return db


def _seed_operation(
    db_path: Path,
    *,
    model: str | None = "NARWHAL",
    mode: OperationKind | str = OperationKind.T2I,
    aspect_ratio: str | None = "16:9",
    status: OperationStatus | str = OperationStatus.SUCCEEDED,
    started_at: str | None = None,
    prompt: str = "a prompt",
    profile_name: str = "alice",
) -> str:
    store = DataStore.open(db_path)
    repo = DataRepository(store)
    repo.upsert_profile(name=profile_name, profile_dir=Path(f"/home/{profile_name}"))
    proj_id = str(uuid.uuid4())
    repo.upsert_project(
        ProjectRecord(
            id=proj_id,
            profile_name=profile_name,
            flow_project_id=f"flow-{proj_id[:8]}",
            title="test project",
            source="cli",
            created_at=datetime.now(UTC).isoformat(),
        )
    )
    op_id = str(uuid.uuid4())
    now_iso = started_at or datetime.now(UTC).isoformat()
    repo.insert_operation(
        OperationRecord(
            id=op_id,
            profile_name=profile_name,
            flow_project_id=f"flow-{proj_id[:8]}",
            command="image",
            mode=mode if isinstance(mode, OperationKind) else OperationKind.T2I,  # type: ignore[arg-type]
            status=status if isinstance(status, OperationStatus) else OperationStatus.SUCCEEDED,  # type: ignore[arg-type]
            flow_operation_id=None,
            flow_batch_id=None,
            prompt=prompt,
            prompt_hash=None,
            prompt_redacted=False,
            model=model,
            aspect_ratio=aspect_ratio,
            error_type=None,
            error_detail=None,
            started_at=now_iso,
            completed_at=now_iso,
        )
    )
    if isinstance(mode, str) and mode not in [k.value for k in OperationKind]:
        store.conn.execute("UPDATE operations SET mode = ? WHERE id = ?", (mode, op_id))
    if isinstance(status, str) and status not in [s.value for s in OperationStatus]:
        store.conn.execute("UPDATE operations SET status = ? WHERE id = ?", (status, op_id))
    store.close()
    return op_id


def test_report_keys_are_exactly_the_allow_list() -> None:
    current_week = telemetry.iso_week()
    report = telemetry.build_report(current_week)
    assert set(report.keys()) == ALLOWED


def test_report_never_contains_seeded_secrets(
    empty_catalog_db: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Seed catalog with prompt, path, profile "jane.doe", s3://bucket/x, and a .gflow_account email
    secret_prompt = "super-secret-customer-prompt-12345"
    secret_path = "/home/jane.doe/private/secret_reference.png"
    secret_profile = "jane.doe"
    secret_s3 = "s3://my-secret-bucket/folder"
    secret_email = "jane.doe.confidential@example.com"

    monkeypatch.setenv("GFLOW_CLI_STORAGE_URI", secret_s3)
    account_file = tmp_path / "gflow_home" / ".gflow_account"
    account_file.write_text(secret_email, encoding="utf-8")

    _seed_operation(
        empty_catalog_db,
        prompt=secret_prompt,
        profile_name=secret_profile,
    )
    # Also record counter with flag
    telemetry.record("cli", "image t2i", ["--aspect"], "0", 1.5)

    dumped = json.dumps(telemetry.build_report(telemetry.iso_week()))
    assert secret_prompt not in dumped
    assert secret_path not in dumped
    assert secret_profile not in dumped
    assert secret_email not in dumped
    assert "my-secret-bucket" not in dumped


def test_counters_are_keyed_by_surface_then_command() -> None:
    week = telemetry.iso_week()
    telemetry.record("cli", "video t2v", ["--aspect"], "0", 1.0)
    telemetry.record("cli", "video t2v", ["--aspect"], "0", 2.0)
    telemetry.record("cli", "video t2v", ["--duration"], "11", 0.5)

    report = telemetry.build_report(week)
    assert report["exits"] == {"cli": {"video t2v": {"0": 2, "11": 1}}}
    assert report["commands"] == {"cli": {"video t2v": 3}}
    assert report["flags"] == {"cli": {"video t2v": {"--aspect": 2, "--duration": 1}}}
    assert "<10s" in report["durations"]["cli"]["video t2v"]  # type: ignore[index]


def test_models_come_from_the_catalog(empty_catalog_db: Path) -> None:
    week = telemetry.iso_week()
    _seed_operation(empty_catalog_db, model="NARWHAL")

    report = telemetry.build_report(week)
    assert report["models"] == {"NARWHAL": 1}


@pytest.mark.parametrize("field", ["models", "modes", "aspects", "outcomes"])
def test_unknown_value_becomes_other(empty_catalog_db: Path, field: str) -> None:
    week = telemetry.iso_week()
    kwargs: dict[str, object] = {
        "model": "unknown_model_xyz" if field == "models" else "NARWHAL",
        "mode": "unknown_mode_xyz" if field == "modes" else OperationKind.T2I,
        "aspect_ratio": "unknown_aspect_xyz" if field == "aspects" else "16:9",
        "status": "unknown_status_xyz" if field == "outcomes" else OperationStatus.SUCCEEDED,
    }
    _seed_operation(empty_catalog_db, **kwargs)  # type: ignore[arg-type]

    report = telemetry.build_report(week)
    assert report[field] == {"other": 1}


@pytest.mark.parametrize(
    "uri,kind",
    [
        (None, "local"),
        ("s3://b/x", "s3"),
        ("gs://b/x", "gcs"),
        ("memory://x", "other"),
    ],
)
def test_storage_is_scheme_only(
    monkeypatch: pytest.MonkeyPatch, uri: str | None, kind: str
) -> None:
    if uri is not None:
        monkeypatch.setenv("GFLOW_CLI_STORAGE_URI", uri)
    else:
        monkeypatch.delenv("GFLOW_CLI_STORAGE_URI", raising=False)

    report = telemetry.build_report(telemetry.iso_week())
    assert report["storage"] == kind


@pytest.mark.parametrize(
    "days,bucket",
    [
        (6, "<1w"),
        (29, "<1m"),
        (179, "<6m"),
        (180, "6m+"),
    ],
)
def test_install_age_buckets(tmp_path: Path, days: int, bucket: str) -> None:
    home = tmp_path / "gflow_home"
    db_file = home / "telemetry.db"
    con = sqlite3.connect(db_file)
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute(
        "CREATE TABLE IF NOT EXISTS counters (week TEXT, surface TEXT, kind TEXT, key TEXT,"
        " n INTEGER, PRIMARY KEY (week, surface, kind, key))"
    )
    first_seen = (datetime.now(UTC) - timedelta(days=days)).date().isoformat()
    con.execute("INSERT OR REPLACE INTO meta VALUES ('first_seen', ?)", (first_seen,))
    con.commit()
    con.close()

    report = telemetry.build_report(telemetry.iso_week())
    assert report["install_age"] == bucket


def test_report_id_is_stable_within_a_week_and_differs_across_weeks() -> None:
    id1 = telemetry.report_id_for("2026-W44")
    id2 = telemetry.report_id_for("2026-W44")
    id3 = telemetry.report_id_for("2026-W45")

    assert id1 == id2
    assert id1 != id3
    assert telemetry.stored_report_id("2026-W44") == id1
    assert len(id1) == 22
    assert re.match(r"^[A-Za-z0-9_-]+$", id1)


def test_off_reports_do_not_create_telemetry_db_or_stored_report_id(
    monkeypatch: pytest.MonkeyPatch, _tele_env: Path
) -> None:
    from gflow_cli.cli_data import collect_usage_stats

    monkeypatch.setenv("DO_NOT_TRACK", "1")
    telemetry.killed.cache_clear()
    home = _tele_env
    result = CliRunner().invoke(main, ["telemetry", "show"])
    assert result.exit_code == 0, result.output
    assert not (home / "telemetry.db").exists()

    collect_usage_stats(weeks=12)
    assert not (home / "telemetry.db").exists()
    assert telemetry.stored_report_id(telemetry.iso_week()) is None


def test_telemetry_state_is_shared_by_status_and_usage_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.cli_data import collect_usage_stats

    monkeypatch.setattr(telemetry, "state", lambda: ("off", "test-switch"))

    status = CliRunner().invoke(main, ["telemetry", "status", "--json"])
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["switch"] == "test-switch"
    assert collect_usage_stats(weeks=12)["telemetry"]["switch"] == "test-switch"


def test_state_keeps_telemetry_off_switch_label(_tele_env: Path) -> None:
    (_tele_env / "telemetry.off").write_text("", encoding="utf-8")
    assert telemetry.state() == ("off", "telemetry.off")


def test_report_catalog_query_is_bounded_to_iso_week(
    empty_catalog_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    statements: list[str] = []
    original_open = DataStore.open_readonly

    def traced_open(db_path: Path) -> DataStore:
        store = original_open(db_path)
        store.conn.set_trace_callback(statements.append)
        return store

    monkeypatch.setattr(DataStore, "open_readonly", staticmethod(traced_open))
    week = telemetry.iso_week()

    telemetry.build_report(week)

    operation_query = next(sql for sql in statements if "FROM operations" in sql)
    assert "started_at >=" in operation_query
    assert "started_at <" in operation_query


def test_packaged_tool_keeps_its_name_with_expanded_and_unchanged_counts() -> None:
    from gflow_cli.tools.loader import load_builtin_tools

    builtins = load_builtin_tools()
    spec = next(iter(builtins.values()))

    telemetry.record_tool(spec, expanded=True)
    telemetry.record_tool(spec, expanded=True)
    telemetry.record_tool(spec, expanded=False)

    report = telemetry.build_report(telemetry.iso_week())
    assert report["tools"][spec.name] == {"expanded": 2, "unchanged": 1}  # type: ignore[index]


def test_user_tool_counts_as_custom() -> None:
    custom_spec = ToolSpec(
        name="acme-client",
        title="Acme Client",
        description="Custom client",
        category="both",
        version="1.0.0",
        config=ToolConfig(system_template="custom"),
    )
    telemetry.record_tool(custom_spec, expanded=True)

    report = telemetry.build_report(telemetry.iso_week())
    assert "acme-client" not in report["tools"]
    assert report["tools"]["custom"] == {"expanded": 1}  # type: ignore[index]


def test_user_tool_shadowing_a_packaged_name_counts_as_custom() -> None:
    from gflow_cli.tools.loader import load_builtin_tools

    builtin_name = next(iter(load_builtin_tools().keys()))
    shadowed_spec = ToolSpec(
        name=builtin_name,
        title="Shadowed Tool",
        description="User override",
        category="both",
        version="1.0.0",
        config=ToolConfig(system_template="shadowed modified config"),
    )
    telemetry.record_tool(shadowed_spec, expanded=False)

    report = telemetry.build_report(telemetry.iso_week())
    assert builtin_name not in report["tools"]
    assert report["tools"]["custom"] == {"unchanged": 1}  # type: ignore[index]


def test_tool_counted_through_cli_and_generation_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.tools.loader import load_builtin_tools
    from gflow_cli.tools.runtime import ExpansionResult

    builtins = load_builtin_tools()
    spec = next(iter(builtins.values()))

    # Mock expander so apply_tool doesn't call external LLM
    with patch("gflow_cli.tools.runtime._apply_tool") as mock_apply:
        mock_apply.return_value = ExpansionResult(
            original="orig", expanded="expanded", was_expanded=True
        )
        from gflow_cli.tools.runtime import apply_tool

        res = apply_tool(spec, "test prompt", {})
        assert res.was_expanded

    report = telemetry.build_report(telemetry.iso_week())
    assert report["tools"][spec.name]["expanded"] == 1  # type: ignore[index]


def test_tool_recording_failure_leaves_the_result_unchanged() -> None:
    from gflow_cli.tools.loader import load_builtin_tools
    from gflow_cli.tools.runtime import ExpansionResult, apply_tool

    builtins = load_builtin_tools()
    spec = next(iter(builtins.values()))

    with (
        patch("gflow_cli.tools.runtime._apply_tool") as mock_apply,
        patch("gflow_cli.telemetry.record_tool") as mock_record,
    ):
        mock_apply.return_value = ExpansionResult(
            original="raw", expanded="raw", was_expanded=False
        )
        mock_record.side_effect = RuntimeError("database exploded")
        res = apply_tool(spec, "raw", {})
        assert res.expanded == "raw"
        assert not res.was_expanded


def test_report_works_with_no_catalog_and_does_not_create_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    absent = tmp_path / "nonexistent" / "absent_catalog.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(absent))

    report = telemetry.build_report(telemetry.iso_week())
    assert report["models"] == {}
    assert report["modes"] == {}
    assert report["aspects"] == {}
    assert report["outcomes"] == {}
    assert not absent.exists()


def test_show_prints_the_report_as_json() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["telemetry", "show"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert set(parsed.keys()) == ALLOWED


def test_status_names_the_kill_switch_in_force(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(update_check, "_in_ci", lambda: False)
    monkeypatch.setattr(update_check, "_installed_from_index", lambda: False)
    monkeypatch.setattr(telemetry, "consent_region", lambda: False)
    runner = CliRunner()

    # 1. Default (none)
    res_none = runner.invoke(main, ["telemetry", "status"])
    assert res_none.exit_code == 0
    assert "none" in res_none.output.lower() or "active" in res_none.output.lower()
    assert "Upload: off (not installed from a package index)" in res_none.output

    # 2. DO_NOT_TRACK
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    telemetry.killed.cache_clear()
    res_dnt = runner.invoke(main, ["telemetry", "status"])
    assert res_dnt.exit_code == 0
    assert "DO_NOT_TRACK" in res_dnt.output
    assert "Upload: off (killed: DO_NOT_TRACK)" in res_dnt.output
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    telemetry.killed.cache_clear()

    # 3. GFLOW_CLI_TELEMETRY
    monkeypatch.setenv("GFLOW_CLI_TELEMETRY", "0")
    telemetry.killed.cache_clear()
    res_env = runner.invoke(main, ["telemetry", "status"])
    assert res_env.exit_code == 0
    assert "GFLOW_CLI_TELEMETRY" in res_env.output
    assert "Upload: off (killed: GFLOW_CLI_TELEMETRY)" in res_env.output
    monkeypatch.delenv("GFLOW_CLI_TELEMETRY", raising=False)
    telemetry.killed.cache_clear()

    # 4. telemetry.off sentinel
    home = tmp_path / "gflow_home"
    (home / "telemetry.off").write_text("", encoding="utf-8")
    res_off = runner.invoke(main, ["telemetry", "status"])
    assert res_off.exit_code == 0
    assert "telemetry off" in res_off.output
    assert "Upload: off (telemetry off)" in res_off.output


def test_status_json_mirrors_telemetry_block() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["telemetry", "status", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "state" in data
    assert "switch" in data
    assert "upload" in data
    assert set(data["upload"]) == {"enabled", "reason"}
    assert "report" in data
    assert set(data["report"].keys()) == ALLOWED


def test_no_keys_contain_pipe() -> None:
    report = telemetry.build_report(telemetry.iso_week())

    def _check_keys(obj: object) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert "|" not in str(k), f"Pipe character found in key {k!r}"
                _check_keys(v)
        elif isinstance(obj, list):
            for item in obj:
                _check_keys(item)

    _check_keys(report)


def test_mcp_tools_param_reaches_apply_tool() -> None:
    from gflow_cli.mcp.tools import _adapt_tools

    adapted = _adapt_tools([{"name": "creative-director", "options": {}}])
    assert isinstance(adapted, tuple)
    assert len(adapted) == 1
    assert "creative-director" in adapted[0]
