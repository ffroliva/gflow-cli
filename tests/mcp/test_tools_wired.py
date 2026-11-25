# SPDX-License-Identifier: MIT
"""Tests for the wired MCP tool execution path.

These tests verify that the generation tools enqueue tasks, invoke
FlowWorker.process_task, and return structured results — without actually
launching a Chrome browser.  FlowApiClient is patched out so the tests
run offline and quickly.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.api.video import VideoResult, VideoStatus
from gflow_cli.data.store import DataStore

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def temp_db(tmp_path: Path) -> Iterator[DataStore]:
    """Isolated DataStore with a seeded 'default' profile row."""
    db_file = tmp_path / "gflow_test.db"
    store = DataStore.open(db_file)
    store.conn.execute(
        "INSERT INTO profiles(name, profile_dir, first_seen_at) "
        "VALUES ('default', '/profiles/default', '2026-06-29T00:00:00Z')"
    )
    store.conn.commit()
    try:
        yield store
    finally:
        store.close()


@dataclass
class _FakeImage:
    media_name: str
    dimensions: tuple[int, int] = (1024, 1024)
    workflow_id: str = "workflow-123"
    media_generation_id: str = "gen-123"
    model_name_type: str = "model-123"
    aspect_ratio: str = "1:1"
    seed: int = 12345
    fife_url: str = "http://fake"
    display_name: str | None = None  # mirrors GeneratedImage.display_name
    transport: str | None = None  # mirrors GeneratedImage.transport


def _completed_video_result(media_id: str = "media-vid-wired") -> VideoResult:
    """A real, successful VideoResult — the worker checks ``status.succeeded``,
    which only exists on the real VideoStatus. A hand-rolled fake silently broke
    that path and flipped the task to ``failed``."""
    return VideoResult(
        status=VideoStatus(media_id=media_id, status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
        local_path=None,
        project_id="proj-abc",
        flow_operation_id="op-123",
    )


class _FakeFlowApiClient:
    def __init__(self, **kwargs: Any):
        self.generate_image = AsyncMock(return_value=_FakeImage(media_name="media-img-wired"))
        self.generate_images_batch = AsyncMock(
            return_value=[_FakeImage(media_name="media-img-wired")]
        )
        self.generate_video = AsyncMock(return_value=_completed_video_result())
        self.create_project = AsyncMock(
            return_value=MagicMock(project_id="proj-abc", title="Test Project")
        )

        async def _download_impl(_image: Any, out_path: Path) -> Path:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"PNG_FAKE")
            return out_path

        self.download_image = AsyncMock(side_effect=_download_impl)

    async def __aenter__(self) -> _FakeFlowApiClient:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False


# ---------------------------------------------------------------------------
# Image generation — wired path
# ---------------------------------------------------------------------------


class TestGenerateImageWired:
    @pytest.mark.asyncio
    async def test_image_t2i_returns_completed(self, temp_db: DataStore, tmp_path: Path) -> None:
        """gflow_generate_image should return status='completed' with the wired path."""
        from gflow_cli.mcp.tools import gflow_generate_image

        with (
            patch(
                "gflow_cli.mcp.tools._resolve_and_validate_profile",
                return_value="default",
            ),
            patch(
                "gflow_cli.worker.daemon.FlowApiClient",
                return_value=_FakeFlowApiClient(),
            ),
            patch(
                "gflow_cli.mcp.tools.get_settings",
                return_value=MagicMock(
                    resolved_db_path=lambda: temp_db.path,
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    timeout_seconds=30,
                    output_dir=tmp_path / "out",
                ),
            ),
            patch(
                "gflow_cli.worker.daemon.get_settings",
                return_value=MagicMock(
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    headless=True,
                    transport=None,
                    output_dir=tmp_path / "out",
                    max_credits=None,
                ),
            ),
        ):
            result = await gflow_generate_image(prompt="scenic mountain at sunset")

        assert result["status"] == "completed"
        assert result["task_id"]
        assert "flow_media_id" in result
        assert "files" in result
        assert result["params"]["prompt"] == "scenic mountain at sunset"

    @pytest.mark.asyncio
    async def test_image_completed_reports_the_seed_flow_used(
        self, temp_db: DataStore, tmp_path: Path
    ) -> None:
        """#1013: the result carries the catalogued seed Flow chose, never a requested one."""
        from gflow_cli.mcp.tools import gflow_generate_image

        with (
            patch(
                "gflow_cli.mcp.tools._resolve_and_validate_profile",
                return_value="default",
            ),
            patch(
                "gflow_cli.worker.daemon.FlowApiClient",
                return_value=_FakeFlowApiClient(),
            ),
            patch(
                "gflow_cli.mcp.tools.get_settings",
                return_value=MagicMock(
                    resolved_db_path=lambda: temp_db.path,
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    timeout_seconds=30,
                    output_dir=tmp_path / "out",
                ),
            ),
            patch(
                "gflow_cli.worker.daemon.get_settings",
                return_value=MagicMock(
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    headless=True,
                    transport=None,
                    output_dir=tmp_path / "out",
                    max_credits=None,
                ),
            ),
        ):
            result = await gflow_generate_image(prompt="scenic mountain at sunset")

        assert result["status"] == "completed"
        assert result["seed"] == 12345  # _FakeImage.seed, as persisted in the catalog
        assert "seed" not in result["params"]

    @pytest.mark.asyncio
    async def test_image_completed_task_has_params(
        self, temp_db: DataStore, tmp_path: Path
    ) -> None:
        """The result always carries the original request params."""
        from gflow_cli.mcp.tools import gflow_generate_image

        with (
            patch(
                "gflow_cli.mcp.tools._resolve_and_validate_profile",
                return_value="default",
            ),
            patch(
                "gflow_cli.worker.daemon.FlowApiClient",
                return_value=_FakeFlowApiClient(),
            ),
            patch(
                "gflow_cli.mcp.tools.get_settings",
                return_value=MagicMock(
                    resolved_db_path=lambda: temp_db.path,
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    timeout_seconds=30,
                    output_dir=tmp_path / "out",
                ),
            ),
            patch(
                "gflow_cli.worker.daemon.get_settings",
                return_value=MagicMock(
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    headless=True,
                    transport=None,
                    output_dir=tmp_path / "out",
                    max_credits=None,
                ),
            ),
        ):
            result = await gflow_generate_image(
                prompt="cat in a garden",
                model="nano-pro",
                aspect="16:9",
                count=2,
            )

        assert result["params"]["model"] == "nano-pro"
        assert result["params"]["aspect"] == "16:9"
        assert result["params"]["count"] == 2

    @pytest.mark.asyncio
    async def test_image_t2i_count_gt_1_returns_all_files(
        self, temp_db: DataStore, tmp_path: Path
    ) -> None:
        """#1469: wait=True and task_payload return every image file generated for count > 1."""
        from gflow_cli.mcp.tools import gflow_generate_image, task_payload

        fake_client = _FakeFlowApiClient()
        fake_client.generate_images_batch = AsyncMock(
            return_value=[
                _FakeImage(media_name="media-img-1", workflow_id="wf-1", seed=101),
                _FakeImage(media_name="media-img-2", workflow_id="wf-2", seed=102),
                _FakeImage(media_name="media-img-3", workflow_id="wf-3", seed=103),
            ]
        )
        with (
            patch(
                "gflow_cli.mcp.tools._resolve_and_validate_profile",
                return_value="default",
            ),
            patch(
                "gflow_cli.worker.daemon.FlowApiClient",
                return_value=fake_client,
            ),
            patch(
                "gflow_cli.mcp.tools.get_settings",
                return_value=MagicMock(
                    resolved_db_path=lambda: temp_db.path,
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    timeout_seconds=30,
                    output_dir=tmp_path / "out",
                ),
            ),
            patch(
                "gflow_cli.worker.daemon.get_settings",
                return_value=MagicMock(
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    headless=True,
                    transport=None,
                    output_dir=tmp_path / "out",
                    max_credits=None,
                ),
            ),
        ):
            result = await gflow_generate_image(prompt="three cats", count=3)

        assert result["status"] == "completed"
        assert len(result["files"]) == 3
        assert len(set(result["files"])) == 3
        assert all(Path(p).is_file() for p in result["files"])
        for path, expected_media in zip(
            result["files"], ("media-img-1", "media-img-2", "media-img-3"), strict=True
        ):
            assert Path(path).stem.startswith(expected_media)
        payload = task_payload("default", result["task_id"], temp_db.path)
        assert payload["files"] == result["files"]

    @pytest.mark.asyncio
    async def test_openai_route_n2_returns_both_generated_images(
        self, temp_db: DataStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#1365 over #1469: ``n=2`` on /v1/images/generations reaches the worker and back."""
        import base64

        import httpx

        from gflow_cli.mcp.server import build_app

        monkeypatch.delenv("GFLOW_MCP_NO_SPEND", raising=False)
        fake_client = _FakeFlowApiClient()
        fake_client.generate_images_batch = AsyncMock(
            return_value=[
                _FakeImage(media_name="media-img-1", workflow_id="wf-1", seed=101),
                _FakeImage(media_name="media-img-2", workflow_id="wf-2", seed=102),
            ]
        )
        transport = httpx.ASGITransport(app=build_app(transport="http"))
        with (
            patch("gflow_cli.mcp.tools._resolve_and_validate_profile", return_value="default"),
            patch("gflow_cli.worker.daemon.FlowApiClient", return_value=fake_client),
            patch(
                "gflow_cli.mcp.tools.get_settings",
                return_value=MagicMock(
                    resolved_db_path=lambda: temp_db.path,
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    timeout_seconds=30,
                    output_dir=tmp_path / "out",
                ),
            ),
            patch(
                "gflow_cli.worker.daemon.get_settings",
                return_value=MagicMock(
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    headless=True,
                    transport=None,
                    output_dir=tmp_path / "out",
                    max_credits=None,
                ),
            ),
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
                resp = await c.post("/v1/images/generations", json={"prompt": "two cats", "n": 2})

        assert resp.status_code == 200, resp.text
        images = [base64.b64decode(d["b64_json"]) for d in resp.json()["data"]]
        assert len(images) == 2
        assert all(images)

    @pytest.mark.asyncio
    async def test_image_t2i_count_fewer_returned_lists_actual_files(
        self, temp_db: DataStore, tmp_path: Path
    ) -> None:
        """#1469: when Flow returns fewer than count images, files lists the actual outputs."""
        from gflow_cli.mcp.tools import gflow_generate_image

        fake_client = _FakeFlowApiClient()
        fake_client.generate_images_batch = AsyncMock(
            return_value=[
                _FakeImage(media_name="media-img-a", workflow_id="wf-a"),
                _FakeImage(media_name="media-img-b", workflow_id="wf-b"),
            ]
        )
        with (
            patch(
                "gflow_cli.mcp.tools._resolve_and_validate_profile",
                return_value="default",
            ),
            patch(
                "gflow_cli.worker.daemon.FlowApiClient",
                return_value=fake_client,
            ),
            patch(
                "gflow_cli.mcp.tools.get_settings",
                return_value=MagicMock(
                    resolved_db_path=lambda: temp_db.path,
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    timeout_seconds=30,
                    output_dir=tmp_path / "out",
                ),
            ),
            patch(
                "gflow_cli.worker.daemon.get_settings",
                return_value=MagicMock(
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    headless=True,
                    transport=None,
                    output_dir=tmp_path / "out",
                    max_credits=None,
                ),
            ),
        ):
            result = await gflow_generate_image(prompt="two cats", count=3)

        assert result["status"] == "completed"
        assert len(result["files"]) == 2
        assert len(set(result["files"])) == 2
        # #1489: the reply says an image is missing.
        assert result["warnings"] == [{"type": "images_missing", "requested": 3, "returned": 2}]

    def test_task_payload_falls_back_to_single_asset_without_operation_link(
        self, temp_db: DataStore, tmp_path: Path
    ) -> None:
        """Legacy/unlinked assets without operation_assets rows still resolve via flow_media_id."""
        from gflow_cli.data.models import AssetKind, AssetRecord, LocalFileRecord
        from gflow_cli.data.repository import DataRepository
        from gflow_cli.mcp.tools import task_payload
        from gflow_cli.worker.queue import QueueRepository

        img_file = tmp_path / "single.png"
        img_file.write_bytes(b"PNG")
        repo = DataRepository(temp_db)
        repo.upsert_asset(
            AssetRecord(
                id="asset-only-1",
                profile_name="default",
                flow_project_id="proj-1",
                flow_media_id="media-only-1",
                flow_workflow_id="wf-only-1",
                flow_media_generation_id=None,
                kind=AssetKind.IMAGE,
                status="ready",
                model=None,
                aspect_ratio=None,
                width=None,
                height=None,
                duration_seconds=None,
                seed=777,
                metadata_json={},
            )
        )
        repo.upsert_local_file(
            LocalFileRecord(
                id="lf-only-1",
                profile_name="default",
                asset_id="asset-only-1",
                path=img_file,
                media_type="image/png",
                bytes=3,
                sha256=None,
            )
        )
        qrepo = QueueRepository(temp_db)
        qrepo.enqueue_task("task-only-1", "default", "t2i", {"prompt": "x"})
        qrepo.update_task_status("task-only-1", status="completed", flow_media_id="media-only-1")

        payload = task_payload("default", "task-only-1", temp_db.path)
        assert payload["status"] == "completed"
        assert payload["files"] == [str(img_file)]

    @pytest.mark.asyncio
    async def test_image_task_payload_ignores_later_character_op_reusing_media_id(
        self, temp_db: DataStore, tmp_path: Path
    ) -> None:
        """#1469: later character ops reusing an image media id must not shadow task outputs."""
        import dataclasses

        from gflow_cli.data.models import (
            AssetKind,
            AssetRecord,
            LocalFileRecord,
            OperationAssetRole,
            OperationKind,
            OperationRecord,
        )
        from gflow_cli.data.repository import DataRepository
        from gflow_cli.mcp.tools import gflow_generate_image, task_payload
        from gflow_cli.worker.queue import QueueRepository

        fake_client = _FakeFlowApiClient()
        fake_client.generate_images_batch = AsyncMock(
            return_value=[
                _FakeImage(media_name="media-img-1", workflow_id="wf-1", seed=101),
                _FakeImage(media_name="media-img-2", workflow_id="wf-2", seed=102),
                _FakeImage(media_name="media-img-3", workflow_id="wf-3", seed=103),
            ]
        )
        with (
            patch(
                "gflow_cli.mcp.tools._resolve_and_validate_profile",
                return_value="default",
            ),
            patch(
                "gflow_cli.worker.daemon.FlowApiClient",
                return_value=fake_client,
            ),
            patch(
                "gflow_cli.mcp.tools.get_settings",
                return_value=MagicMock(
                    resolved_db_path=lambda: temp_db.path,
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    timeout_seconds=30,
                    output_dir=tmp_path / "out",
                ),
            ),
            patch(
                "gflow_cli.worker.daemon.get_settings",
                return_value=MagicMock(
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    headless=True,
                    transport=None,
                    output_dir=tmp_path / "out",
                    max_credits=None,
                ),
            ),
        ):
            result = await gflow_generate_image(prompt="three cats", count=3)

        assert len(result["files"]) == 3
        repo = DataRepository(temp_db)
        reused = repo.get_asset_by_flow_media_id("default", "media-img-1")
        assert reused is not None
        char_body_file = tmp_path / "char_body.png"
        char_body_file.write_bytes(b"CHAR_BODY")
        repo.upsert_asset(
            AssetRecord(
                id="asset-char-body",
                profile_name="default",
                flow_project_id="proj-abc",
                flow_media_id="media-char-body",
                flow_workflow_id=None,
                flow_media_generation_id=None,
                kind=AssetKind.IMAGE,
                status="ready",
                model=None,
                aspect_ratio=None,
                width=None,
                height=None,
                duration_seconds=None,
                seed=None,
                metadata_json={},
            )
        )
        repo.upsert_local_file(
            LocalFileRecord(
                id="lf-char-body",
                profile_name="default",
                asset_id="asset-char-body",
                path=char_body_file,
                media_type="image/png",
                bytes=9,
                sha256=None,
            )
        )
        char_op = dataclasses.replace(
            OperationRecord.minimal(
                id="op-char-later",
                profile_name="default",
                flow_project_id="proj-abc",
                mode=OperationKind.CHARACTER,
            ),
            started_at="2099-01-01T00:00:00.000Z",
        )
        repo.insert_operation(char_op)
        repo.link_operation_asset("op-char-later", reused.id, OperationAssetRole.OUTPUT, 0)
        repo.link_operation_asset("op-char-later", "asset-char-body", OperationAssetRole.OUTPUT, 1)

        # Primary lookup uses catalog_operation_id recorded on the task checkpoint.
        cp = QueueRepository(temp_db).read_checkpoint(result["task_id"])
        assert cp is not None
        assert isinstance(cp.get("catalog_operation_id"), str) and cp["catalog_operation_id"]
        payload = task_payload("default", result["task_id"], temp_db.path)
        assert payload["files"] == result["files"]
        assert str(char_body_file) not in payload["files"]

        # Legacy tasks without catalog_operation_id still scope fallback lookup by task mode.
        temp_db.conn.execute(
            "UPDATE generation_queue SET checkpoint_json = NULL WHERE task_id = ?",
            (result["task_id"],),
        )
        temp_db.conn.commit()
        legacy_payload = task_payload("default", result["task_id"], temp_db.path)
        assert legacy_payload["files"] == result["files"]
        assert str(char_body_file) not in legacy_payload["files"]

    @pytest.mark.asyncio
    async def test_image_explicit_output_file(self, temp_db: DataStore, tmp_path: Path) -> None:
        """When output is specified, the generated asset lands at the custom path."""
        from gflow_cli.mcp.tools import _TokenBucket, gflow_generate_image

        custom_output = tmp_path / "out" / "custom_sub" / "hero.png"
        with (
            patch(
                "gflow_cli.mcp.tools._rate_limiter",
                _TokenBucket(capacity=8, refill_rate=0.0),
            ),
            patch(
                "gflow_cli.mcp.tools._resolve_and_validate_profile",
                return_value="default",
            ),
            patch(
                "gflow_cli.worker.daemon.FlowApiClient",
                return_value=_FakeFlowApiClient(),
            ),
            patch(
                "gflow_cli.mcp.tools.get_settings",
                return_value=MagicMock(
                    resolved_db_path=lambda: temp_db.path,
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    timeout_seconds=30,
                    output_dir=tmp_path / "out",
                ),
            ),
            patch(
                "gflow_cli.worker.daemon.get_settings",
                return_value=MagicMock(
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    headless=True,
                    transport=None,
                    output_dir=tmp_path / "out",
                    max_credits=None,
                    history_prompts=False,
                ),
            ),
        ):
            result = await gflow_generate_image(
                prompt="custom output test",
                out_file=str(custom_output),
            )

        assert result["status"] == "completed"
        assert result["files"] == [str(custom_output)]
        assert custom_output.is_file()

    @pytest.mark.asyncio
    async def test_image_rate_limited_bypasses_worker(self, temp_db: DataStore) -> None:
        """Rate-limited calls must not invoke the worker at all."""
        from gflow_cli.mcp.tools import _TokenBucket, gflow_generate_image

        exhausted_bucket = _TokenBucket(capacity=0, refill_rate=0.0)
        with (
            patch("gflow_cli.mcp.tools._rate_limiter", exhausted_bucket),
            patch("gflow_cli.mcp.tools._run_generation_task") as mock_run,
        ):
            result = await gflow_generate_image(prompt="blocked")

        assert result["status"] == "rate_limited"
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# Video generation — wired path
# ---------------------------------------------------------------------------


class TestGenerateVideoWired:
    @pytest.mark.asyncio
    async def test_video_t2v_returns_completed(self, temp_db: DataStore, tmp_path: Path) -> None:
        """gflow_generate_video t2v should return status='completed' with the wired path."""
        from gflow_cli.mcp.tools import _TokenBucket, gflow_generate_video

        with (
            patch(
                "gflow_cli.mcp.tools._rate_limiter",
                _TokenBucket(capacity=8, refill_rate=0.0),
            ),
            patch(
                "gflow_cli.mcp.tools._resolve_and_validate_profile",
                return_value="default",
            ),
            patch(
                "gflow_cli.worker.daemon.FlowApiClient",
                return_value=_FakeFlowApiClient(),
            ),
            patch(
                "gflow_cli.mcp.tools.get_settings",
                return_value=MagicMock(
                    resolved_db_path=lambda: temp_db.path,
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    timeout_seconds=30,
                    output_dir=tmp_path / "out",
                ),
            ),
            patch(
                "gflow_cli.worker.daemon.get_settings",
                return_value=MagicMock(
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    headless=True,
                    transport=None,
                    output_dir=tmp_path / "out",
                    max_credits=None,
                ),
            ),
        ):
            result = await gflow_generate_video(prompt="cinematic drone over ocean")

        assert result["status"] == "completed"
        assert "flow_media_id" in result
        assert result["params"]["mode"] == "t2v"

    @pytest.mark.asyncio
    async def test_video_explicit_output_file(self, temp_db: DataStore, tmp_path: Path) -> None:
        """When output is specified for video, the generated video relocates to custom output."""
        from gflow_cli.mcp.tools import _TokenBucket, gflow_generate_video

        src_mp4 = tmp_path / "generated.mp4"
        src_mp4.write_bytes(b"MP4_FAKE")
        custom_output = tmp_path / "out" / "video_sub" / "clip.mp4"

        fake_client = _FakeFlowApiClient()
        fake_client.generate_video = AsyncMock(
            return_value=VideoResult(
                status=VideoStatus(
                    media_id="media-vid-wired",
                    status="MEDIA_GENERATION_STATUS_SUCCESSFUL",
                ),
                local_path=src_mp4,
                project_id="proj-abc",
                flow_operation_id="op-123",
            )
        )

        with (
            patch(
                "gflow_cli.mcp.tools._rate_limiter",
                _TokenBucket(capacity=8, refill_rate=0.0),
            ),
            patch(
                "gflow_cli.mcp.tools._resolve_and_validate_profile",
                return_value="default",
            ),
            patch(
                "gflow_cli.worker.daemon.FlowApiClient",
                return_value=fake_client,
            ),
            patch(
                "gflow_cli.mcp.tools.get_settings",
                return_value=MagicMock(
                    resolved_db_path=lambda: temp_db.path,
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    timeout_seconds=30,
                    output_dir=tmp_path / "out",
                    storage_uri=None,  # local storage: -o is allowed (#1008)
                ),
            ),
            patch(
                "gflow_cli.worker.daemon.get_settings",
                return_value=MagicMock(
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    headless=True,
                    transport=None,
                    output_dir=tmp_path / "out",
                    max_credits=None,
                    history_prompts=False,
                ),
            ),
        ):
            result = await gflow_generate_video(
                prompt="custom video test",
                out_file=str(custom_output),
            )

        assert result["status"] == "completed"
        assert custom_output.is_file()

    @pytest.mark.asyncio
    async def test_video_i2v_without_initial_frame_is_rejected(self) -> None:
        """i2v without initial_frame must fail fast at the tool boundary with a
        clear 400, not enqueue a task that dies on a cryptic ValueError."""
        from gflow_cli.mcp.tools import _TokenBucket, gflow_generate_video

        with (
            patch("gflow_cli.mcp.tools._rate_limiter", _TokenBucket(capacity=8, refill_rate=0.0)),
            patch(
                "gflow_cli.mcp.tools._resolve_and_validate_profile",
                return_value="default",
            ),
        ):
            result = await gflow_generate_video(prompt="pan across the scene", mode="i2v")

        assert result["status"] == "error"
        assert result["error"]["title"] == "Missing Start Image"
        assert result["error"]["status"] == 400

    @pytest.mark.asyncio
    async def test_video_r2v_without_any_reference_is_rejected(self) -> None:
        """r2v with neither images nor entities must fail fast at the tool boundary."""
        from gflow_cli.mcp.tools import _TokenBucket, gflow_generate_video

        with (
            patch("gflow_cli.mcp.tools._rate_limiter", _TokenBucket(capacity=8, refill_rate=0.0)),
            patch(
                "gflow_cli.mcp.tools._resolve_and_validate_profile",
                return_value="default",
            ),
        ):
            result = await gflow_generate_video(prompt="blend these refs", mode="r2v")

        assert result["status"] == "error"
        assert result["error"]["title"] == "Missing References"
        assert result["error"]["status"] == 400

    @pytest.mark.asyncio
    async def test_video_r2v_accepts_a_character_entity_as_its_reference(self) -> None:
        """A saved CHARACTER entity satisfies r2v on its own (#689).

        GenerateVideoRequest accepts "reference_images, ref_names, OR
        reference_entities"; the tool guard used to demand images, so an agent holding
        only an entity id could not reach r2v at all.
        """
        from gflow_cli.mcp.tools import _TokenBucket, gflow_generate_video

        with (
            patch("gflow_cli.mcp.tools._rate_limiter", _TokenBucket(capacity=8, refill_rate=0.0)),
            patch(
                "gflow_cli.mcp.tools._resolve_and_validate_profile",
                return_value="default",
            ),
            patch("gflow_cli.mcp.tools._run_generation_task") as run,
        ):
            run.return_value = {"status": "queued"}
            result = await gflow_generate_video(
                prompt="the same man, three angles",
                mode="r2v",
                reference_entities=["11111111-2222-3333-4444-555555555555"],
                wait=False,
            )

        assert result["status"] != "error", result
        payload = run.call_args.kwargs["payload"]
        assert payload["reference_entities"] == ["11111111-2222-3333-4444-555555555555"]

    @pytest.mark.asyncio
    async def test_video_failed_task_returns_error(
        self, temp_db: DataStore, tmp_path: Path
    ) -> None:
        """When the worker marks a task failed, gflow_generate_video returns status='failed'."""
        from gflow_cli.errors import FlowApiError
        from gflow_cli.mcp.tools import _TokenBucket, gflow_generate_video

        failing_client = _FakeFlowApiClient()
        failing_client.generate_video.side_effect = FlowApiError(
            429, "Rate limit exceeded", route="video.generate"
        )

        # Use a fresh full bucket to avoid cross-test token depletion.
        full_bucket = _TokenBucket(capacity=8, refill_rate=0.0)

        with (
            patch("gflow_cli.mcp.tools._rate_limiter", full_bucket),
            patch(
                "gflow_cli.mcp.tools._resolve_and_validate_profile",
                return_value="default",
            ),
            patch(
                "gflow_cli.worker.daemon.FlowApiClient",
                return_value=failing_client,
            ),
            patch(
                "gflow_cli.mcp.tools.get_settings",
                return_value=MagicMock(
                    resolved_db_path=lambda: temp_db.path,
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    timeout_seconds=30,
                    output_dir=tmp_path / "out",
                ),
            ),
            patch(
                "gflow_cli.worker.daemon.get_settings",
                return_value=MagicMock(
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    headless=True,
                    transport=None,
                    output_dir=tmp_path / "out",
                    max_credits=None,
                ),
            ),
        ):
            result = await gflow_generate_video(prompt="will fail")

        assert result["status"] == "failed"
        assert "error" in result
        assert result["error"]["title"] == "Flow API error"

    @pytest.mark.asyncio
    async def test_video_forwards_model_duration_count_to_payload(self) -> None:
        """model/duration/count must reach the generation payload so agents get
        the same model / length / batch control the CLI exposes (parity).

        Uses ``omni_flash`` because parity is the whole point: it is the only
        model Flow gives a duration control, and the CLI rejects ``--duration``
        on every Veo 3.1 model with exit 2 (#451/#288). This test previously
        passed ``veo_quality`` + ``duration=8`` and asserted it reached the
        payload — asserting, in the name of parity, the exact combination the
        CLI refuses. The MCP tool now returns a 400 for it too (#630).
        """
        from unittest.mock import AsyncMock

        from gflow_cli.mcp.tools import _TokenBucket, gflow_generate_video

        captured: dict[str, Any] = {}

        async def _fake_run(
            *, profile: str, task_type: str, payload: dict[str, Any], **kwargs: Any
        ) -> dict[str, Any]:
            captured.update(payload)
            return {"status": "completed", "files": []}

        with (
            patch("gflow_cli.mcp.tools._rate_limiter", _TokenBucket(capacity=8, refill_rate=0.0)),
            patch("gflow_cli.mcp.tools._resolve_and_validate_profile", return_value="default"),
            patch("gflow_cli.mcp.tools._run_generation_task", AsyncMock(side_effect=_fake_run)),
        ):
            result = await gflow_generate_video(
                prompt="a slow zoom", model="omni_flash", duration=8, count=2
            )

        assert result["status"] == "completed"
        assert captured["model"] == "omni_flash"
        assert captured["duration"] == 8
        assert captured["count"] == 2
        assert result["params"]["model"] == "omni_flash"

    @pytest.mark.asyncio
    async def test_video_omits_unset_model_and_duration_from_payload(self) -> None:
        """When model/duration are omitted, the payload must NOT carry them so the
        transport's own i2v veo-lite default (issue #125) still applies."""
        from unittest.mock import AsyncMock

        from gflow_cli.mcp.tools import _TokenBucket, gflow_generate_video

        captured: dict[str, Any] = {}

        async def _capture(
            *, profile: str, task_type: str, payload: dict[str, Any], **kwargs: Any
        ) -> dict[str, Any]:
            captured.update(payload)
            return {"status": "completed", "files": []}

        with (
            patch("gflow_cli.mcp.tools._rate_limiter", _TokenBucket(capacity=8, refill_rate=0.0)),
            patch("gflow_cli.mcp.tools._resolve_and_validate_profile", return_value="default"),
            patch("gflow_cli.mcp.tools._run_generation_task", AsyncMock(side_effect=_capture)),
        ):
            await gflow_generate_video(prompt="a slow zoom")

        assert "model" not in captured
        assert "duration" not in captured
        assert captured["count"] == 1  # count always defaults to 1

    @pytest.mark.asyncio
    async def test_video_invalid_model_is_rejected_before_enqueue(self) -> None:
        """An unknown model must fail fast at the tool boundary (400), not enqueue
        a task that dies on a cryptic ValueError deep in the worker."""
        from gflow_cli.mcp.tools import _TokenBucket, gflow_generate_video

        with (
            patch("gflow_cli.mcp.tools._rate_limiter", _TokenBucket(capacity=8, refill_rate=0.0)),
            patch("gflow_cli.mcp.tools._resolve_and_validate_profile", return_value="default"),
        ):
            result = await gflow_generate_video(prompt="x", model="not-a-real-model")

        assert result["status"] == "error"
        assert result["error"]["status"] == 400
        assert "model" in result["error"]["detail"].lower()


# ---------------------------------------------------------------------------
# gflow_list_projects — wired path
# ---------------------------------------------------------------------------


class TestListProjectsWired:
    @pytest.mark.asyncio
    async def test_list_projects_empty_db(self, temp_db: DataStore) -> None:
        """With an empty catalog, list_projects should return empty results."""
        from gflow_cli.mcp.tools import gflow_list_projects

        with patch(
            "gflow_cli.mcp.tools.get_settings",
            return_value=MagicMock(resolved_db_path=lambda: temp_db.path),
        ):
            result = await gflow_list_projects(profile="default")

        assert result["status"] == "ok"
        assert result["projects"] == []
        assert result["count"] == 0
        assert result["has_more"] is False
        assert result["next_offset"] is None

    @pytest.mark.asyncio
    async def test_list_projects_returns_data(self, temp_db: DataStore) -> None:
        """Projects seeded in the catalog are returned by gflow_list_projects."""
        import uuid

        from gflow_cli.mcp.tools import gflow_list_projects

        # Seed a project directly.
        temp_db.conn.execute(
            "INSERT INTO projects(id, profile_name, flow_project_id, title, source, created_at) "
            "VALUES (?, 'default', 'flow-proj-1', 'Test Project', 'cli', '2026-06-29T00:00:00Z')",
            (str(uuid.uuid4()),),
        )
        temp_db.conn.commit()

        with patch(
            "gflow_cli.mcp.tools.get_settings",
            return_value=MagicMock(resolved_db_path=lambda: temp_db.path),
        ):
            result = await gflow_list_projects(profile="default")

        assert result["status"] == "ok"
        assert result["count"] == 1
        assert result["has_more"] is False
        assert result["projects"][0]["project_id"] == "flow-proj-1"


# ---------------------------------------------------------------------------
# _run_generation_task — unit tests for the helper
# ---------------------------------------------------------------------------


class TestRunGenerationTask:
    @pytest.mark.asyncio
    async def test_task_enqueued_and_completed(self, temp_db: DataStore, tmp_path: Path) -> None:
        """_run_generation_task enqueues, runs worker, and returns completed status."""
        from gflow_cli.mcp.tools import _run_generation_task

        with (
            patch(
                "gflow_cli.worker.daemon.FlowApiClient",
                return_value=_FakeFlowApiClient(),
            ),
            patch(
                "gflow_cli.mcp.tools.get_settings",
                return_value=MagicMock(
                    resolved_db_path=lambda: temp_db.path,
                    profile_subdir=lambda _: tmp_path / "profile_default",
                ),
            ),
            patch(
                "gflow_cli.worker.daemon.get_settings",
                return_value=MagicMock(
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    headless=True,
                    transport=None,
                    output_dir=tmp_path / "out",
                    max_credits=None,
                ),
            ),
        ):
            result = await _run_generation_task(
                profile="default",
                task_type="t2i",
                payload={"prompt": "test helper", "aspect": "1:1", "count": 1},
            )

        assert result["status"] == "completed"
        assert "task_id" in result
        assert "flow_media_id" in result

    @pytest.mark.asyncio
    async def test_envelope_returns_media_id_not_workflow_id(
        self, temp_db: DataStore, tmp_path: Path
    ) -> None:
        """PR #245 review #2: the 'flow_media_id' key must carry the real media
        id, not the asset's flow_workflow_id, which is exposed separately."""
        from gflow_cli.mcp.tools import _run_generation_task

        with (
            patch(
                "gflow_cli.worker.daemon.FlowApiClient",
                return_value=_FakeFlowApiClient(),
            ),
            patch(
                "gflow_cli.mcp.tools.get_settings",
                return_value=MagicMock(
                    resolved_db_path=lambda: temp_db.path,
                    profile_subdir=lambda _: tmp_path / "profile_default",
                ),
            ),
            patch(
                "gflow_cli.worker.daemon.get_settings",
                return_value=MagicMock(
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    headless=True,
                    transport=None,
                    output_dir=tmp_path / "out",
                    max_credits=None,
                ),
            ),
        ):
            result = await _run_generation_task(
                profile="default",
                task_type="t2i",
                payload={"prompt": "envelope test", "aspect": "1:1", "count": 1},
            )

        assert result["status"] == "completed"
        # The fake asset has flow_media_id="media-img-wired", flow_workflow_id="workflow-123".
        assert result["flow_media_id"] == "media-img-wired"
        assert result["flow_workflow_id"] == "workflow-123"

    @pytest.mark.asyncio
    async def test_unknown_error_returns_error_status(
        self, temp_db: DataStore, tmp_path: Path
    ) -> None:
        """An unexpected exception in _run_generation_task returns status='error'."""
        from gflow_cli.mcp.tools import _run_generation_task

        with (
            patch(
                "gflow_cli.mcp.tools.DataStore",
                side_effect=RuntimeError("DB exploded"),
            ),
            patch(
                "gflow_cli.mcp.tools.get_settings",
                return_value=MagicMock(resolved_db_path=lambda: temp_db.path),
            ),
        ):
            result = await _run_generation_task(
                profile="default",
                task_type="t2i",
                payload={"prompt": "boom"},
            )

        assert result["status"] == "error"
        assert "error" in result


# ---------------------------------------------------------------------------
# --project / project_id parity (mirrors the CLI --project flag)
# ---------------------------------------------------------------------------


class TestProjectParam:
    """The MCP `project` arg must thread through to the task payload as
    `project_id` (which the worker already consumes), and reject a bad id."""

    @pytest.mark.asyncio
    async def test_image_project_threads_to_payload(self) -> None:
        from gflow_cli.mcp import tools as tools_mod

        captured: dict[str, Any] = {}

        async def _fake_run(
            *, profile: str, task_type: str, payload: dict[str, Any], **kwargs: Any
        ):
            captured["payload"] = payload
            return {"status": "completed", "files": [], "flow_media_id": "m"}

        with (
            patch(
                "gflow_cli.mcp.tools._rate_limiter",
                tools_mod._TokenBucket(capacity=8, refill_rate=0.0),
            ),
            patch("gflow_cli.mcp.tools._resolve_and_validate_profile", return_value="default"),
            patch.object(tools_mod, "_run_generation_task", _fake_run),
        ):
            result = await tools_mod.gflow_generate_image(prompt="a cat", project="PROJ123")

        assert captured["payload"]["project_id"] == "PROJ123"
        assert result["params"]["project"] == "PROJ123"

    @pytest.mark.asyncio
    async def test_video_project_threads_to_payload(self) -> None:
        from gflow_cli.mcp import tools as tools_mod

        captured: dict[str, Any] = {}

        async def _fake_run(
            *, profile: str, task_type: str, payload: dict[str, Any], **kwargs: Any
        ):
            captured["payload"] = payload
            return {"status": "completed", "files": [], "flow_media_id": "m"}

        with (
            patch(
                "gflow_cli.mcp.tools._rate_limiter",
                tools_mod._TokenBucket(capacity=8, refill_rate=0.0),
            ),
            patch("gflow_cli.mcp.tools._resolve_and_validate_profile", return_value="default"),
            patch.object(tools_mod, "_run_generation_task", _fake_run),
        ):
            result = await tools_mod.gflow_generate_video(prompt="a dog", project="PROJ123")

        assert captured["payload"]["project_id"] == "PROJ123"
        assert result["params"]["project"] == "PROJ123"

    @pytest.mark.asyncio
    async def test_image_omitted_project_has_no_project_id(self) -> None:
        from gflow_cli.mcp import tools as tools_mod

        captured: dict[str, Any] = {}

        async def _fake_run(
            *, profile: str, task_type: str, payload: dict[str, Any], **kwargs: Any
        ):
            captured["payload"] = payload
            return {"status": "completed", "files": [], "flow_media_id": "m"}

        with (
            patch(
                "gflow_cli.mcp.tools._rate_limiter",
                tools_mod._TokenBucket(capacity=8, refill_rate=0.0),
            ),
            patch("gflow_cli.mcp.tools._resolve_and_validate_profile", return_value="default"),
            patch.object(tools_mod, "_run_generation_task", _fake_run),
        ):
            await tools_mod.gflow_generate_image(prompt="a cat")

        assert "project_id" not in captured["payload"]

    @pytest.mark.asyncio
    async def test_image_bad_project_rejected_before_worker(self) -> None:
        from gflow_cli.mcp import tools as tools_mod

        result = await tools_mod.gflow_generate_image(prompt="a cat", project="bad/id")
        assert result["status"] == "error"
        assert result["error"]["title"] == "Invalid Project Id"

    @pytest.mark.asyncio
    async def test_video_bad_project_rejected_before_worker(self) -> None:
        from gflow_cli.mcp import tools as tools_mod

        result = await tools_mod.gflow_generate_video(prompt="a dog", project="bad/id")
        assert result["status"] == "error"
        assert result["error"]["title"] == "Invalid Project Id"

    @pytest.mark.asyncio
    async def test_video_output_naming_a_directory_rejected_before_worker(
        self, tmp_path: Path
    ) -> None:
        # #1114 spike arm E: shutil.move drops the clip INSIDE a directory and the row
        # records the directory as the clip; the CLI refuses it via dir_okay=False.
        from gflow_cli.mcp import tools as tools_mod

        with patch.object(tools_mod, "_run_generation_task") as run:
            result = await tools_mod.gflow_generate_video(prompt="a dog", out_file=str(tmp_path))
        assert result["status"] == "error"
        assert result["error"]["title"] == "Invalid Output"
        run.assert_not_called()
