"""E2E: ``gflow video edit`` (video-to-video, ``jIps6``) on flow.google.com.

Four $0 ``e2e_auth`` tests and one paid ``e2e_video`` test:

* the zero cap through the CLI — ``--max-credits 0`` on a clip in the project exits 45 with
  ``charged: no`` and no submit click (Flow quotes every edit above 0 credits);
* the zero cap through the MCP twin ``gflow_edit_video`` — the tool enqueues, the in-process
  worker decodes the payload and drives the same transport, so a key the worker never reads
  or a codec that drops ``max_credits`` would show here and nowhere else;
* the request contract, at $0, on both paths — the production driver
  (``migrated_video_edit.run_video_edit``) prepares the edit of a project clip (clip editor)
  and of an uploaded local copy of it (main composer) with one ``--ref``; the checked
  ``jIps6`` must target the source's workflow, carry an Omni edit model key and exactly the
  uploaded reference id in ``entry[8]``, and is then aborted before Flow sees it. The uploads
  are free and stay in the project;
* ``e2e_video`` (SPENDS VEO CREDITS, opt in with ``GFLOW_CLI_E2E_RUN_VIDEO=1`` and only with
  the account owner's consent): one real edit of the project clip through the CLI.

Belt and braces: a context-level route aborts every ``jIps6`` for the whole of each $0 test,
so even a fall-through in the driver cannot bill.

Env: ``GFLOW_CLI_E2E_PROFILE`` (profile served flow.google.com), ``GFLOW_CLI_E2E_PROJECT``,
``GFLOW_CLI_E2E_EXTEND_MEDIA`` (a finished video in that project; the extend tests use the
same clip).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.api.transports import migrated_composer
from gflow_cli.api.transports import migrated_video_edit as ve
from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.video import EditVideoRequest
from gflow_cli.config import reset_settings
from gflow_cli.errors import GFlowError

from .test_video_extend_migrated_e2e import _ids, _ref_png, _source_clip_file

pytestmark = pytest.mark.e2e

_PROMPT = "make it a snowy winter evening, same framing"
#: The page mints its own reCAPTCHA token before ``jIps6``; the observer caps the wait anyway.
_SUBMIT_WAIT_S = 60.0


def _decoded_edits(request: Any) -> list[Any]:
    body = migrated_composer.request_body(request)
    return ve._decode_rpc_frames(ve.EDIT_SUBMIT_RPC, body)  # noqa: SLF001


async def _live_page(profile: Path, captured: list[Any]) -> tuple[UiAutomationTransport, Any]:
    """A live page whose context aborts (and records) every ``jIps6``, checked or not."""

    async def block_edit(route: Any, request: Any) -> None:
        edits = _decoded_edits(request)
        if edits:
            captured.extend(edits)
            await route.abort()
            return
        await route.fallback()

    transport = UiAutomationTransport()
    await transport.setup(profile)
    page = transport._page  # noqa: SLF001 - the e2e reads the live page
    assert page is not None
    await page.context.route("**/batchexecute*", block_edit)
    return transport, page


def _isolate(e2e_env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """The real authenticated home, with this test's own catalog and output directory."""
    for key in ("GFLOW_CLI_DB_PATH", "GFLOW_CLI_OUTPUT_DIR"):
        monkeypatch.setenv(key, e2e_env[key])
    monkeypatch.delenv("GFLOW_CLI_FLOW_HOST", raising=False)
    monkeypatch.delenv("GFLOW_CLI_MAX_CREDITS", raising=False)
    reset_settings()


@pytest.mark.e2e_auth
def test_e2e_cli_edit_with_a_zero_cap_is_exit_45_and_nothing_is_submitted(
    e2e_env: dict[str, str],
) -> None:
    from .test_image_uuid_ref_e2e import _events, _run_gflow

    project, media = _ids()
    proc = _run_gflow(
        ["video", "edit", media, _PROMPT, "--project", project, "--max-credits", "0", "--json"],
        e2e_env,
    )
    assert proc.returncode == 45, proc.stderr[-800:]
    error = json.loads(proc.stdout)["error"]
    assert error["charged"] == "no", error
    assert "migrated.submit_clicked" not in _events(proc.stderr)


@pytest.mark.asyncio
@pytest.mark.e2e_auth
async def test_e2e_mcp_edit_with_a_zero_cap_is_refused_before_any_submit(
    e2e_env: dict[str, str], e2e_profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del e2e_profile_dir  # selects the real authenticated gflow home
    from gflow_cli.mcp import video_edit_tools

    project, media = _ids()
    _isolate(e2e_env, monkeypatch)
    submits: list[Any] = []

    async def never_submit(_self: Any, _page: Any, **kwargs: Any) -> Any:
        submits.append(kwargs)
        raise AssertionError("the zero cap must refuse before the submit is clicked")

    monkeypatch.setattr(MigratedComposer, "submit_and_observe", never_submit)
    result = await video_edit_tools.gflow_edit_video(
        source=media,
        prompt=_PROMPT,
        project=project,
        max_credits=0,
        profile=e2e_env["GFLOW_CLI_PROFILE"],
        wait=True,
    )

    assert result["status"] == "failed", result
    assert result["error"]["exit_code"] == 45, result
    assert result["error"]["charged"] == "no", result
    assert submits == []


@pytest.mark.asyncio
@pytest.mark.e2e_auth
@pytest.mark.parametrize("source", ["clip_editor", "local_file"])
async def test_e2e_edit_request_targets_the_source_and_carries_the_ref_at_zero_cost(
    source: str, e2e_profile_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, media = _ids()
    monkeypatch.delenv("GFLOW_CLI_FLOW_HOST", raising=False)
    reset_settings()
    ref = _ref_png(tmp_path / "ref.png")
    if source == "local_file":
        clip = await _source_clip_file(e2e_profile_dir, tmp_path / "source.mp4")
        request = EditVideoRequest(prompt=_PROMPT, source_video=clip, reference_images=(ref,))
    else:
        request = EditVideoRequest(prompt=_PROMPT, source_media_id=media, reference_images=(ref,))

    seen: dict[str, Any] = {"workflow": None, "refs": []}
    real_require, real_upload_mention = ve.require_video_source, ve.upload_video_for_mention
    real_upload = MigratedComposer.upload

    def spy_require(clip: Any, **kw: Any) -> str:
        seen["workflow"] = real_require(clip, **kw)
        return seen["workflow"]

    async def spy_upload_mention(*args: Any, **kw: Any) -> Any:
        uploaded = await real_upload_mention(*args, **kw)
        seen["workflow"] = uploaded.workflow_id
        return uploaded

    async def spy_upload(self: Any, *args: Any, **kw: Any) -> Any:
        ref_id, name = await real_upload(self, *args, **kw)
        seen["refs"].append(ref_id)
        return ref_id, name

    monkeypatch.setattr(ve, "require_video_source", spy_require)
    monkeypatch.setattr(ve, "upload_video_for_mention", spy_upload_mention)
    monkeypatch.setattr(MigratedComposer, "upload", spy_upload)
    captured: list[Any] = []
    transport, page = await _live_page(e2e_profile_dir, captured)
    try:
        with pytest.raises(GFlowError):  # the aborted submit never gets a reply
            await ve.run_video_edit(
                page,
                request,
                project_id=project,
                save=lambda *_a, **_k: None,  # type: ignore[arg-type, return-value]
                poll_timeout_s=_SUBMIT_WAIT_S,
                download=False,
                on_started=None,
            )
    finally:
        await transport.teardown()

    # Exactly one jIps6 left the page, and the production check let it through: it targets
    # the source's workflow, carries an Omni edit key and exactly the uploaded reference.
    assert len(captured) == 1, captured
    entry = ve._at(captured[0], 0, 0)  # noqa: SLF001
    assert ve._at(entry, 0, 1) == seen["workflow"], (entry, seen)  # noqa: SLF001
    assert "_edit" in str(ve._at(entry, 2)), entry  # noqa: SLF001
    assert ve._extract_reference_ids(entry) == set(seen["refs"]), (entry, seen)  # noqa: SLF001
    assert len(seen["refs"]) == 1, seen


@pytest.mark.e2e_video
def test_e2e_one_paid_edit_of_a_project_clip(e2e_env: dict[str, str]) -> None:
    """SPENDS VEO CREDITS. One edit of the project clip through the CLI; the output is an mp4
    and the catalog lists it."""
    if os.environ.get("GFLOW_CLI_E2E_RUN_VIDEO") != "1":
        pytest.skip("spends Veo credits — set GFLOW_CLI_E2E_RUN_VIDEO=1 (owner consent)")
    from gflow_cli.data.repository import DataRepository
    from gflow_cli.data.store import DataStore

    from .test_image_uuid_ref_e2e import _run_gflow

    project, media = _ids()
    proc = _run_gflow(["video", "edit", media, _PROMPT, "--project", project, "--json"], e2e_env)
    assert proc.returncode == 0, proc.stderr[-800:]
    payload = json.loads(proc.stdout)
    assert Path(payload["local_path"]).read_bytes()[4:8] == b"ftyp", payload
    with DataStore.open(Path(e2e_env["GFLOW_CLI_DB_PATH"])) as store:
        row = DataRepository(store).get_asset_by_flow_media_id(
            e2e_env["GFLOW_CLI_PROFILE"], payload["media_id"]
        )
    assert row is not None, payload
