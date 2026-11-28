"""E2E test for media trash and restore over the wire (#1363).

Binds ``tests/features/media_trash_live.feature`` (@e2e @e2e_image).
Verifies that trashing and restoring an item on flow.google.com via pGCYOe
spends zero credits, updates the project listing accordingly, and restores cleanly
in a finally block.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli._cli_helpers import client_visibility
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports._mc_submit_observe import read_project_listing
from gflow_cli.cli import main
from gflow_cli.config import get_settings
from gflow_cli.mcp import media_trash_tools
from gflow_cli.services import credits
from gflow_cli.services.media_trash import restore_media_service

scenarios("../features/media_trash_live.feature")


def _walk_lists(node: object) -> list[list[Any]]:
    """Walk nested lists depth-first."""
    if not isinstance(node, list):
        return []
    result: list[list[Any]] = [node]
    for item in node:
        result.extend(_walk_lists(item))
    return result


def cleanup_e2e_media_trash(
    state: dict[str, Any],
    *,
    restore_fn: Callable[..., Any] = restore_media_service,
) -> None:
    """Restore trashed media in e2e cleanup, failing loudly if restore fails."""
    if not (state.get("needs_restore") and state.get("media_id") and state.get("project_id")):
        return

    media_id = str(state["media_id"])
    project_id = str(state["project_id"])
    profile = state.get("profile")

    restore_cmd = f"gflow media restore {media_id} --project {project_id}"
    if profile:
        restore_cmd += f" --profile {profile}"

    try:
        coro = restore_fn(
            target_id=media_id,
            project_id=project_id,
            profile=profile,
        )
        if inspect.iscoroutine(coro):
            asyncio.run(coro)
    except Exception as exc:  # noqa: BLE001
        msg = (
            f"\n[E2E CLEANUP FAILURE] Failed to restore media {media_id}: {exc}\n"
            f"Please restore manually: {restore_cmd}\n"
        )
        print(msg, file=sys.stderr)
        print(msg)
        raise


@pytest.fixture
def world(e2e_profile_dir: Path) -> Iterator[dict[str, Any]]:
    profile = e2e_profile_dir.name.removeprefix("profile_")
    state: dict[str, Any] = {
        "profile": profile,
        "profile_dir": e2e_profile_dir,
        "needs_restore": False,
        "media_id": None,
        "project_id": None,
        "credits_initial": None,
    }
    try:
        yield state
    finally:
        # Restore in a finally block through restore_media_service (direct, then browser)
        cleanup_e2e_media_trash(state)


async def _assert_media_listing_state(
    profile_dir: Path, project_id: str, media_id: str, *, archived: bool
) -> None:
    settings = get_settings()
    async with FlowApiClient(
        profile_dir=profile_dir, visibility=client_visibility(settings)
    ) as client:
        page = client._page
        assert page is not None
        listing = await read_project_listing(page, project_id, budget_s=30.0)
        assert listing is not None, "Failed to read Zzl0ze project listing from Flow"

        found_entry: list[Any] | None = None
        if isinstance(listing, list) and len(listing) > 1 and isinstance(listing[1], list):
            for entry in listing[1]:
                if isinstance(entry, list) and len(entry) > 0 and entry[0] == media_id:
                    found_entry = entry
                    break
            # An image's entry is keyed by the id gflow records as its workflow id; the
            # media id gflow reports sits at entry[3][4] (#1451).
            for entry in listing[1] if found_entry is None else []:
                meta = entry[3] if isinstance(entry, list) and len(entry) > 3 else None
                if isinstance(meta, list) and len(meta) > 4 and meta[4] == media_id:
                    found_entry = entry
                    break
        if found_entry is None and isinstance(listing, list):
            for item in _walk_lists(listing):
                if len(item) > 3 and item[0] == media_id and isinstance(item[3], list):
                    found_entry = item
                    break

        if archived:
            is_archived = found_entry is None or (
                len(found_entry) > 3
                and isinstance(found_entry[3], list)
                and len(found_entry[3]) > 2
                and found_entry[3][2] in (True, 1)
            )
            assert is_archived, (
                f"Media {media_id} was expected to be archived in Zzl0ze: {found_entry}"
            )
        else:
            assert found_entry is not None, (
                f"Media {media_id} was expected in Zzl0ze listing after restore, but was not found"
            )
            has_meta = (
                len(found_entry) > 3
                and isinstance(found_entry[3], list)
                and len(found_entry[3]) > 2
            )
            if has_meta:
                assert found_entry[3][2] not in (True, 1), (
                    f"Media {media_id} still has archived flag in Zzl0ze: {found_entry}"
                )


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
    env_media = os.environ.get("GFLOW_CLI_E2E_MEDIA_ID", "").strip()
    env_project = os.environ.get("GFLOW_CLI_E2E_PROJECT", "").strip()
    if not env_media or not env_project:
        pytest.skip(
            "GFLOW_CLI_E2E_MEDIA_ID and GFLOW_CLI_E2E_PROJECT must be set to run media trash e2e "
            "(prevents touching arbitrary real items in live account)"
        )

    world["media_id"] = env_media
    world["project_id"] = env_project


@given('an image generated by "gflow image t2i --json" in the e2e project')
def _generated_image(world: dict[str, Any], tmp_path: Path) -> None:
    project = os.environ.get("GFLOW_CLI_E2E_PROJECT", "").strip()
    if not project:
        pytest.skip("GFLOW_CLI_E2E_PROJECT must be set: the image is generated in that project")
    result = CliRunner().invoke(
        main,
        [
            "image",
            "t2i",
            "a single blue ceramic mug on a plain white table",
            "--model",
            "nano2",
            "--project",
            project,
            "--out",
            str(tmp_path / "out"),
            "--json",
            "--profile",
            world["profile"],
        ],
    )
    assert result.exit_code == 0, f"image t2i failed ({result.exit_code}): {result.output[-400:]}"
    images = json.loads(result.stdout).get("images") or []
    assert images, f"image t2i returned no images: {result.output[-400:]}"
    # The id gflow reports for the image, which the user passes to media trash.
    world["media_id"] = str(images[0]["media_name"])
    world["project_id"] = project


@when('I run "gflow media trash <media_id> --yes --json" without --project')
def _run_trash_cli_by_catalog(world: dict[str, Any]) -> None:
    result = CliRunner().invoke(
        main,
        ["media", "trash", world["media_id"], "--yes", "--json", "--profile", world["profile"]],
    )
    world["cli_result"] = result
    if result.exit_code == 0:
        world["needs_restore"] = True


@when('I run "gflow media restore <media_id> --json" without --project')
def _run_restore_cli_by_catalog(world: dict[str, Any]) -> None:
    result = CliRunner().invoke(
        main,
        ["media", "restore", world["media_id"], "--json", "--profile", world["profile"]],
    )
    world["cli_result"] = result
    if result.exit_code == 0:
        world["needs_restore"] = False


@when('I run "gflow media trash <media_id> --yes --json"')
def _run_trash_cli(world: dict[str, Any]) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "media",
            "trash",
            world["media_id"],
            "--project",
            world["project_id"],
            "--yes",
            "--json",
            "--profile",
            world["profile"],
        ],
    )
    world["cli_result"] = result
    if result.exit_code == 0:
        world["needs_restore"] = True


@when('I run "gflow media restore <media_id> --json"')
def _run_restore_cli(world: dict[str, Any]) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "media",
            "restore",
            world["media_id"],
            "--project",
            world["project_id"],
            "--json",
            "--profile",
            world["profile"],
        ],
    )
    world["cli_result"] = result
    if result.exit_code == 0:
        world["needs_restore"] = False


@when('I run "gflow media trash <media_id> --yes --json" forcing browser fallback')
def _run_trash_cli_browser(world: dict[str, Any]) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "media",
            "trash",
            world["media_id"],
            "--project",
            world["project_id"],
            "--yes",
            "--json",
            "--profile",
            world["profile"],
        ],
        env={"GFLOW_CLI_WIRE_READS": "0"},
    )
    world["cli_result"] = result
    if result.exit_code == 0:
        world["needs_restore"] = True


@when('I run "gflow media restore <media_id> --json" forcing browser fallback')
def _run_restore_cli_browser(world: dict[str, Any]) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "media",
            "restore",
            world["media_id"],
            "--project",
            world["project_id"],
            "--json",
            "--profile",
            world["profile"],
        ],
        env={"GFLOW_CLI_WIRE_READS": "0"},
    )
    world["cli_result"] = result
    if result.exit_code == 0:
        world["needs_restore"] = False


@then("the command exits with code 0")
def _exit_zero(world: dict[str, Any]) -> None:
    result = world.get("cli_result")
    assert result is not None
    assert result.exit_code == 0, f"Command failed with {result.exit_code}: {result.output}"


@then(parsers.parse('the JSON output reports status "{expected_status}"'))
def _json_status(world: dict[str, Any], expected_status: str) -> None:
    result = world.get("cli_result")
    assert result is not None
    data = json.loads(result.stdout)
    assert data["status"] == expected_status
    assert data["media_id"] == world["media_id"]
    if expected_status == "trashed":
        assert data.get("archived") is True
    elif expected_status == "restored":
        assert data.get("archived") is False


@when('I invoke MCP tool "gflow_media_trash" with confirm true')
def _invoke_mcp_trash(world: dict[str, Any]) -> None:
    async def _call() -> dict[str, Any]:
        return await media_trash_tools.gflow_media_trash(
            media_id=world["media_id"],
            project_id=world["project_id"],
            profile=world["profile"],
            confirm=True,
        )

    res = asyncio.run(_call())
    world["mcp_result"] = res
    if res.get("status") == "trashed":
        world["needs_restore"] = True


@when('I invoke MCP tool "gflow_media_restore"')
def _invoke_mcp_restore(world: dict[str, Any]) -> None:
    async def _call() -> dict[str, Any]:
        return await media_trash_tools.gflow_media_restore(
            media_id=world["media_id"],
            project_id=world["project_id"],
            profile=world["profile"],
        )

    res = asyncio.run(_call())
    world["mcp_result"] = res
    if res.get("status") == "restored":
        world["needs_restore"] = False


@then(parsers.parse('the MCP response reports status "{expected_status}"'))
def _mcp_status(world: dict[str, Any], expected_status: str) -> None:
    res = world.get("mcp_result")
    assert res is not None
    assert res.get("status") == expected_status, res
    assert res.get("media_id") == world["media_id"]
    if expected_status == "trashed":
        assert res.get("archived") is True
    elif expected_status == "restored":
        assert res.get("archived") is False


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
        data = json.loads(world["cli_result"].stdout)
        assert data.get("charged") == "no"
    if "mcp_result" in world and world["mcp_result"] is not None:
        assert world["mcp_result"].get("charged") == "no"

    profile = world.get("profile")
    if profile and world.get("credits_initial") is not None:
        balance_after = asyncio.run(credits.inspect_profile(profile)).get("balance")
        assert balance_after == world["credits_initial"], (
            f"Credits balance changed: initial={world['credits_initial']}, after={balance_after}"
        )


# ---------------------------------------------------------------------------
# Offline tests for cleanup helper (ensures no signature drift)
# ---------------------------------------------------------------------------


def test_cleanup_e2e_media_trash_call_shape_with_fake_service() -> None:
    calls: list[dict[str, Any]] = []

    async def fake_restore(
        target_id: str, *, project_id: str | None = None, profile: str | None = None
    ) -> Any:
        calls.append({"target_id": target_id, "project_id": project_id, "profile": profile})

    state = {
        "needs_restore": True,
        "media_id": "media-test-123",
        "project_id": "proj-test-456",
        "profile": "prof-test",
    }
    cleanup_e2e_media_trash(state, restore_fn=fake_restore)
    assert calls == [
        {
            "target_id": "media-test-123",
            "project_id": "proj-test-456",
            "profile": "prof-test",
        }
    ]


def test_cleanup_e2e_media_trash_signature_matches_restore_media_service() -> None:
    sig = inspect.signature(restore_media_service)
    bound = sig.bind(
        target_id="media-test-123",
        project_id="proj-test-456",
        profile="prof-test",
    )
    assert bound.arguments["target_id"] == "media-test-123"
    assert bound.arguments["project_id"] == "proj-test-456"
    assert bound.arguments["profile"] == "prof-test"


def test_cleanup_e2e_media_trash_loud_on_failure(capsys: pytest.CaptureFixture[str]) -> None:
    async def failing_restore(
        target_id: str, *, project_id: str | None = None, profile: str | None = None
    ) -> Any:
        raise RuntimeError("Network failure restoring media")

    state = {
        "needs_restore": True,
        "media_id": "loud-media-999",
        "project_id": "loud-proj-888",
        "profile": "loud-prof-777",
    }
    with pytest.raises(RuntimeError, match="Network failure restoring media"):
        cleanup_e2e_media_trash(state, restore_fn=failing_restore)

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "[E2E CLEANUP FAILURE]" in output
    assert "loud-media-999" in output
    assert (
        "gflow media restore loud-media-999 --project loud-proj-888 --profile loud-prof-777"
        in output
    )
