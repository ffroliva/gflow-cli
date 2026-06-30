# SPDX-License-Identifier: MIT
"""Tests for the wired MCP tool execution path.

These tests verify that the generation tools enqueue tasks, invoke
FlowWorker.process_task, and return structured results — without actually
launching a Chrome browser.  FlowApiClient is patched out so the tests
run offline and quickly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.data.store import DataStore

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def temp_db(tmp_path: Path) -> DataStore:
    """Isolated DataStore with a seeded 'default' profile row."""
    db_file = tmp_path / "gflow_test.db"
    store = DataStore.open(db_file)
    store.conn.execute(
        "INSERT INTO profiles(name, profile_dir, first_seen_at) "
        "VALUES ('default', '/profiles/default', '2026-06-29T00:00:00Z')"
    )
    store.conn.commit()
    return store


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


@dataclass
class _FakeVideoStatus:
    media_id: str
    status: str = "completed"


@dataclass
class _FakeVideo:
    status: _FakeVideoStatus
    local_path: Path | None = None
    project_id: str | None = "proj-abc"
    flow_operation_id: str | None = "op-123"


class _FakeFlowApiClient:
    def __init__(self, **kwargs: Any):
        self.generate_image = AsyncMock(return_value=_FakeImage(media_name="media-img-wired"))
        self.generate_images_batch = AsyncMock(
            return_value=[_FakeImage(media_name="media-img-wired")]
        )
        self.generate_video = AsyncMock(
            return_value=_FakeVideo(status=_FakeVideoStatus(media_id="media-vid-wired"))
        )
        self.create_project = AsyncMock(
            return_value=MagicMock(project_id="proj-abc", title="Test Project")
        )
        self.download_image = AsyncMock(return_value=Path("/tmp/fake.png"))

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
                ),
            ),
            patch(
                "gflow_cli.worker.daemon.get_settings",
                return_value=MagicMock(
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    headless=True,
                    transport=None,
                    output_dir=tmp_path / "out",
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
                ),
            ),
            patch(
                "gflow_cli.worker.daemon.get_settings",
                return_value=MagicMock(
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    headless=True,
                    transport=None,
                    output_dir=tmp_path / "out",
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
        from gflow_cli.mcp.tools import gflow_generate_video

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
                ),
            ),
            patch(
                "gflow_cli.worker.daemon.get_settings",
                return_value=MagicMock(
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    headless=True,
                    transport=None,
                    output_dir=tmp_path / "out",
                ),
            ),
        ):
            result = await gflow_generate_video(prompt="cinematic drone over ocean")

        assert result["status"] == "completed"
        assert "flow_media_id" in result
        assert result["params"]["mode"] == "t2v"

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
                ),
            ),
            patch(
                "gflow_cli.worker.daemon.get_settings",
                return_value=MagicMock(
                    profile_subdir=lambda _: tmp_path / "profile_default",
                    headless=True,
                    transport=None,
                    output_dir=tmp_path / "out",
                ),
            ),
        ):
            result = await gflow_generate_video(prompt="will fail")

        assert result["status"] == "failed"
        assert "error" in result
        assert result["error"]["title"] == "Flow API error"


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
        assert result["total"] == 0

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
        assert result["total"] == 1
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
