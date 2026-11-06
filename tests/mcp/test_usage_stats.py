"""Tests for MCP gflow_usage_stats tool (#1164)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gflow_cli.cli import main
from gflow_cli.mcp import tools
from tests.fixtures.seeded_catalog import build_seeded_catalog


@pytest.fixture
def seeded_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "catalog.db"
    store, _repo = build_seeded_catalog(db)
    store.close()
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    return db


@pytest.mark.asyncio
async def test_mcp_twin_matches_cli_json(
    seeded_db: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gflow_cli import telemetry

    monkeypatch.delenv("GFLOW_CLI_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    home = tmp_path / "gflow_home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("GFLOW_CLI_HOME", str(home))
    telemetry.killed.cache_clear()

    telemetry.record("cli", "image t2i", [], "0", 1.0)
    telemetry.record("mcp", "video t2v", [], "0", 2.0)

    mcp_result = await tools.gflow_usage_stats()

    cli_result = CliRunner().invoke(main, ["data", "stats", "--json"])
    assert cli_result.exit_code == 0, cli_result.output
    cli_json = json.loads(cli_result.output)

    mcp_result = await tools.gflow_usage_stats()
    expected = dict(cli_json)
    expected["commands"] = sorted(
        [*cli_json["commands"], {"surface": "cli", "command": "data stats", "n": 1}],
        key=lambda row: (row["surface"], row["command"]),
    )
    assert mcp_result == expected
    telemetry.killed.cache_clear()


@pytest.mark.asyncio
async def test_mcp_when_killed_says_counters_are_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli import telemetry

    monkeypatch.setenv("GFLOW_CLI_TELEMETRY", "0")
    telemetry.killed.cache_clear()

    mcp_result = await tools.gflow_usage_stats()
    assert mcp_result["telemetry"]["state"] == "off"
    assert mcp_result["telemetry"]["switch"] == "GFLOW_CLI_TELEMETRY"
    assert "report" in mcp_result["telemetry"]
    assert mcp_result["commands"] is None  # never reads as zero usage
    telemetry.killed.cache_clear()


@pytest.mark.asyncio
async def test_mcp_usage_stats_invalid_weeks() -> None:
    res_0 = await tools.gflow_usage_stats(weeks=0)
    assert res_0["status"] == "error"
    assert res_0["error"]["status"] == 400

    res_54 = await tools.gflow_usage_stats(weeks=54)
    assert res_54["status"] == "error"
    assert res_54["error"]["status"] == 400
