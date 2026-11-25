"""MCP + worker tests for `gflow_edit_video` and `task_type='edit'`."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from gflow_cli.api.video import EditVideoRequest, VideoModel, VideoResult, VideoStatus
from gflow_cli.config import reset_settings
from gflow_cli.data.models import OperationKind, OperationStatus
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.mcp import tools as common
from gflow_cli.mcp import video_edit_tools as edit_tools
from gflow_cli.worker import codec
from gflow_cli.worker.daemon import FlowWorker
from gflow_cli.worker.queue import QueueRepository, QueueTask

_PID = "11111111-1111-1111-1111-111111111111"
_MID = "22222222-2222-2222-2222-222222222222"
_WF = "33333333-3333-3333-3333-333333333333"
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_MP4_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


def test_codec_decodes_edit_payload_and_warns_retiring(tmp_path: Path) -> None:
    clip = tmp_path / "in.mp4"
    clip.write_bytes(_MP4_BYTES)
    decoded = codec.decode_payload(
        "edit",
        {
            "schema_version": 1,
            "prompt": "warm sunset",
            "source_video": str(clip),
            "model": "omni-flash",
            "resolution": "720p",
            "max_credits": 12,
        },
    )
    assert isinstance(decoded.request, EditVideoRequest)
    assert decoded.request.model is VideoModel.OMNI_FLASH
    assert decoded.request.max_credits == 12

    task = QueueTask(
        task_id="t-1",
        profile_name="p1",
        task_type="edit",
        status="completed",
        payload={"model": "omni-flash"},
        created_at="2026-11-24T00:00:00Z",
    )
    warns = codec.task_warnings(task)
    assert warns["warnings"][0]["type"] == "model_retiring"


@pytest.mark.asyncio
async def test_mcp_edit_video_validation_and_confinement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    clip = tmp_path / "in.mp4"
    clip.write_bytes(_MP4_BYTES)
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(out_dir))
    reset_settings()
    try:
        # Escaping out_file refused pre-queue
        res = await edit_tools.gflow_edit_video(
            source=str(clip), prompt="glow", out_file="../escape.mp4"
        )
        assert res["status"] == "error"
        assert res["error"]["title"] == "Invalid Output"

        # Non-Omni model refused pre-queue
        res = await edit_tools.gflow_edit_video(source=str(clip), prompt="glow", model="veo-lite")
        assert res["status"] == "error"
        assert res["error"]["title"] == "Invalid Video Edit Request"

        # >5 reference_images refused pre-queue
        refs = [str(clip)] * 6
        res = await edit_tools.gflow_edit_video(
            source=str(clip), prompt="glow", reference_images=refs
        )
        assert res["status"] == "error"
        assert res["error"]["title"] == "Invalid Video Edit Request"

        # Missing project for uncatalogued UUID refused
        with (
            patch.object(common, "_resolve_and_validate_profile", return_value="default"),
            patch("gflow_cli.cli_video_extend.default_project", return_value=None),
            patch("gflow_cli.cli_video_extend.lookup_project_in_catalog", return_value=None),
        ):
            res = await edit_tools.gflow_edit_video(source=_MID, prompt="glow")
        assert res["status"] == "error"
        assert res["error"]["title"] == "Missing Project"

        # Valid call enqueues task_type='edit' with confined output_file
        run_mock = AsyncMock(return_value={"status": "completed", "flow_media_id": _MID})
        with (
            patch.object(common, "_resolve_and_validate_profile", return_value="default"),
            patch("gflow_cli.cli_video_extend.default_project", return_value=_PID),
            patch.object(common, "_run_generation_task", run_mock),
        ):
            res = await edit_tools.gflow_edit_video(
                source=str(clip),
                prompt="glow",
                resolution="720p",
                max_credits=15,
                out_file="sub/edited.mp4",
            )
        assert res["status"] == "completed"
        call_kw = run_mock.call_args.kwargs
        assert call_kw["task_type"] == "edit"
        assert call_kw["payload"]["source_video"] == str(clip.resolve())
        assert call_kw["payload"]["output_file"] == str((out_dir / "sub" / "edited.mp4").resolve())
        assert call_kw["payload"]["max_credits"] == 15

        # Mixed [catalog_uuid, local_path]: the UUID resolves to its catalogued file before
        # the queue, in the caller's order (the worker reads local paths only).
        ref_a = tmp_path / "catalog_a.png"
        ref_b = tmp_path / "local_b.png"
        ref_a.write_bytes(_PNG_BYTES)
        ref_b.write_bytes(_PNG_BYTES)
        run_mock.reset_mock()
        with (
            patch.object(common, "_resolve_and_validate_profile", return_value="default"),
            patch("gflow_cli.cli_video_extend.default_project", return_value=_PID),
            patch.object(common, "_resolve_ref_local_path", return_value=(str(ref_a), None)),
            patch.object(common, "_run_generation_task", run_mock),
        ):
            res = await edit_tools.gflow_edit_video(
                source=str(clip), prompt="glow", reference_images=[_MID, str(ref_b)]
            )
        assert res["status"] == "completed"
        assert run_mock.call_args.kwargs["payload"]["reference_images"] == [
            str(ref_a),
            str(ref_b.resolve()),
        ]

        # An empty prompt is refused before the queue, like the CLI's
        res = await edit_tools.gflow_edit_video(source=str(clip), prompt="  ")
        assert res["status"] == "error"
        assert res["error"]["title"] == "Invalid Video Edit Request"
    finally:
        reset_settings()


@pytest.mark.asyncio
async def test_worker_processes_edit_task_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "gflow.db"
    clip = tmp_path / "in.mp4"
    dl = tmp_path / "dl.mp4"
    dest = tmp_path / "out" / "edited.mp4"
    clip.write_bytes(_MP4_BYTES)
    dl.write_bytes(_MP4_BYTES)

    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(tmp_path / "out"))
    reset_settings()

    class _FakeClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def edit_video(self, **kwargs: Any) -> VideoResult:
            if kwargs.get("on_started"):
                from gflow_cli.api.video import VideoStarted

                kwargs["on_started"](VideoStarted(media_id=_MID, project_id=_PID, workflow_id=_WF))
            return VideoResult(
                status=VideoStatus(media_id=_MID, status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
                local_path=dl,
                project_id=_PID,
                workflow_id=_WF,
                quoted_credits=9,
            )

    try:
        with DataStore.open(db_path) as store:
            DataRepository(store).upsert_profile("p1", tmp_path / "home" / "profile_p1")
            QueueRepository(store).enqueue_task(
                task_id="edit-1",
                profile_name="p1",
                task_type="edit",
                payload={
                    "schema_version": 1,
                    "prompt": "lantern glow",
                    "source_video": str(clip),
                    "project_id": _PID,
                    "model": "omni-1.2",
                    "output_file": str(dest),
                },
            )
        worker = FlowWorker("p1", str(db_path))
        try:
            task = worker.repo.claim_task("edit-1", "worker:test")
            assert task is not None
            with patch("gflow_cli.worker.daemon.FlowApiClient", _FakeClient):
                await worker.process_task(task)
            done = worker.repo.get_task("edit-1")
            assert done is not None
            assert done.status == "completed"
            assert done.flow_media_id == _MID
            assert dest.is_file()
            op = DataRepository(worker.db).get_operation_for_output_asset(
                "p1", _MID, OperationKind.EDIT
            )
            assert op is not None
            assert op.status == OperationStatus.SUCCEEDED
        finally:
            worker.close()
    finally:
        reset_settings()


@pytest.mark.asyncio
async def test_worker_refuses_an_unsupported_file_before_creating_a_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 11, charged no, and no empty project left behind (the extend task's order)."""
    db_path = tmp_path / "gflow.db"
    not_a_video = tmp_path / "notes.mp4"
    not_a_video.write_text("plain text, not a container")
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path / "home"))
    reset_settings()
    created: list[str] = []

    class _Client:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def create_project(self, *, title: str) -> Any:
            created.append(title)
            raise AssertionError("no project may be created for a refused file")

    try:
        with DataStore.open(db_path) as store:
            DataRepository(store).upsert_profile("p1", tmp_path / "home" / "profile_p1")
            QueueRepository(store).enqueue_task(
                task_id="edit-bad",
                profile_name="p1",
                task_type="edit",
                payload={"schema_version": 1, "prompt": "glow", "source_video": str(not_a_video)},
            )
        worker = FlowWorker("p1", str(db_path))
        try:
            task = worker.repo.claim_task("edit-bad", "worker:test")
            assert task is not None
            with patch("gflow_cli.worker.daemon.FlowApiClient", _Client):
                await worker.process_task(task)
            done = worker.repo.get_task("edit-bad")
            assert done is not None
            assert done.status == "failed"
            assert done.error is not None
            assert done.error["exit_code"] == 11
            assert done.error["charged"] == "no"
            assert created == []
            # The failure is recorded with the edit's own request (its model), not as anonymous.
            row = worker.db.conn.execute(
                "SELECT model, status FROM operations WHERE mode = 'edit'"
            ).fetchone()
            assert row is not None
            assert tuple(row) == (VideoModel.OMNI_1_2_FLASH.value, "failed")
        finally:
            worker.close()
    finally:
        reset_settings()


@pytest.mark.parametrize(
    "bad",
    [
        {"aspect": 16},
        {"model": 3},
        {"resolution": 720},
        {"source_media_id": 7},
        {"source_video": ["a.mp4"]},
        {"max_credits": 1.5},
        {"max_credits": True},
        {"reference_images": "ref.png"},
        {"reference_images": [3]},
        {"project_id": 1},
        {"output_file": {"p": 1}},
        {"prompt": 5},
    ],
    ids=lambda d: next(iter(d)) + "-" + type(next(iter(d.values()))).__name__,
)
def test_an_edit_payload_with_a_wrong_type_is_a_queue_schema_error(bad: dict[str, object]) -> None:
    from gflow_cli.errors import EXIT_CODE_MAP, QueueSchemaError

    payload = {"prompt": "go", "source_video": "clip.mp4", **bad}
    if "source_media_id" in bad:
        payload.pop("source_video")
    with pytest.raises(QueueSchemaError):
        codec.decode_payload("edit", payload)
    assert EXIT_CODE_MAP[QueueSchemaError] == 30


def test_an_edit_payload_without_a_model_decodes_to_the_default() -> None:
    from gflow_cli.api.video import EDIT_DEFAULT_MODEL

    decoded = codec.decode_payload("edit", {"prompt": "go", "source_video": "clip.mp4"})
    assert isinstance(decoded.request, EditVideoRequest)
    assert decoded.request.model is EDIT_DEFAULT_MODEL


@pytest.mark.asyncio
async def test_mcp_refuses_references_on_an_existing_clip_before_the_queue(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref.png"
    ref.write_bytes(_PNG_BYTES)
    run_mock = AsyncMock()
    with patch.object(common, "_run_generation_task", run_mock):
        res = await edit_tools.gflow_edit_video(
            source=_MID, prompt="add snow", project=_PID, reference_images=[str(ref)]
        )
    assert res["status"] == "error"
    assert res["error"]["type"] == "https://gflow-cli.dev/errors/configuration"  # exit 11
    assert res["error"]["charged"] == "no"
    assert "not ported" in res["error"]["detail"]
    run_mock.assert_not_called()


@pytest.mark.asyncio
async def test_worker_refuses_references_on_an_existing_clip_before_a_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "gflow.db"
    ref = tmp_path / "ref.png"
    ref.write_bytes(_PNG_BYTES)
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path / "home"))
    reset_settings()
    opened: list[str] = []

    class _Client:
        def __init__(self, **_kwargs: Any) -> None:
            opened.append("client")

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

    try:
        with DataStore.open(db_path) as store:
            DataRepository(store).upsert_profile("p1", tmp_path / "home" / "profile_p1")
            QueueRepository(store).enqueue_task(
                task_id="edit-refs",
                profile_name="p1",
                task_type="edit",
                payload={
                    "schema_version": 1,
                    "prompt": "snow",
                    "source_media_id": _MID,
                    "project_id": _PID,
                    "reference_images": [str(ref)],
                },
            )
        worker = FlowWorker("p1", str(db_path))
        try:
            task = worker.repo.claim_task("edit-refs", "worker:test")
            assert task is not None
            with patch("gflow_cli.worker.daemon.FlowApiClient", _Client):
                await worker.process_task(task)
            done = worker.repo.get_task("edit-refs")
            assert done is not None
            assert done.status == "failed"
            assert done.error is not None
            assert done.error["exit_code"] == 11
            assert done.error["charged"] == "no"
            assert opened == []  # no browser, no project
        finally:
            worker.close()
    finally:
        reset_settings()


@pytest.mark.asyncio
async def test_a_failed_queued_edit_records_the_prompt_its_tool_rewrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.errors import TransportTimeoutError

    db_path = tmp_path / "gflow.db"
    clip = tmp_path / "in.mp4"
    clip.write_bytes(_MP4_BYTES)
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GFLOW_CLI_HISTORY_PROMPTS", "store")
    reset_settings()
    monkeypatch.setattr(
        "gflow_cli._cli_helpers.apply_tool_option",
        lambda prompt, _specs, **_kw: (f"rewritten: {prompt}", prompt, None),
    )

    class _Client:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def edit_video(self, **_kwargs: Any) -> VideoResult:
            raise TransportTimeoutError(detail="no reply")

    try:
        with DataStore.open(db_path) as store:
            DataRepository(store).upsert_profile("p1", tmp_path / "home" / "profile_p1")
            QueueRepository(store).enqueue_task(
                task_id="edit-tool",
                profile_name="p1",
                task_type="edit",
                payload={
                    "schema_version": 1,
                    "prompt": "glow",
                    "source_video": str(clip),
                    "project_id": _PID,
                    "tool_specs": ["creative-director"],
                },
            )
        worker = FlowWorker("p1", str(db_path))
        try:
            task = worker.repo.claim_task("edit-tool", "worker:test")
            assert task is not None
            with patch("gflow_cli.worker.daemon.FlowApiClient", _Client):
                await worker.process_task(task)
            row = worker.db.conn.execute(
                "SELECT prompt, expanded_prompt FROM operations WHERE mode = 'edit'"
            ).fetchone()
            assert row is not None
            # As a success records it: the user's prompt, and what the tool sent.
            assert tuple(row) == ("glow", "rewritten: glow")
        finally:
            worker.close()
    finally:
        reset_settings()
