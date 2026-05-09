"""Models — frozen dataclasses, JobStatus enum."""

from __future__ import annotations

from pathlib import Path

import pytest

from flow_cli.models import Asset, GenerationJob, GenerationRequest, JobStatus


class TestJobStatus:
    def test_values(self) -> None:
        assert JobStatus.PENDING.value == "pending"
        assert JobStatus.RUNNING.value == "running"
        assert JobStatus.SUCCEEDED.value == "succeeded"
        assert JobStatus.FAILED.value == "failed"


class TestAsset:
    def test_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        a = Asset(uuid="u", media_url="https://example", kind="image")
        with pytest.raises(FrozenInstanceError):
            a.uuid = "other"  # type: ignore[misc]


class TestGenerationRequest:
    def test_defaults(self) -> None:
        req = GenerationRequest(start_image=Path("a.png"), motion_prompt="x")
        assert req.aspect == "9:16"
        assert req.end_image is None


class TestGenerationJob:
    def test_pending_is_not_terminal(self) -> None:
        job = GenerationJob(job_id="j", status=JobStatus.PENDING)
        assert job.status not in (JobStatus.SUCCEEDED, JobStatus.FAILED)
