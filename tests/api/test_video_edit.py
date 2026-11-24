"""Unit tests for video validation, upload, V2V edit DTO, jIps6 guard, and recorder."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

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
    MediaUploadRejectedError,
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
async def test_staged_video_path_creates_and_cleans_private_dir(tmp_path: Path) -> None:
    clip = tmp_path / "source.mp4"
    clip.write_bytes(_MP4_BYTES)
    async with upload_mod._staged_video_path(clip, "source-deadbeef.mp4") as staged:
        assert staged.is_file()
        assert staged.name == "source-deadbeef.mp4"
        assert staged.read_bytes() == _MP4_BYTES
        tmp_dir = staged.parent
    assert not tmp_dir.exists()


@pytest.mark.asyncio
async def test_upload_video_two_post_and_rights_dialog_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(_MP4_BYTES)

    listeners: dict[str, list[Any]] = {"request": [], "response": []}

    class _FakeLocator:
        def __init__(self, count_val: int) -> None:
            self._count = count_val

        async def count(self) -> int:
            return self._count

    class _FakePage:
        def __init__(self, dialog_counts: list[int]) -> None:
            self._counts = iter(dialog_counts)

        def on(self, event: str, fn: Any) -> None:
            listeners[event].append(fn)

        def remove_listener(self, event: str, fn: Any) -> None:
            if fn in listeners[event]:
                listeners[event].remove(fn)

        def locator(self, _sel: str) -> _FakeLocator:
            return _FakeLocator(next(self._counts, 0))

    class _FakeResp:
        def __init__(self, status: int, body: str) -> None:
            self.url = f"https://flow.google.com/upload/v1/flow/upload/video/{_PID}"
            self.status = status
            self._body = body

        async def text(self) -> str:
            return self._body

    class _FakeChooser:
        def __init__(self, emit_upload: bool) -> None:
            self._emit = emit_upload

        async def set_files(self, _path: str) -> None:
            if not self._emit:
                return
            for fn in list(listeners["request"]):
                fn(MagicMock(url=f"https://flow.google.com/upload/v1/flow/upload/video/{_PID}"))
            # 1st POST: empty 200; 2nd POST: JSON 200 (REST mediaId=rec[0], media.workflowId=rec[2])
            for resp in (
                _FakeResp(200, ""),
                _FakeResp(200, json.dumps({"mediaId": _WF, "media": {"workflowId": _MID}})),
            ):
                for fn in list(listeners["response"]):
                    await fn(resp)

    monkeypatch.setattr(
        upload_mod, "_open_upload_chooser", AsyncMock(return_value=_FakeChooser(True))
    )
    uploaded = await upload_mod.upload_video(_FakePage([0]), _PID, clip, timeout_s=2.0)
    assert uploaded.media_id == _MID
    assert uploaded.workflow_id == _WF
    assert uploaded.display_name.endswith(".mp4")
    assert not uploaded.query_name.endswith(".mp4")

    # Rights dialog opened with no upload POST -> MediaUploadRejectedError with remediation
    monkeypatch.setattr(
        upload_mod, "_open_upload_chooser", AsyncMock(return_value=_FakeChooser(False))
    )
    with pytest.raises(MediaUploadRejectedError) as exc_info:
        await upload_mod.upload_video(_FakePage([0, 1]), _PID, clip, timeout_s=0.05)
    assert "rights to use this video" in (exc_info.value.remediation_hint or "")


# --- Zzl0ze source classification & jIps6 route guard -----------------------


def test_edit_source_record_recognises_generated_uploaded_and_image() -> None:
    generated_video = [[[_WF, _PID, _MID, None, None, [], None, [["video-meta"], [1280, 720]]]]]
    uploaded_video = [[[_WF, _PID, _MID, None, None, [], None, [None, [1280, 720, [3]]]]]]
    uploaded_image = [
        [
            [
                _WF,
                _PID,
                _MID,
                None,
                None,
                [],
                None,
                [None, [512, 512, None, None, None, None, "image/png"]],
            ]
        ]
    ]
    assert edit_mod.edit_source_record(generated_video, _MID) == edit_mod.SourceClip(_WF, "video")
    assert edit_mod.edit_source_record(uploaded_video, _MID) == edit_mod.SourceClip(_WF, "video")
    assert edit_mod.edit_source_record(uploaded_image, _MID) == edit_mod.SourceClip(_WF, "image")
    assert edit_mod.edit_source_record([], _MID) is None


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
    assert "single-clip" in err.detail

    # Multi-frame jIps6 batch refused before send
    frame_entry = ["jIps6", json.dumps(valid_inner), None, "generic"]
    two_frames = f"f.req={json.dumps([[frame_entry, frame_entry]])}"
    err = edit_mod.edit_request_problem("jIps6", two_frames, workflow_id=_WF)
    assert isinstance(err, WireFormatError)
    assert "expected 1 jIps6 frame" in err.detail


@pytest.mark.asyncio
async def test_run_video_edit_validates_refs_and_completes_local_and_clip_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.api.transports.batchexecute import GenerationRecord

    clip = tmp_path / "in.mp4"
    clip.write_bytes(_MP4_BYTES)
    good_ref = tmp_path / "ref.png"
    good_ref.write_bytes(_PNG_BYTES)
    bad_ref = tmp_path / "secret.png"
    bad_ref.write_bytes(b"-----BEGIN OPENSSH PRIVATE KEY-----")
    out_mp4 = tmp_path / "edited.mp4"

    class _FakeLoc:
        def __init__(self, count_val: int = 1) -> None:
            self._count = count_val

        @property
        def first(self) -> _FakeLoc:
            return self

        def locator(self, _sel: str) -> _FakeLoc:
            return self

        async def count(self) -> int:
            return self._count

        async def hover(self, **_kw: Any) -> None:
            return None

        async def wait_for(self, **_kw: Any) -> None:
            return None

        async def click(self, **_kw: Any) -> None:
            return None

    class _FakePage:
        url = f"https://flow.google.com/project/{_PID}/edit/{_MID}"

        def __init__(self) -> None:
            self.keyboard = MagicMock(insert_text=AsyncMock(), press=AsyncMock())

        def on(self, *_a: Any) -> None:
            return None

        def remove_listener(self, *_a: Any) -> None:
            return None

        def locator(self, sel: str) -> _FakeLoc:
            if sel == edit_mod.CHIP_CONTAINER_SELECTOR:
                return _FakeLoc(0)
            return _FakeLoc(1)

        async def wait_for_timeout(self, _ms: int) -> None:
            return None

        async def evaluate(self, *_a: Any) -> None:
            return None

    # 1. Invalid reference image fails BEFORE upload_video or browser interaction
    upload_spy = AsyncMock()
    monkeypatch.setattr(edit_mod, "upload_video", upload_spy)
    with pytest.raises(ValueError, match="Not a supported image format"):
        await edit_mod.run_video_edit(
            _FakePage(),
            EditVideoRequest(prompt="glow", source_video=clip, reference_images=(bad_ref,)),
            project_id=_PID,
            save=AsyncMock(),
            poll_timeout_s=10.0,
            download=True,
            on_started=None,
        )
    upload_spy.assert_not_called()

    # 2. Local video path end-to-end: upload -> attach -> quote -> submit_and_observe -> download
    done_record = GenerationRecord(
        workflow_id=_WF,
        project_id=_PID,
        media_id=_MID,
        status=3,
        video_url="https://flow-content.google/v.mp4",
        poster_url=None,
        size_bytes=len(_MP4_BYTES),
    )
    monkeypatch.setattr(
        edit_mod,
        "upload_video",
        AsyncMock(
            return_value=upload_mod.UploadedVideo(_MID, _WF, "in-12345678.mp4", "in-12345678")
        ),
    )
    monkeypatch.setattr(edit_mod, "_apply_main_edit_settings", AsyncMock(return_value=9))
    monkeypatch.setattr(edit_mod, "_attach_mention_asset", AsyncMock())

    fake_composer = MagicMock()
    fake_composer.ensure_editor_for = AsyncMock(return_value="classic")
    fake_composer.upload = AsyncMock(return_value=(_REF1, "ref-aaaaaaaa.png"))
    fake_composer.clear_composer = AsyncMock()
    fake_composer.send_prompt = AsyncMock()

    async def _fake_submit(_page: Any, **kwargs: Any) -> GenerationRecord:
        refs_wire = [[None, _REF1]]
        inner = [[[[None, _WF], None, "macaron_cookie_edit", 2, None, None, None, None, refs_wire]]]
        assert kwargs["request_check"]("jIps6", _make_freq("jIps6", inner)) is None
        if kwargs.get("on_started"):
            kwargs["on_started"](VideoStarted(media_id=_MID, project_id=_PID, workflow_id=_WF))
        return done_record

    fake_composer.submit_and_observe = AsyncMock(side_effect=_fake_submit)
    fake_composer.download = AsyncMock(return_value=out_mp4)
    monkeypatch.setattr(edit_mod, "MigratedComposer", lambda: fake_composer)

    started_seen: list[str] = []
    res = await edit_mod.run_video_edit(
        _FakePage(),
        EditVideoRequest(
            prompt="golden glow",
            source_video=clip,
            reference_images=(good_ref,),
            aspect=Aspect.LANDSCAPE,
            resolution="720p",
            max_credits=12,
        ),
        project_id=_PID,
        save=AsyncMock(),
        poll_timeout_s=30.0,
        download=True,
        on_started=lambda s: started_seen.append(s.media_id),
    )
    assert res.status.succeeded is True
    assert res.local_path == out_mp4
    assert res.quoted_credits == 9
    assert started_seen == [_MID]

    # 3. Clip Editor path end-to-end: source_clip -> model/quote -> attach -> submit -> download
    monkeypatch.setattr(
        edit_mod, "_source_clip", AsyncMock(return_value=edit_mod.SourceClip(_WF, "video"))
    )
    monkeypatch.setattr(edit_mod, "_confirm_editor", MagicMock())
    monkeypatch.setattr(
        edit_mod, "_configure_clip_editor_model_and_quote", AsyncMock(return_value=9)
    )
    res_clip = await edit_mod.run_video_edit(
        _FakePage(),
        EditVideoRequest(
            prompt="add snow",
            source_media_id=_MID,
            reference_images=(good_ref,),
            max_credits=12,
        ),
        project_id=_PID,
        save=AsyncMock(),
        poll_timeout_s=30.0,
        download=True,
        on_started=None,
    )
    assert res_clip.status.succeeded is True
    assert res_clip.quoted_credits == 9
    assert res_clip.local_path == out_mp4


# --- Client, Transport, Recorder & JSON Output ------------------------------


@pytest.mark.asyncio
async def test_client_edit_video_requires_project_for_uuid_and_creates_for_local(
    tmp_path: Path,
) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(_MP4_BYTES)

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
    client.create_project = AsyncMock(return_value=MagicMock(project_id=_PID))  # type: ignore[method-assign]

    # Existing media UUID without project -> ConfigurationError
    with pytest.raises(ConfigurationError, match="--project"):
        await client.edit_video(req=EditVideoRequest(prompt="snow", source_media_id=_MID))

    # Local video without project -> auto-creates project and relays checkpoints
    phases: list[str] = []
    res = await client.edit_video(
        req=EditVideoRequest(prompt="warm glow", source_video=clip),
        on_checkpoint=lambda cp: phases.append(cp.phase),
    )
    assert res.project_id == _PID
    assert res.quoted_credits == 9
    assert phases == ["submit_attempted"]
    assert transport.calls[0]["project_id"] == _PID


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
        with pytest.raises(FlowHostMigratedError):
            await tr.edit_video(request=EditVideoRequest(prompt="glow", source_video=clip))
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
