"""Unit tests for video validation, upload, V2V edit DTO, jIps6 guard, and recorder."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from gflow_cli import json_output
from gflow_cli.api._flow_workspace import (
    MAX_VIDEO_BYTES,
    _is_supported_video_header,
    validate_video_file,
)
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports import _mc_upload_video as upload_mod
from gflow_cli.api.transports import migrated_video_edit as edit_mod
from gflow_cli.api.transports.base import VideoEditCapableTransport
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.video import (
    MAX_EDIT_REFERENCE_IMAGES,
    Aspect,
    EditVideoRequest,
    GenerateVideoRequest,
    Mode,
    VideoModel,
    VideoResult,
    VideoStarted,
    VideoStatus,
)
from gflow_cli.data.models import OperationKind, OperationStatus
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import (
    ConfigurationError,
    FlowHostMigratedError,
    WireFormatError,
)

_PID = "11111111-1111-1111-1111-111111111111"
_MID = "22222222-2222-2222-2222-222222222222"
_WF = "33333333-3333-3333-3333-333333333333"
_REF1 = "44444444-4444-4444-4444-444444444444"
_REF2 = "55555555-5555-5555-5555-555555555555"

_MP4_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
_MOV_MOOV_BYTES = b"\x00\x00\x00\x20moov\x00\x00\x00\x18mvhd"
_AVI_BYTES = b"RIFF\x24\x00\x00\x00AVI LIST"
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _make_freq(rpcid: str, inner: Any) -> str:
    outer = [[[rpcid, json.dumps(inner), None, "generic"]]]
    return f"f.req={json.dumps(outer)}"


# --- validate_video_file ----------------------------------------------------


def test_supported_video_header_checks() -> None:
    assert _is_supported_video_header(_MP4_BYTES) is True
    assert _is_supported_video_header(_MOV_MOOV_BYTES) is True
    assert _is_supported_video_header(_AVI_BYTES) is True
    assert _is_supported_video_header(b"\x00\x00\x00\x02moov\x00\x00\x00\x00") is False
    assert _is_supported_video_header(_PNG_BYTES) is False
    assert _is_supported_video_header(b"short") is False


@pytest.mark.asyncio
async def test_validate_video_file_accepts_valid_mp4_and_avi(tmp_path: Path) -> None:
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(_MP4_BYTES)
    await validate_video_file(mp4)

    mov = tmp_path / "clip.mov"
    mov.write_bytes(_MOV_MOOV_BYTES)
    await validate_video_file(mov)

    avi = tmp_path / "clip.avi"
    avi.write_bytes(_AVI_BYTES)
    await validate_video_file(avi)


@pytest.mark.asyncio
async def test_validate_video_file_rejects_bad_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(FileNotFoundError):
        await validate_video_file(tmp_path / "missing.mp4")

    bad_ext = tmp_path / "clip.mkv"
    bad_ext.write_bytes(_MP4_BYTES)
    with pytest.raises(ValueError, match="Unsupported video extension"):
        await validate_video_file(bad_ext)

    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        await validate_video_file(empty)

    fake_mp4 = tmp_path / "fake.mp4"
    fake_mp4.write_bytes(b"not a real mp4 header at all")
    with pytest.raises(ValueError, match="Not a supported video format"):
        await validate_video_file(fake_mp4)

    huge = tmp_path / "huge.mp4"
    huge.write_bytes(_MP4_BYTES)
    monkeypatch.setattr("gflow_cli.api._flow_workspace.MAX_VIDEO_BYTES", 8)
    with pytest.raises(ValueError, match="too large"):
        await validate_video_file(huge)
    assert MAX_VIDEO_BYTES == 1024**3


# --- EditVideoRequest DTO ---------------------------------------------------


def test_edit_video_request_validation(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(_MP4_BYTES)
    refs = tuple(tmp_path / f"r{i}.png" for i in range(MAX_EDIT_REFERENCE_IMAGES))

    req = EditVideoRequest(
        prompt="warm golden light",
        source_video=clip,
        reference_images=refs,
        model=VideoModel.OMNI_1_2_FLASH,
        aspect=Aspect.LANDSCAPE,
        resolution="720p",
        max_credits=15,
    )
    assert req.source_video == clip
    assert len(req.reference_images) == 5

    req_uuid = EditVideoRequest(prompt="add snow", source_media_id=_MID)
    assert req_uuid.source_media_id == _MID

    with pytest.raises(ValueError, match="empty"):
        EditVideoRequest(prompt="   ", source_video=clip)
    with pytest.raises(ValueError, match="exactly one"):
        EditVideoRequest(prompt="ok")
    with pytest.raises(ValueError, match="exactly one"):
        EditVideoRequest(prompt="ok", source_video=clip, source_media_id=_MID)
    with pytest.raises(ValueError, match="UUID"):
        EditVideoRequest(prompt="ok", source_media_id="not-a-uuid")
    with pytest.raises(ValueError, match="at most 5"):
        EditVideoRequest(
            prompt="ok",
            source_video=clip,
            reference_images=(*refs, tmp_path / "extra.png"),
        )
    with pytest.raises(ValueError, match="Omni"):
        EditVideoRequest(prompt="ok", source_video=clip, model=VideoModel.VEO_3_1_LITE)
    with pytest.raises(ValueError, match="SQUARE"):
        EditVideoRequest(prompt="ok", source_video=clip, aspect=Aspect.SQUARE)
    with pytest.raises(ValueError, match="resolution"):
        EditVideoRequest(prompt="ok", source_video=clip, resolution="1080p")
    with pytest.raises(ValueError, match="max_credits"):
        EditVideoRequest(prompt="ok", source_video=clip, max_credits=-1)


# --- _mc_upload_video -------------------------------------------------------


def test_parse_upload_reply() -> None:
    # REST mediaId == batchexecute rec[0] (workflow_id); REST media.workflowId == rec[2] (media_id)
    good = json.dumps({"mediaId": _WF, "media": {"workflowId": _MID}})
    assert upload_mod._parse_upload_reply(good, project_id=_PID) == (_MID, _WF)
    same_as_proj = json.dumps({"mediaId": _WF, "media": {"workflowId": _PID}})
    assert upload_mod._parse_upload_reply(same_as_proj, project_id=_PID) is None
    assert upload_mod._parse_upload_reply("", project_id=_PID) is None
    assert upload_mod._parse_upload_reply("{}", project_id=_PID) is None


@pytest.mark.asyncio
async def test_run_unique_name_hard_links_beside_the_source_and_cleans_up(tmp_path: Path) -> None:
    """Beside the source, so the link stays on the source's volume (no cross-drive copy)."""
    clip = tmp_path / "source.mp4"
    clip.write_bytes(_MP4_BYTES)
    async with upload_mod._run_unique_name(clip) as sent:
        assert sent != clip
        assert sent.parent.parent == tmp_path
        assert sent.stem.startswith("source")
        assert sent.suffix == ".mp4"
        assert sent.stat().st_nlink >= 2  # a hard link, not a copy
        tmp_dir = sent.parent
    assert not tmp_dir.exists()
    assert clip.read_bytes() == _MP4_BYTES


@pytest.mark.asyncio
async def test_run_unique_name_copies_only_when_no_hard_link_is_possible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = tmp_path / "source.mp4"
    clip.write_bytes(_MP4_BYTES)
    monkeypatch.setattr(upload_mod.os, "link", MagicMock(side_effect=OSError("no links")))
    async with upload_mod._run_unique_name(clip) as sent:
        assert sent != clip
        assert sent.read_bytes() == _MP4_BYTES
        assert sent.stat().st_nlink == 1


@pytest.mark.asyncio
async def test_run_unique_name_falls_back_to_the_plain_name_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = tmp_path / "source.mp4"
    clip.write_bytes(_MP4_BYTES)
    monkeypatch.setattr(
        upload_mod.tempfile, "mkdtemp", MagicMock(side_effect=PermissionError("read-only"))
    )
    warn = MagicMock()
    monkeypatch.setattr(upload_mod.log, "warning", warn)
    async with upload_mod._run_unique_name(clip) as sent:
        assert sent == clip
    warn.assert_called_once()
    assert warn.call_args.args[0] == "migrated.video_upload_plain_name"


@pytest.mark.asyncio
async def test_upload_video_for_mention_returns_both_ids_and_the_picker_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(_MP4_BYTES)
    sent: list[str] = []

    async def fake_toolbar_upload(_page: Any, files: Any, **_kw: Any) -> tuple[int, str]:
        sent.append(str(files))
        return 200, json.dumps({"mediaId": _WF, "media": {"workflowId": _MID}})

    monkeypatch.setattr(upload_mod, "toolbar_upload", fake_toolbar_upload)
    uploaded = await upload_mod.upload_video_for_mention(MagicMock(), _PID, clip)
    assert (uploaded.media_id, uploaded.workflow_id) == (_MID, _WF)
    assert uploaded.query_name == Path(sent[0]).stem
    # The extend path keeps its contract: the media id only, the file under its own name.
    assert await upload_mod.upload_video(MagicMock(), _PID, clip) == _MID
    assert sent[1] == str(clip)


def test_edit_request_problem_enforces_jips6_contract() -> None:
    refs_wire = [[None, _REF1], [None, _REF2]]
    valid_inner = [
        [[[None, _WF], None, "macaron_cookie_edit", 2, None, None, None, None, refs_wire]]
    ]
    body = _make_freq("jIps6", valid_inner)

    assert (
        edit_mod.batch_edit_request_problem(
            ["jIps6"],
            body,
            workflow_id=_WF,
            expected_resolution="720p",
            expected_aspect=Aspect.LANDSCAPE,
            expected_reference_ids=(_REF1, _REF2),
        )
        is None
    )

    # Wrong RPC id (dropped video -> t2v/r2v) refused at $0
    err = edit_mod.edit_request_problem("MZZa6b", body, workflow_id=_WF)
    assert isinstance(err, WireFormatError)
    assert "MZZa6b" in err.detail

    # Wrong workflow id
    err = edit_mod.edit_request_problem("jIps6", body, workflow_id=_PID)
    assert isinstance(err, WireFormatError)

    # Resolution mismatch (requested 360p, wire carries 720p key)
    err = edit_mod.edit_request_problem(
        "jIps6",
        body,
        workflow_id=_WF,
        expected_resolution="360p",
        expected_reference_ids=(_REF1, _REF2),
    )
    assert isinstance(err, WireFormatError)

    # Aspect mismatch in Clip Editor -> FlowHostMigratedError (exit 36)
    err = edit_mod.edit_request_problem(
        "jIps6",
        body,
        workflow_id=_WF,
        expected_aspect=Aspect.PORTRAIT,
        expected_reference_ids=(_REF1, _REF2),
        clip_editor=True,
    )
    assert isinstance(err, FlowHostMigratedError)

    # Missing reference image in entry[8]
    err = edit_mod.edit_request_problem(
        "jIps6",
        body,
        workflow_id=_WF,
        expected_reference_ids=(_REF1, _REF2, _PID),
    )
    assert isinstance(err, WireFormatError)
    assert "references did not match" in err.detail

    # Wrong Omni model family (abra_edit for Omni 1.2 Flash)
    abra_inner = [[[[None, _WF], None, "abra_edit", 2, None, None, None, None, refs_wire]]]
    err = edit_mod.edit_request_problem(
        "jIps6",
        _make_freq("jIps6", abra_inner),
        workflow_id=_WF,
        expected_model=VideoModel.OMNI_1_2_FLASH,
        expected_reference_ids=(_REF1, _REF2),
    )
    assert isinstance(err, WireFormatError)
    assert "macaron_cookie_edit" in err.detail

    # Multi-entry jIps6 batch (2 clips) refused before send
    two_entries = [
        [
            [[None, _WF], None, "macaron_cookie_edit", 2, None, None, None, None, refs_wire],
            [[None, _PID], None, "macaron_cookie_edit", 2, None, None, None, None, refs_wire],
        ]
    ]
    err = edit_mod.edit_request_problem("jIps6", _make_freq("jIps6", two_entries), workflow_id=_WF)
    assert isinstance(err, WireFormatError)
    assert "single-clip entry" in err.detail

    # Multi-frame jIps6 batch refused before send
    frame_entry = ["jIps6", json.dumps(valid_inner), None, "generic"]
    two_frames = f"f.req={json.dumps([[frame_entry, frame_entry]])}"
    err = edit_mod.edit_request_problem("jIps6", two_frames, workflow_id=_WF)
    assert isinstance(err, WireFormatError)
    assert "expected one jIps6 frame" in err.detail


@pytest.mark.asyncio
async def test_client_edit_video_validates_files_before_the_transport(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(_MP4_BYTES)
    not_a_video = tmp_path / "notes.mp4"
    not_a_video.write_bytes(b"plain text, not a container")

    class _FakeTransport:
        name = "fake"

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def edit_video(self, **kwargs: Any) -> VideoResult:
            self.calls.append(kwargs)
            return VideoResult(
                status=VideoStatus(media_id=_MID, status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
                local_path=clip,
                project_id=kwargs["project_id"],
                workflow_id=_WF,
                quoted_credits=9,
            )

    transport = _FakeTransport()
    assert isinstance(transport, VideoEditCapableTransport)
    client = FlowApiClient(profile_dir=tmp_path)
    client.transport = transport  # type: ignore[assignment]

    with pytest.raises(ConfigurationError, match="cannot upload this file") as info:
        await client.edit_video(
            req=EditVideoRequest(prompt="glow", source_video=not_a_video), project_id=_PID
        )
    assert info.value.charged == "no"
    assert transport.calls == []

    observer = MagicMock()
    res = await client.edit_video(
        req=EditVideoRequest(prompt="warm glow", source_video=clip),
        project_id=_PID,
        on_checkpoint=observer,
    )
    assert res.quoted_credits == 9
    # The checkpoints are the transport's to emit, next to the click; none before it.
    observer.assert_not_called()
    assert transport.calls[0]["project_id"] == _PID
    assert transport.calls[0]["on_checkpoint"] is observer


@pytest.mark.asyncio
async def test_ui_transport_edit_video_refuses_labs_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(_MP4_BYTES)
    monkeypatch.setenv("GFLOW_CLI_FLOW_HOST", "labs.google")
    from gflow_cli.config import reset_settings

    reset_settings()
    try:
        tr = UiAutomationTransport()
        tr._setup_done = True
        tr._page = MagicMock(url="https://labs.google/fx/tools/flow")
        with pytest.raises(FlowHostMigratedError, match="video edit"):
            await tr.edit_video(
                request=EditVideoRequest(prompt="glow", source_video=clip), project_id=_PID
            )
    finally:
        reset_settings()


def test_recorder_and_json_output_for_video_edit(tmp_path: Path) -> None:
    clip = tmp_path / "in.mp4"
    out_mp4 = tmp_path / "out.mp4"
    clip.write_bytes(_MP4_BYTES)
    out_mp4.write_bytes(_MP4_BYTES)

    store = DataStore.open(tmp_path / "gflow.db")
    try:
        repo = DataRepository(store)
        rec = OperationRecorder(repo, prompt_mode="store")
        req = EditVideoRequest(
            prompt="golden lantern light",
            source_video=clip,
            model=VideoModel.OMNI_1_2_FLASH,
            aspect=Aspect.LANDSCAPE,
            resolution="720p",
        )
        started = VideoStarted(media_id=_MID, project_id=_PID, workflow_id=_WF)
        rec.record_started_video(
            profile_name="p1", profile_dir=tmp_path, request=req, started=started
        )
        result = VideoResult(
            status=VideoStatus(media_id=_MID, status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
            local_path=out_mp4,
            project_id=_PID,
            workflow_id=_WF,
            quoted_credits=9,
        )
        rec.record_completed_video(
            profile_name="p1", _profile_dir=tmp_path, request=req, result=result
        )
        op = repo.get_operation_for_output_asset("p1", _MID, OperationKind.EDIT)
        assert op is not None
        assert op.status == OperationStatus.SUCCEEDED
        assert op.command == "video edit"

        payload = json_output.edit_video_result(command="video edit", request=req, result=result)
        assert payload["status"] == "ok"
        assert payload["quoted_credits"] == 9
        assert payload["request"]["mode"] == "edit"
        assert payload["request"]["resolution"] == "720p"

        gen_req = GenerateVideoRequest(prompt="test", mode=Mode.T2V, aspect=Aspect.LANDSCAPE)
        gen_payload = json_output.video_result(command="video t2v", request=gen_req, result=result)
        assert gen_payload["quoted_credits"] == 9
    finally:
        store.close()


def test_an_in_place_edit_keeps_the_source_row_and_the_earlier_edit(tmp_path: Path) -> None:
    """Flow can return an edit under the source's media id: the source's own length and
    seed stay, and a second, failed edit does not rewrite the first edit's operation."""
    out_mp4 = tmp_path / "out.mp4"
    out_mp4.write_bytes(_MP4_BYTES)
    store = DataStore.open(tmp_path / "gflow.db")
    try:
        repo = DataRepository(store)
        rec = OperationRecorder(repo, prompt_mode="store")
        source = GenerateVideoRequest(
            prompt="a lantern", mode=Mode.T2V, aspect=Aspect.LANDSCAPE, duration=8, seed=7
        )
        rec.record_started_video(
            profile_name="p1",
            profile_dir=tmp_path,
            request=source,
            started=VideoStarted(media_id=_MID, project_id=_PID, workflow_id=_WF),
        )
        done = VideoResult(
            status=VideoStatus(media_id=_MID, status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
            local_path=out_mp4,
            project_id=_PID,
            workflow_id=_WF,
        )
        rec.record_completed_video(
            profile_name="p1", _profile_dir=tmp_path, request=source, result=done
        )
        edit = EditVideoRequest(prompt="snow", source_media_id=_MID)
        rec.record_completed_video(
            profile_name="p1", _profile_dir=tmp_path, request=edit, result=done
        )
        first_edit = repo.get_operation_for_output_asset("p1", _MID, OperationKind.EDIT)
        assert first_edit is not None
        assert first_edit.status == OperationStatus.SUCCEEDED

        failed = VideoResult(
            status=VideoStatus(media_id=_MID, status="MEDIA_GENERATION_STATUS_FAILED"),
            local_path=None,
            project_id=_PID,
            workflow_id=_WF,
        )
        rec.record_completed_video(
            profile_name="p1", _profile_dir=tmp_path, request=edit, result=failed
        )
        row = store.conn.execute(
            "SELECT duration_seconds, seed, aspect_ratio FROM assets WHERE flow_media_id = ?",
            (_MID,),
        ).fetchone()
        assert tuple(row) == (8.0, 7, Aspect.LANDSCAPE.value)
        # A failed in-place edit leaves the source clip's own status alone.
        status = store.conn.execute(
            "SELECT status FROM assets WHERE flow_media_id = ?", (_MID,)
        ).fetchone()[0]
        assert status == "MEDIA_GENERATION_STATUS_SUCCESSFUL"
        statuses = [
            r[0]
            for r in store.conn.execute(
                "SELECT status FROM operations WHERE mode = ? ORDER BY started_at",
                (OperationKind.EDIT.value,),
            )
        ]
        assert OperationStatus.SUCCEEDED.value in statuses
        assert statuses.count(OperationStatus.SUCCEEDED.value) == 1
        assert len(statuses) == 2
    finally:
        store.close()


def test_a_failed_in_place_edit_is_recorded_even_after_an_earlier_failed_edit(
    tmp_path: Path,
) -> None:
    from gflow_cli.errors import TransportTimeoutError

    store = DataStore.open(tmp_path / "gflow.db")
    try:
        repo = DataRepository(store)
        rec = OperationRecorder(repo, prompt_mode="store")
        source = GenerateVideoRequest(prompt="a lantern", mode=Mode.T2V, aspect=Aspect.LANDSCAPE)
        rec.record_started_video(
            profile_name="p1",
            profile_dir=tmp_path,
            request=source,
            started=VideoStarted(media_id=_MID, project_id=_PID, workflow_id=_WF),
        )
        edit = EditVideoRequest(prompt="snow", source_media_id=_MID)
        failed = VideoResult(
            status=VideoStatus(media_id=_MID, status="MEDIA_GENERATION_STATUS_FAILED"),
            local_path=None,
            project_id=_PID,
            workflow_id=_WF,
        )
        rec.record_completed_video(
            profile_name="p1", _profile_dir=tmp_path, request=edit, result=failed
        )
        for _ in range(2):
            rec.record_failed_operation(
                profile_name="p1",
                profile_dir=tmp_path,
                command="video edit",
                mode=OperationKind.EDIT,
                exc=TransportTimeoutError(detail="no reply"),
                request=edit,
                flow_media_ids=[_MID],
            )
        count = store.conn.execute(
            "SELECT COUNT(*) FROM operations WHERE mode = ? AND status = ?",
            (OperationKind.EDIT.value, OperationStatus.FAILED.value),
        ).fetchone()[0]
        assert count == 3  # the earlier failed edit, and each of this run's failures
    finally:
        store.close()


@pytest.mark.asyncio
async def test_an_interrupted_copy_leaves_no_staging_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    clip = tmp_path / "source.mp4"
    clip.write_bytes(_MP4_BYTES)
    monkeypatch.setattr(upload_mod.os, "link", MagicMock(side_effect=OSError("no links")))

    def partial_copy(_src: Path, dst: Path) -> None:
        Path(dst).write_bytes(b"part")
        raise KeyboardInterrupt

    monkeypatch.setattr(upload_mod.shutil, "copyfile", partial_copy)
    with pytest.raises((KeyboardInterrupt, asyncio.CancelledError)):
        async with upload_mod._run_unique_name(clip):
            pass
    assert [p.name for p in tmp_path.iterdir()] == ["source.mp4"]
