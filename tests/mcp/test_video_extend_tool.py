"""Tests for MCP ``gflow_extend_video``, queue codec, and FlowWorker extend execution."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.api.transports.batchexecute import GenerationRecord
from gflow_cli.api.transports.migrated_extend import ExtendOutcome
from gflow_cli.api.video_extend import ExtendStarted, MigratedExtendRequest
from gflow_cli.config import reset_settings
from gflow_cli.data.models import AssetRecord, LocalFileRecord, ProjectRecord
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import CreditCapExceededError
from gflow_cli.mcp import video_extend_tools as ext_tools
from gflow_cli.worker import codec
from gflow_cli.worker.daemon import FlowWorker
from gflow_cli.worker.queue import QueueRepository

MEDIA = "b9458021-fc2d-4d95-ab53-cf844c6f1079"
PROJECT = "7d3d6bd9-a39f-4c2d-b772-146e73e539cf"
REF_UUID = "11111111-2222-4333-8444-555555555555"


def _make_png(path: Path) -> Path:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    return path


def _make_mp4(path: Path) -> Path:
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00")
    return path


def test_codec_round_trips_extend_payload_and_rejects_bad_inputs(tmp_path: Path) -> None:
    clip = _make_mp4(tmp_path / "src.mp4")
    ref = _make_png(tmp_path / "ref.png")
    decoded = codec.decode_payload(
        "extend",
        {
            "schema_version": 1,
            "prompt": "camera glides forward",
            "source_video": str(clip),
            "duration": 6,
            "reference_images": [str(ref)],
            "aspect": "16:9",
            "max_credits": 20,
        },
    )
    assert isinstance(decoded.request, MigratedExtendRequest)
    assert decoded.request.source_video == clip
    assert decoded.request.duration == 6
    assert decoded.request.reference_images == (ref,)
    assert decoded.request.max_credits == 20

    from gflow_cli.errors import QueueSchemaError

    with pytest.raises(QueueSchemaError):
        codec.decode_payload("extend", {"prompt": "x", "source_media_id": MEDIA, "duration": 11})
    with pytest.raises(QueueSchemaError):
        codec.decode_payload(
            "extend",
            {
                "prompt": "x",
                "source_media_id": MEDIA,
                "reference_images": [str(ref)] * 4,
            },
        )


@pytest.mark.asyncio
async def test_mcp_extend_validation_rejects_bad_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(tmp_path / "out"))
    reset_settings()

    bad_src = await ext_tools.gflow_extend_video(source="not-a-uuid-or-file", prompt="go")
    assert bad_src["status"] == "error"

    clip = _make_mp4(tmp_path / "clip.mp4")
    bad_dur = await ext_tools.gflow_extend_video(source=str(clip), prompt="go", duration=12)
    assert bad_dur["status"] == "error"

    bad_aspect = await ext_tools.gflow_extend_video(source=str(clip), prompt="go", aspect="1:1")
    assert bad_aspect["status"] == "error"

    refs = [str(_make_png(tmp_path / f"r{i}.png")) for i in range(4)]
    too_many = await ext_tools.gflow_extend_video(
        source=str(clip), prompt="go", reference_images=refs
    )
    assert too_many["status"] == "error"

    escape = await ext_tools.gflow_extend_video(
        source=str(clip), prompt="go", out_file=str(tmp_path / "outside.mp4")
    )
    assert escape["status"] == "error"


@pytest.mark.asyncio
async def test_mcp_extend_preserves_mixed_ref_order_and_runs_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "gflow.db"
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(out_dir))
    monkeypatch.setenv("GFLOW_CLI_PROFILES_DIR", str(tmp_path / "profiles"))
    (tmp_path / "profiles" / "default").mkdir(parents=True)
    reset_settings()

    clip = _make_mp4(tmp_path / "clip.mp4")
    local_ref = _make_png(tmp_path / "local_ref.png")
    cat_ref = _make_png(tmp_path / "cat_ref.png")

    with DataStore.open(db_path) as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", tmp_path / "profiles" / "default")
        repo.upsert_project(
            ProjectRecord(
                id="p1", profile_name="default", flow_project_id=PROJECT, title="t", source="gen"
            )
        )
        repo.upsert_asset(
            AssetRecord.minimal_image(
                id="a1", profile_name="default", flow_project_id=PROJECT, flow_media_id=REF_UUID
            )
        )
        repo.upsert_local_file(
            LocalFileRecord(
                id="lf1",
                profile_name="default",
                asset_id="a1",
                path=cat_ref,
                media_type="image/png",
                bytes=cat_ref.stat().st_size,
                sha256=hashlib.sha256(cat_ref.read_bytes()).hexdigest(),
            )
        )

    captured_calls: list[dict[str, Any]] = []

    class _FakeClient:
        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def extend_video_migrated(self, **kwargs: Any) -> tuple[ExtendOutcome, Path]:
            captured_calls.append(kwargs)
            kwargs["on_started"](
                ExtendStarted(
                    media_id="extended-media-1",
                    workflow_id="extended-wf-1",
                    model_key="macaron_cookie_extend_5s",
                ),
                "16:9",
            )
            out_path: Path = kwargs["out_path"]
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00")
            rec = GenerationRecord(
                workflow_id="extended-wf-1",
                project_id=PROJECT,
                media_id="extended-media-1",
                status=3,
            )
            outcome = ExtendOutcome(
                record=rec,
                video=b"\x00\x00\x00\x18ftypmp42\x00\x00",
                model_key="macaron_cookie_extend_5s",
                aspect="16:9",
                scene_id=None,
                quoted_credits=15,
                source_media_id="extended-media-1",
            )
            return outcome, out_path

    with (
        patch("gflow_cli.mcp.tools._resolve_and_validate_profile", return_value="default"),
        patch("gflow_cli.worker.daemon.FlowApiClient", return_value=_FakeClient()),
    ):
        result = await ext_tools.gflow_extend_video(
            source=str(clip),
            prompt="continue panning",
            duration=5,
            reference_images=[REF_UUID, str(local_ref)],
            max_credits=20,
            project=PROJECT,
            out_file="extended.mp4",
            wait=True,
        )

    assert result["status"] == "completed", result
    assert len(captured_calls) == 1
    call = captured_calls[0]
    assert call["source_video"] == clip.resolve()
    assert call["duration"] == 5
    assert call["max_credits"] == 20
    # Caller order preserved: catalog UUID resolved first, local path second.
    assert call["reference_images"] == (cat_ref.resolve(), local_ref.resolve())
    assert call["out_path"] == (out_dir / "extended.mp4").resolve()
    assert result["files"] == [str((out_dir / "extended.mp4").resolve())]
    with DataStore.open(db_path) as store:
        row = store.conn.execute(
            "SELECT status FROM assets WHERE profile_name = ? AND flow_media_id = ?",
            ("default", "extended-media-1"),
        ).fetchone()
        assert row is not None
        assert row["status"] == "MEDIA_GENERATION_STATUS_SUCCESSFUL"


@pytest.mark.asyncio
async def test_mcp_extend_uncatalogued_clip_returns_files_and_prefers_catalog_over_default_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "gflow.db"
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    other_project = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(out_dir))
    monkeypatch.setenv("GFLOW_CLI_PROFILES_DIR", str(tmp_path / "profiles"))
    monkeypatch.setenv("GFLOW_CLI_PROJECT", other_project)
    (tmp_path / "profiles" / "default").mkdir(parents=True)
    reset_settings()

    # 1. Catalogued clip in PROJECT must resolve PROJECT even when GFLOW_CLI_PROJECT=other_project.
    with DataStore.open(db_path) as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", tmp_path / "profiles" / "default")
        repo.upsert_project(
            ProjectRecord(
                id="p1", profile_name="default", flow_project_id=PROJECT, title="t", source="gen"
            )
        )
        repo.upsert_asset(
            AssetRecord.minimal_image(
                id="a-vid", profile_name="default", flow_project_id=PROJECT, flow_media_id=MEDIA
            )
        )
    resolved_proj, err = ext_tools._resolve_extend_project(
        None, None, "default", {"source_media_id": MEDIA}
    )
    assert err is None
    assert resolved_proj == PROJECT

    # 2. Uncatalogued clip extended in place via MCP must populate catalog and return files=[...].
    uncatalogued_media = "99999999-8888-4777-8666-555555555555"

    class _FakeClient:
        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def extend_video_migrated(self, **kwargs: Any) -> tuple[ExtendOutcome, Path]:
            out_path: Path = kwargs["out_path"]
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00")
            rec = GenerationRecord(
                workflow_id="uncat-wf-1",
                project_id=PROJECT,
                media_id=uncatalogued_media,
                status=3,
            )
            outcome = ExtendOutcome(
                record=rec,
                video=b"\x00\x00\x00\x18ftypmp42\x00\x00",
                model_key="macaron_cookie_extend_4s",
                aspect="16:9",
                scene_id=None,
                quoted_credits=12,
                source_media_id=uncatalogued_media,
            )
            return outcome, out_path

    with (
        patch("gflow_cli.mcp.tools._resolve_and_validate_profile", return_value="default"),
        patch("gflow_cli.worker.daemon.FlowApiClient", return_value=_FakeClient()),
    ):
        result = await ext_tools.gflow_extend_video(
            source=uncatalogued_media,
            prompt="continue slowly",
            project=PROJECT,
            out_file="uncat.mp4",
            wait=True,
        )
    assert result["status"] == "completed", result
    assert result["flow_project_id"] == PROJECT
    assert result["files"] == [str((out_dir / "uncat.mp4").resolve())]


@pytest.mark.asyncio
async def test_worker_extend_respects_kill_switch_and_validates_before_create_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "gflow.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    monkeypatch.setenv("GFLOW_CLI_PROFILES_DIR", str(tmp_path / "profiles"))
    (tmp_path / "profiles" / "default").mkdir(parents=True)

    # 1. Host kill switch (GFLOW_CLI_FLOW_HOST=labs.google) stops before client/project creation.
    monkeypatch.setenv("GFLOW_CLI_FLOW_HOST", "labs.google")
    reset_settings()
    fake = MagicMock()
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=None)
    fake.create_project = AsyncMock()
    fake.extend_video_migrated = AsyncMock()

    with DataStore.open(db_path) as store:
        DataRepository(store).upsert_profile("default", tmp_path / "profiles" / "default")
        q = QueueRepository(store)
        q.enqueue_task(
            "kill-switch-1",
            "default",
            "extend",
            {"schema_version": 1, "prompt": "go", "source_media_id": MEDIA, "project_id": PROJECT},
        )
        claimed = q.claim_task("kill-switch-1", "test")
        assert claimed is not None

    worker = FlowWorker("default", str(db_path))
    try:
        with patch("gflow_cli.worker.daemon.FlowApiClient", return_value=fake):
            await worker.process_task(claimed)
    finally:
        worker.close()

    with DataStore.open(db_path) as store:
        row = QueueRepository(store).get_task("kill-switch-1")
        assert row is not None and row.status == "failed"
        assert row.error is not None and row.error["exit_code"] == 36
    fake.create_project.assert_not_awaited()
    fake.extend_video_migrated.assert_not_awaited()

    # 2. Invalid local video file fails before create_project.
    monkeypatch.delenv("GFLOW_CLI_FLOW_HOST", raising=False)
    reset_settings()
    bad_video = tmp_path / "corrupt.mp4"
    bad_video.write_bytes(b"NOT_A_REAL_VIDEO")
    with DataStore.open(db_path) as store:
        q = QueueRepository(store)
        q.enqueue_task(
            "bad-video-1",
            "default",
            "extend",
            {"schema_version": 1, "prompt": "go", "source_video": str(bad_video)},
        )
        claimed2 = q.claim_task("bad-video-1", "test")
        assert claimed2 is not None

    worker2 = FlowWorker("default", str(db_path))
    try:
        with patch("gflow_cli.worker.daemon.FlowApiClient", return_value=fake):
            await worker2.process_task(claimed2)
    finally:
        worker2.close()

    with DataStore.open(db_path) as store:
        row2 = QueueRepository(store).get_task("bad-video-1")
        assert row2 is not None and row2.status == "failed"
    fake.create_project.assert_not_awaited()
    fake.extend_video_migrated.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_extend_preserves_cloud_storage_uri_without_path_mangling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "gflow.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    monkeypatch.setenv("GFLOW_CLI_PROFILES_DIR", str(tmp_path / "profiles"))
    (tmp_path / "profiles" / "default").mkdir(parents=True)
    reset_settings()

    class _FakeS3Path:
        name = "extended.mp4"

        def __str__(self) -> str:
            return "s3://my-bucket/videos/extended.mp4"

    cloud_target = _FakeS3Path()

    class _CloudClient:
        async def __aenter__(self) -> _CloudClient:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def extend_video_migrated(self, **_kwargs: Any) -> tuple[ExtendOutcome, Any]:
            rec = GenerationRecord(
                workflow_id="cloud-wf-1",
                project_id=PROJECT,
                media_id=MEDIA,
                status=3,
            )
            outcome = ExtendOutcome(
                record=rec,
                video=b"\x00\x00\x00\x18ftypmp42\x00\x00",
                model_key="macaron_cookie_extend_4s",
                aspect="16:9",
                scene_id=None,
                quoted_credits=12,
                source_media_id=MEDIA,
            )
            return outcome, cloud_target

    with DataStore.open(db_path) as store:
        DataRepository(store).upsert_profile("default", tmp_path / "profiles" / "default")
        q = QueueRepository(store)
        q.enqueue_task(
            "cloud-ext-1",
            "default",
            "extend",
            {"schema_version": 1, "prompt": "go", "source_media_id": MEDIA, "project_id": PROJECT},
        )
        claimed = q.claim_task("cloud-ext-1", "test")
        assert claimed is not None

    worker = FlowWorker("default", str(db_path))
    try:
        with patch("gflow_cli.worker.daemon.FlowApiClient", return_value=_CloudClient()):
            await worker.process_task(claimed)
    finally:
        worker.close()

    with DataStore.open(db_path) as store:
        lf = store.conn.execute(
            "SELECT path, storage_provider, cloud_uri FROM local_files WHERE profile_name = ?",
            ("default",),
        ).fetchone()
        assert lf is not None
        assert lf["path"] == "s3://my-bucket/videos/extended.mp4"
        assert lf["storage_provider"] == "s3"
        assert lf["cloud_uri"] == "s3://my-bucket/videos/extended.mp4"


@pytest.mark.asyncio
async def test_worker_extend_credit_cap_error_persists_exit_45(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "gflow.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    monkeypatch.setenv("GFLOW_CLI_PROFILES_DIR", str(tmp_path / "profiles"))
    (tmp_path / "profiles" / "default").mkdir(parents=True)
    reset_settings()

    fake = MagicMock()
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=None)
    fake.extend_video_migrated = AsyncMock(
        side_effect=CreditCapExceededError(quoted=15, max_credits=0)
    )

    with DataStore.open(db_path) as store:
        DataRepository(store).upsert_profile("default", tmp_path / "profiles" / "default")
        q = QueueRepository(store)
        task_id = "extend-cap-task-1"
        q.enqueue_task(
            task_id,
            "default",
            "extend",
            {
                "schema_version": 1,
                "prompt": "go",
                "source_media_id": MEDIA,
                "project_id": PROJECT,
                "max_credits": 0,
            },
        )
        claimed = q.claim_task(task_id, "test")
        assert claimed is not None

    worker = FlowWorker("default", str(db_path))
    try:
        with patch("gflow_cli.worker.daemon.FlowApiClient", return_value=fake):
            await worker.process_task(claimed)
    finally:
        worker.close()

    with DataStore.open(db_path) as store:
        row = QueueRepository(store).get_task(task_id)
        assert row is not None
        assert row.status == "failed"
        assert row.error is not None
        assert row.error["exit_code"] == 45
        assert row.error["charged"] == "no"
