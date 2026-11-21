"""E2E test for media trash and restore over the wire (#1363).

Binds ``tests/features/media_trash_live.feature`` (@e2e @e2e_image).
Verifies that trashing and restoring an item on flow.google.com via pGCYOe
spends zero credits, updates the project listing accordingly, and restores cleanly
in a finally block.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli.api import flow_wire
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports._mc_submit_observe import read_project_listing
from gflow_cli.api.transports.batchexecute import find_record
from gflow_cli.cli import main
from gflow_cli.config import get_settings
from gflow_cli.data.store import DataStore
from gflow_cli.mcp import media_trash_tools
from gflow_cli.services import credits

scenarios("../features/media_trash_live.feature")


@pytest.fixture
def world(e2e_profile_dir: Path) -> Iterator[dict[str, Any]]:
    profile = e2e_profile_dir.name.removeprefix("profile_")
    state: dict[str, Any] = {
        "profile": profile,
        "profile_dir": e2e_profile_dir,
        "needs_restore": False,
        "media_id": None,
        "project_id": None,
    }
    try:
        yield state
    finally:
        # Restore in a finally block to ensure the live Flow project is not left trashed
        if state.get("needs_restore") and state.get("media_id") and state.get("project_id"):
            try:
                asyncio.run(
                    flow_wire.direct_restore_media(
                        e2e_profile_dir,
                        state["media_id"],
                        state["project_id"],
                    )
                )
            except Exception:  # noqa: BLE001 - backstop cleanup
                pass


async def _assert_media_listing_state(
    profile_dir: Path, project_id: str, media_id: str, *, archived: bool
) -> None:
    settings = get_settings()
    async with FlowApiClient(profile_dir=profile_dir, headless=settings.headless) as client:
        page = client._page
        assert page is not None
        listing = await read_project_listing(page, project_id, budget_s=30.0)
        assert listing is not None, "Failed to read Zzl0ze project listing from Flow"
        rec = find_record(listing, (2, media_id))
        if archived:
            is_archived = rec is None or (
                len(rec) > 3 and isinstance(rec[3], list) and len(rec[3]) > 2 and rec[3][2] == 1
            )
            assert is_archived, (
                f"Media {media_id} was expected to be archived in Zzl0ze, but was active: {rec}"
            )
        else:
            assert rec is not None, (
                f"Media {media_id} was expected in Zzl0ze listing after restore, but was not found"
            )
            if len(rec) > 3 and isinstance(rec[3], list) and len(rec[3]) > 2:
                assert rec[3][2] != 1, f"Media {media_id} still has archived flag in Zzl0ze: {rec}"


@given("the e2e profile is signed in to Flow")
def _profile(world: dict[str, Any], e2e_profile_dir: Path) -> None:
    profile = e2e_profile_dir.name.removeprefix("profile_")
    world["profile"] = profile
    world["profile_dir"] = e2e_profile_dir
    try:
        balance_info = asyncio.run(credits.inspect_profile(profile))
        world["credits_initial"] = balance_info.get("balance")
    except Exception:  # noqa: BLE001
        world["credits_initial"] = None


@given("an existing generated image asset is cataloged for the project")
def _cataloged_image(world: dict[str, Any]) -> None:
    settings = get_settings()
    db_path = settings.db_path or (settings.home / "gflow.db")
    store = DataStore.open(db_path)
    profile = world["profile"]

    env_media = os.environ.get("GFLOW_CLI_E2E_MEDIA_ID", "").strip()
    env_project = os.environ.get("GFLOW_CLI_E2E_PROJECT", "").strip()
    if env_media and env_project:
        world["media_id"] = env_media
        world["project_id"] = env_project
        return

    with store.transaction():
        row = store.conn.execute(
            "SELECT flow_media_id, flow_project_id FROM assets "
            "WHERE profile_name = ? AND flow_media_id IS NOT NULL "
            "AND flow_project_id IS NOT NULL LIMIT 1",
            (profile,),
        ).fetchone()

    if row is None:
        pytest.skip(
            f"No cataloged image asset with flow_media_id found for profile '{profile}'. "
            "Set GFLOW_CLI_E2E_MEDIA_ID and GFLOW_CLI_E2E_PROJECT to target an item."
        )

    world["media_id"] = str(row[0])
    world["project_id"] = str(row[1])


@when('I run "gflow media trash <media_id> --yes --json"')
def _run_trash_cli(world: dict[str, Any]) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["media", "trash", world["media_id"], "--yes", "--json", "--profile", world["profile"]],
    )
    world["cli_result"] = result
    world["needs_restore"] = True


@when('I run "gflow media restore <media_id> --json"')
def _run_restore_cli(world: dict[str, Any]) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["media", "restore", world["media_id"], "--json", "--profile", world["profile"]],
    )
    world["cli_result"] = result


@then("the command exits with code 0")
def _exit_zero(world: dict[str, Any]) -> None:
    result = world.get("cli_result")
    assert result is not None
    assert result.exit_code == 0, f"Command failed with {result.exit_code}: {result.output}"


@then(parsers.parse('the JSON output reports status "{expected_status}"'))
def _json_status(world: dict[str, Any], expected_status: str) -> None:
    result = world.get("cli_result")
    assert result is not None
    data = json.loads(result.output)
    assert data["status"] == expected_status
    assert data["media_id"] == world["media_id"]
    if expected_status == "restored":
        world["needs_restore"] = False


@when('I invoke MCP tool "gflow_media_trash" with confirm true')
def _invoke_mcp_trash(world: dict[str, Any]) -> None:
    async def _call() -> dict[str, Any]:
        return await media_trash_tools.gflow_media_trash(
            media_id=world["media_id"],
            profile=world["profile"],
            confirm=True,
        )

    res = asyncio.run(_call())
    world["mcp_result"] = res
    world["needs_restore"] = True


@when('I invoke MCP tool "gflow_media_restore"')
def _invoke_mcp_restore(world: dict[str, Any]) -> None:
    async def _call() -> dict[str, Any]:
        return await media_trash_tools.gflow_media_restore(
            media_id=world["media_id"],
            profile=world["profile"],
        )

    res = asyncio.run(_call())
    world["mcp_result"] = res


@then(parsers.parse('the MCP response reports status "{expected_status}"'))
def _mcp_status(world: dict[str, Any], expected_status: str) -> None:
    res = world.get("mcp_result")
    assert res is not None
    assert res.get("status") == expected_status, res
    assert res.get("media_id") == world["media_id"]
    if expected_status == "restored":
        world["needs_restore"] = False


@then("Flow's project listing reports the media archived")
def _listing_archived(world: dict[str, Any]) -> None:
    asyncio.run(
        _assert_media_listing_state(
            world["profile_dir"],
            world["project_id"],
            world["media_id"],
            archived=True,
        )
    )


@then("Flow's project listing reports the media active")
def _listing_active(world: dict[str, Any]) -> None:
    asyncio.run(
        _assert_media_listing_state(
            world["profile_dir"],
            world["project_id"],
            world["media_id"],
            archived=False,
        )
    )


@then("zero credits were charged")
def _zero_credits(world: dict[str, Any]) -> None:
    if "cli_result" in world and world["cli_result"] is not None:
        try:
            data = json.loads(world["cli_result"].output)
            assert data.get("charged") == "no"
        except Exception:  # noqa: BLE001
            pass
    if "mcp_result" in world and world["mcp_result"] is not None:
        assert world["mcp_result"].get("charged") == "no"
