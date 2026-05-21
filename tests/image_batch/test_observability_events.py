"""Unit tests for the four new application-layer observability events
emitted by ``run_manifest_image_batch``. Each event MUST fire once per
submission row (with the documented field schema).

Spec: docs/superpowers/specs/2026-05-21-multi-image-prompt-design.md §3, §5, §8.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog
from structlog.testing import LogCapture

from gflow_cli.image_batch import BatchPromptItem, run_manifest_image_batch


@pytest.fixture
def log_capture():
    """Capture structlog events; reset config on teardown to avoid test bleed."""
    capture = LogCapture()
    structlog.configure(processors=[capture])
    try:
        yield capture
    finally:
        structlog.reset_defaults()


@pytest.fixture
def fake_client_factory() -> MagicMock:
    """Factory producing an async-context-manager client with stubbed methods."""
    factory = MagicMock()
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    project = MagicMock()
    project.project_id = "project-abc123"
    client.create_project = AsyncMock(return_value=project)

    stub_image = MagicMock(bytes_=b"\x89PNG\r\n\x1a\n", filename="a.png")
    client.generate_image = AsyncMock(return_value=stub_image)
    client.generate_images_batch = AsyncMock(return_value=[stub_image])

    async def _download(_img, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x89PNG\r\n\x1a\n")
        return target

    client.download_image = AsyncMock(side_effect=_download)

    factory.return_value = client
    return factory


@pytest.mark.asyncio
async def test_emits_submission_attempt_per_row(
    tmp_path: Path,
    log_capture: LogCapture,
    fake_client_factory: MagicMock,
) -> None:
    prompts = (
        BatchPromptItem(text="cat", count=1, aspect_ratio="1:1", model="nano2"),
        BatchPromptItem(text="dog", count=1, aspect_ratio="1:1", model="nano2"),
    )
    await run_manifest_image_batch(
        profile_dir=tmp_path,
        headless=True,
        transport=None,
        prompts=prompts,
        output_dir=tmp_path / "out",
        continue_on_error=False,
        project_title="t",
        same_project=False,
        jitter_range=(0.0, 0.0),
        client_factory=fake_client_factory,
    )
    attempts = [e for e in log_capture.entries if e["event"] == "image_batch.submission_attempt"]
    assert len(attempts) == 2
    assert attempts[0]["row_idx"] == 0
    assert attempts[1]["row_idx"] == 1
    expected_hash = hashlib.sha256(b"cat").hexdigest()[:12]
    assert attempts[0]["prompt_hash"] == expected_hash
    assert attempts[0]["same_project"] is False


@pytest.mark.asyncio
async def test_emits_submission_result_per_row(
    tmp_path: Path,
    log_capture: LogCapture,
    fake_client_factory: MagicMock,
) -> None:
    prompts = (BatchPromptItem(text="cat", count=1, aspect_ratio="1:1", model="nano2"),)
    await run_manifest_image_batch(
        profile_dir=tmp_path,
        headless=True,
        transport=None,
        prompts=prompts,
        output_dir=tmp_path / "out",
        continue_on_error=False,
        project_title="t",
        same_project=False,
        jitter_range=(0.0, 0.0),
        client_factory=fake_client_factory,
    )
    results = [e for e in log_capture.entries if e["event"] == "image_batch.submission_result"]
    assert len(results) == 1
    assert results[0]["outcome"] == "ok"
    assert "latency_ms" in results[0]


@pytest.mark.asyncio
async def test_emits_row_completed_per_row(
    tmp_path: Path,
    log_capture: LogCapture,
    fake_client_factory: MagicMock,
) -> None:
    prompts = (BatchPromptItem(text="cat", count=1, aspect_ratio="1:1", model="nano2"),)
    await run_manifest_image_batch(
        profile_dir=tmp_path,
        headless=True,
        transport=None,
        prompts=prompts,
        output_dir=tmp_path / "out",
        continue_on_error=False,
        project_title="t",
        same_project=False,
        jitter_range=(0.0, 0.0),
        client_factory=fake_client_factory,
    )
    completed = [e for e in log_capture.entries if e["event"] == "image_batch.row_completed"]
    assert len(completed) >= 1
    assert "file_path" in completed[0]
    assert "sha256_prefix" in completed[0]


@pytest.mark.asyncio
async def test_emits_inter_submission_latency_for_subsequent_rows(
    tmp_path: Path,
    log_capture: LogCapture,
    fake_client_factory: MagicMock,
) -> None:
    """The first row has no prior submission, so the latency event fires only
    starting from row 1 (in same_project=True mode)."""
    prompts = (
        BatchPromptItem(text="a", count=1, aspect_ratio="1:1", model="nano2"),
        BatchPromptItem(text="b", count=1, aspect_ratio="1:1", model="nano2"),
        BatchPromptItem(text="c", count=1, aspect_ratio="1:1", model="nano2"),
    )
    await run_manifest_image_batch(
        profile_dir=tmp_path,
        headless=True,
        transport=None,
        prompts=prompts,
        output_dir=tmp_path / "out",
        continue_on_error=False,
        project_title="t",
        same_project=True,
        jitter_range=(0.0, 0.0),
        client_factory=fake_client_factory,
    )
    latencies = [
        e for e in log_capture.entries if e["event"] == "image_batch.inter_submission_latency_ms"
    ]
    assert len(latencies) == 2  # rows 1 and 2; row 0 has no prior
