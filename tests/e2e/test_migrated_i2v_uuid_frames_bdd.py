"""Free ($0) live BDD e2e for migrated `video i2v` in-project UUID frames (#1542).

Binds ``tests/features/migrated_i2v_uuid_frames.feature``. Selected by ``-m e2e_auth``.

**Cost: zero credits.** Both scenarios drive the real ``flow.google.com`` Frames picker
through ``migrated_existing_frames`` and abort the outgoing ``batchexecute`` submit
request via ``page.route`` before it leaves Chromium, asserting zero ``maseQ`` uploads
and verifying the intercepted submit body with ``_i2v_body_problem`` /
``_interpolation_body_problem``.

Requires ``GFLOW_CLI_E2E_PROFILE`` and ``GFLOW_CLI_E2E_PROJECT`` (a project whose local
catalog already holds at least two captioned images).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Route
from pytest_bdd import given, scenarios, then, when

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports import migrated_existing_frames as mef
from gflow_cli.api.transports._mc_common import (
    BATCHEXECUTE_GLOB,
    INTERPOLATION_SUBMIT_RPC,
    SUBMIT_RPCS,
    UPLOAD_RPC,
    _i2v_body_problem,
    _interpolation_body_problem,
    _ligature,
    body_rpcid,
    request_body,
    url_rpcid,
)
from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel
from gflow_cli.config import Settings
from gflow_cli.data.queries import list_images
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore

scenarios("../features/migrated_i2v_uuid_frames.feature")


def _find_two_captioned_images(
    profile_dir: Path, profile_name: str, project_id: str
) -> list[tuple[str, str]]:
    """Return two ``(flow_media_id, display_name)`` pairs catalogued in ``project_id``."""
    # Read-only lookup against the real profile's catalog (tests/conftest.py redirects
    # GFLOW_CLI_DB_PATH to an empty tmp dir for test isolation).
    db_path = profile_dir.parent / "gflow.db"
    if not db_path.is_file():
        return []
    found: list[tuple[str, str]] = []
    rows = list_images(db_path=db_path, profile=profile_name, limit=200, offset=0)
    with DataStore.open_readonly(db_path) as store:
        repo = DataRepository(store)
        for row in rows:
            if row.project_id != project_id or not row.media_id:
                continue
            asset = repo.get_asset_by_flow_media_id(profile_name, row.media_id)
            if asset is None:
                continue
            caption = asset.metadata_json.get("display_name")
            if isinstance(caption, str) and caption.strip():
                found.append((row.media_id, caption.strip()))
            if len(found) == 2:
                break
    return found


@given("an existing migrated Flow project with two catalogued images", target_fixture="world")
def _project_assets(e2e_profile_dir: Path) -> dict[str, Any]:
    profile_name = os.environ.get("GFLOW_CLI_E2E_PROFILE", "").strip()
    project_id = os.environ.get("GFLOW_CLI_E2E_PROJECT", "").strip()
    if not project_id:
        pytest.skip("GFLOW_CLI_E2E_PROJECT must name an existing migrated Flow project")
    assets = _find_two_captioned_images(e2e_profile_dir, profile_name, project_id)
    if len(assets) < 2:
        pytest.skip(
            f"Project {project_id} needs at least 2 catalogued captioned images for #1542 e2e"
        )
    return {
        "profile_dir": e2e_profile_dir,
        "project_id": project_id,
        "start_id": assets[0][0],
        "start_caption": assets[0][1],
        "end_id": assets[1][0],
        "end_caption": assets[1][1],
    }


async def _drive_aborted_submit(world: dict[str, Any], *, with_end: bool) -> None:
    project_id: str = world["project_id"]
    start_id: str = world["start_id"]
    start_caption: str = world["start_caption"]
    end_id: str | None = world["end_id"] if with_end else None
    end_caption: str = world["end_caption"] if with_end else ""

    request = GenerateVideoRequest(
        prompt="slow gentle camera push",
        mode=Mode.I2V,
        aspect=Aspect.PORTRAIT,
        model=VideoModel.VEO_3_1_LITE,
        start_image_ref_id=start_id,
        start_image_ref_display_name=start_caption,
        start_image_ref_in_project=True,
        end_image_ref_id=end_id,
        end_image_ref_display_name=end_caption,
        end_image_ref_in_project=bool(end_id),
    )

    uploads: list[str] = []
    intercepted: dict[str, Any] = {}

    async with FlowApiClient(
        profile_dir=world["profile_dir"],
        settings=Settings(flow_host="flow.google.com", ui_mode="classic"),
        transport="ui_automation",
    ) as client:
        page = client._page  # noqa: SLF001 - route-aborted live DOM + wire probe
        assert page is not None
        composer = MigratedComposer()
        surface = await composer.ensure_editor(page, project_id)
        if surface == "agent_only":
            pytest.skip("account served agent-only composer (#799); i2v needs classic composer")

        wanted = (start_id, end_id) if end_id else (start_id,)
        tokens = await mef.await_existing_frame_tokens(composer, page, project_id, wanted)
        await composer.apply_video_settings(page, request)
        await mef.attach_video_frames(composer, page, project_id, request, tokens)
        await composer.send_prompt(page, request.prompt, append=False)

        submit_seen = asyncio.Event()

        async def _guard_route(route: Route) -> None:
            req = route.request
            body = request_body(req)
            rpc = url_rpcid(req.url) or body_rpcid(body) or ""
            if rpc == UPLOAD_RPC:
                uploads.append(rpc)
            if rpc in SUBMIT_RPCS:
                # Judge the body here without storing raw f.req (which holds reCAPTCHA tokens).
                problem = (
                    _interpolation_body_problem(body, rpc, start_id, end_id)
                    if end_id is not None
                    else _i2v_body_problem(body, rpc, start_id)
                )
                intercepted["rpc"] = rpc
                intercepted["problem"] = problem
                submit_seen.set()
                with contextlib.suppress(Exception):
                    await route.abort("aborted")
                return
            with contextlib.suppress(Exception):
                await route.continue_()

        await page.route(BATCHEXECUTE_GLOB, _guard_route)
        try:
            submit_btn = page.locator("button").filter(has=_ligature(page, "arrow_forward")).first
            await composer._await_submit_enabled(  # noqa: SLF001
                page, submit_btn, disabled="submit button stayed disabled"
            )
            await composer._click(page, submit_btn, named="submit", timeout=5000)  # noqa: SLF001
            await asyncio.wait_for(submit_seen.wait(), timeout=15.0)
        finally:
            await page.unroute(BATCHEXECUTE_GLOB, _guard_route)

    world["uploads"] = uploads
    world["intercepted"] = intercepted


@when("the migrated composer binds the Start UUID frame and intercepts the submit")
def _run_start_only(world: dict[str, Any]) -> None:
    asyncio.run(_drive_aborted_submit(world, with_end=False))


@when("the migrated composer binds the Start and End UUID frames and intercepts the submit")
def _run_start_and_end(world: dict[str, Any]) -> None:
    asyncio.run(_drive_aborted_submit(world, with_end=True))


@then("zero maseQ uploads were sent")
def _assert_zero_uploads(world: dict[str, Any]) -> None:
    assert world["uploads"] == [], f"unexpected maseQ upload(s): {world['uploads']}"


@then("the intercepted eb1hJf submit body carries the Start media id")
def _assert_eb1hjf_body(world: dict[str, Any]) -> None:
    intercepted = world["intercepted"]
    assert intercepted.get("rpc") == "eb1hJf", intercepted
    assert intercepted.get("problem") is None, f"eb1hJf body rejected: {intercepted['problem']}"


@then("the intercepted nprQif submit body carries both Start and End media ids")
def _assert_nprqif_body(world: dict[str, Any]) -> None:
    intercepted = world["intercepted"]
    assert intercepted.get("rpc") == INTERPOLATION_SUBMIT_RPC, intercepted
    assert intercepted.get("problem") is None, f"nprQif body rejected: {intercepted['problem']}"
