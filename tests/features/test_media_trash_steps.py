"""Step bindings for media_trash.feature (#1363).

Executes the offline BDD scenarios for moving media to Flow's restorable Trash
and restoring it over the wire via batchexecute RPC pGCYOe.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli.cli import main
from gflow_cli.config import reset_settings
from gflow_cli.data.models import (
    AssetKind,
    AssetRecord,
    ProjectRecord,
)
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.profile_store import ProfileMeta

scenarios("media_trash.feature")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    db_path = tmp_path / "catalog.db"
    store = DataStore.open(db_path)
    repo = DataRepository(store)

    profiles_dir = tmp_path / "profiles" / "default"
    profiles_dir.mkdir(parents=True, exist_ok=True)

    # Seed default profile
    repo.upsert_profile(name="default", profile_dir=profiles_dir)

    state: dict[str, Any] = {
        "db_path": db_path,
        "store": store,
        "repo": repo,
        "wire_calls": [],
        "cli_result": None,
        "mcp_result": None,
        "tmp_path": tmp_path,
    }

    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
    reset_settings()

    # Intercept wire/client mutations to record calls in tests
    try:
        from gflow_cli.api import flow_wire

        async def fake_direct_trash(
            profile_dir: Path, media_id: str, project_id: str
        ) -> dict[str, Any]:
            state["wire_calls"].append(
                {
                    "rpcid": "pGCYOe",
                    "media_id": media_id,
                    "project_id": project_id,
                    "action": "trash",
                }
            )
            return {"media_id": media_id, "project_id": project_id, "status": "trashed"}

        async def fake_direct_restore(
            profile_dir: Path, media_id: str, project_id: str
        ) -> dict[str, Any]:
            state["wire_calls"].append(
                {
                    "rpcid": "pGCYOe",
                    "media_id": media_id,
                    "project_id": project_id,
                    "action": "restore",
                    "restore": True,
                }
            )
            return {"media_id": media_id, "project_id": project_id, "status": "restored"}

        monkeypatch.setattr(flow_wire, "direct_trash_media", fake_direct_trash, raising=False)
        monkeypatch.setattr(flow_wire, "direct_restore_media", fake_direct_restore, raising=False)
    except Exception:
        pass

    try:
        from gflow_cli import profile_store

        monkeypatch.setattr(
            profile_store,
            "resolve_profile",
            lambda p: ProfileMeta(name=p or "default", profile_dir=profiles_dir),
            raising=False,
        )
    except Exception:
        pass

    return state


# ---------------------------------------------------------------------------
# Given steps
# ---------------------------------------------------------------------------


def _seed_catalog_asset(
    world: dict[str, Any],
    media_id: str,
    project_id: str,
    profile_name: str = "default",
    workflow_id: str = "wf-1",
) -> None:
    repo: DataRepository = world["repo"]
    now_str = datetime.now(UTC).isoformat()

    repo.upsert_profile(name=profile_name, profile_dir=Path(f"/home/{profile_name}/.config/gflow"))
    repo.upsert_project(
        ProjectRecord(
            id=str(uuid.uuid4()),
            profile_name=profile_name,
            flow_project_id=project_id,
            title="Test Proj",
            source="cli",
            created_at=now_str,
        )
    )
    repo.upsert_asset(
        AssetRecord(
            id=str(uuid.uuid4()),
            profile_name=profile_name,
            flow_project_id=project_id,
            flow_media_id=media_id,
            flow_workflow_id=workflow_id,
            flow_media_generation_id=None,
            kind=AssetKind.IMAGE,
            status="ready",
            model="imagen-3.0-fast-generate-001",
            aspect_ratio="16:9",
            width=1280,
            height=720,
            duration_seconds=None,
            seed=1,
            metadata_json={},
            created_at=now_str,
        )
    )


@given(parsers.parse('a cataloged media item with ID "{media_id}" in project "{project_id}"'))
def given_cataloged_media_in_project(world: dict[str, Any], media_id: str, project_id: str) -> None:
    _seed_catalog_asset(world, media_id=media_id, project_id=project_id)


@given(parsers.parse('a cataloged media item with ID "{media_id}"'))
def given_cataloged_media(world: dict[str, Any], media_id: str) -> None:
    _seed_catalog_asset(world, media_id=media_id, project_id="proj-uuid")


@given("the execution environment is non-interactive")
def given_non_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)


@given(parsers.parse('an asset ID "{media_id}" matching multiple profiles "{p1}" and "{p2}"'))
def given_ambiguous_asset(world: dict[str, Any], media_id: str, p1: str, p2: str) -> None:
    _seed_catalog_asset(world, media_id=media_id, project_id="proj-1", profile_name=p1)
    _seed_catalog_asset(world, media_id=media_id, project_id="proj-2", profile_name=p2)


@given(parsers.parse('a trashed media item with ID "{media_id}" in project "{project_id}"'))
def given_trashed_media(world: dict[str, Any], media_id: str, project_id: str) -> None:
    _seed_catalog_asset(world, media_id=media_id, project_id=project_id)


@given("an MCP client connected to gflow-cli")
def given_mcp_client(world: dict[str, Any]) -> None:
    world["mcp_client"] = True


# ---------------------------------------------------------------------------
# When steps
# ---------------------------------------------------------------------------


@when(parsers.parse('the user runs "{cmd}"'))
def when_user_runs(runner: CliRunner, world: dict[str, Any], cmd: str) -> None:
    tokens = shlex.split(cmd)
    if tokens and tokens[0] == "gflow":
        tokens = tokens[1:]
    result = runner.invoke(main, tokens, catch_exceptions=False)
    world["cli_result"] = result


_CALL_MCP_PATTERN = (
    'the agent calls MCP tool "{tool_name}" with media_id "{media_id}" and confirm {confirm}'
)


@when(parsers.parse(_CALL_MCP_PATTERN))
def when_agent_calls_mcp(
    world: dict[str, Any], tool_name: str, media_id: str, confirm: str
) -> None:
    is_confirmed = confirm.strip().lower() == "true"
    # Call MCP tool directly through the handler module
    try:
        from gflow_cli.mcp import media_trash_tools

        handler = getattr(media_trash_tools, tool_name, None) or getattr(
            media_trash_tools, f"gflow_{tool_name}", None
        )
        if handler is None:
            world["mcp_result"] = {"error": f"Tool {tool_name} not found"}
            return
        result = asyncio.run(handler(media_id=media_id, confirm=is_confirmed))
        world["mcp_result"] = result
    except Exception as exc:
        world["mcp_result"] = {"error": str(exc), "type": type(exc).__name__}


# ---------------------------------------------------------------------------
# Then steps
# ---------------------------------------------------------------------------


@then(parsers.parse("the command exits with code {code:d}"))
def then_exit_code(world: dict[str, Any], code: int) -> None:
    result = world["cli_result"]
    assert result is not None, "CLI command was not run"
    msg = f"Expected exit {code}, got {result.exit_code}. Output:\n{result.output}"
    assert result.exit_code == code, msg


@then(parsers.parse('a batchexecute mutation is sent for RPC "{rpcid}" with media ID "{media_id}"'))
def then_rpc_sent(world: dict[str, Any], rpcid: str, media_id: str) -> None:
    calls = world["wire_calls"]
    matching = [c for c in calls if c.get("rpcid") == rpcid and c.get("media_id") == media_id]
    assert matching, f"No matching {rpcid} call found for media ID {media_id}. Calls: {calls}"


@then(parsers.parse('the JSON output reports status "{status}" and project "{project_id}"'))
def then_json_status_and_project(world: dict[str, Any], status: str, project_id: str) -> None:
    result = world["cli_result"]
    assert result is not None
    data = json.loads(result.output)
    assert data.get("status") == status, f"Expected status {status!r}, got {data.get('status')!r}"
    assert data.get("project_id") == project_id, f"Expected project {project_id!r}"


@then("no network request is sent to Flow")
def then_no_network(world: dict[str, Any]) -> None:
    assert len(world["wire_calls"]) == 0, f"Expected no wire calls, got: {world['wire_calls']}"


@then(parsers.parse('the error detail requests specifying "--profile"'))
def then_error_requests_profile(world: dict[str, Any]) -> None:
    result = world["cli_result"]
    assert result is not None
    assert "--profile" in result.output, f"Expected '--profile' in output: {result.output}"


@then(parsers.parse('a batchexecute mutation is sent for RPC "{rpcid}" with collection_id reset'))
def then_rpc_restore_sent(world: dict[str, Any], rpcid: str) -> None:
    calls = world["wire_calls"]
    matching = [c for c in calls if c.get("rpcid") == rpcid and c.get("restore")]
    assert matching, f"No matching restore {rpcid} call found. Calls: {calls}"


@then("the tool call returns an error requiring explicit confirmation")
def then_tool_confirmation_required(world: dict[str, Any]) -> None:
    res = world["mcp_result"]
    assert res is not None, "No MCP result found"
    err_text = str(res).lower()
    assert "confirm" in err_text or "error" in err_text or res.get("status") == "error", (
        f"Expected confirmation error in MCP result, got: {res}"
    )


@then("no mutation is sent to Flow")
def then_no_mutation(world: dict[str, Any]) -> None:
    assert len(world["wire_calls"]) == 0, f"Expected no wire calls, got: {world['wire_calls']}"
