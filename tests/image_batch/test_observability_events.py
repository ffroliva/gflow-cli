"""Unit tests for the application-layer observability events emitted by
``run_manifest_image_batch``. Each event MUST fire once per submission row
(with the documented field schema).

Spec: docs/superpowers/specs/2026-05-22-stay-mounted-batch-session-design.md §4.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import structlog
from structlog.testing import LogCapture

from gflow_cli.api.transports.ui_automation import UiAutomationTransport
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


def _make_fake_image() -> object:
    from gflow_cli.api.dto import GeneratedImage

    return GeneratedImage(
        media_name="m1",
        workflow_id="wf1",
        seed=1,
        prompt="p",
        model_name_type="NARWHAL",
        aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
        fife_url="https://example.com/img.png",
        dimensions=(64, 64),
    )


def _make_transport_stub(project_id: str = "project-abc123") -> UiAutomationTransport:
    """Build a UiAutomationTransport-shaped stub with generate_images_batch mocked."""
    from gflow_cli.api.dto import BatchSubmissionResult

    fake_image = _make_fake_image()
    transport = UiAutomationTransport.__new__(UiAutomationTransport)

    async def fake_batch(prompts, *, jitter_range, continue_on_error=False):  # type: ignore[no-untyped-def]
        results = []
        for idx, req in enumerate(prompts):
            ph = hashlib.sha256(req.prompt.encode("utf-8")).hexdigest()[:8]
            results.append(
                BatchSubmissionResult(
                    status="ok",
                    project_id=project_id,
                    prompt_idx=idx,
                    prompt_hash=ph,
                    images=(fake_image,) * req.count,
                )
            )
        return results

    transport.generate_images_batch = fake_batch  # type: ignore[method-assign]
    return transport


def _make_client_factory(
    project_id: str = "project-abc123",
) -> type:
    """Factory producing a client whose .transport is a UiAutomationTransport stub."""

    class _FakeClient:
        def __init__(self, **_: object) -> None:
            self.transport = _make_transport_stub(project_id=project_id)

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_: object) -> None:
            pass

        async def download_image(self, img: object, target: Path) -> Path:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"\x89PNG\r\n\x1a\n")
            return target

    return _FakeClient


@pytest.mark.asyncio
async def test_emits_submission_attempt_per_row(
    tmp_path: Path,
    log_capture: LogCapture,
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
        jitter_range=(0.0, 0.0),
        client_factory=_make_client_factory(),
    )
    attempts = [e for e in log_capture.entries if e["event"] == "image_batch.submission_attempt"]
    assert len(attempts) == 2
    assert attempts[0]["row_idx"] == 0
    assert attempts[1]["row_idx"] == 1
    expected_hash = hashlib.sha256(b"cat").hexdigest()[:12]
    assert attempts[0]["prompt_hash"] == expected_hash


@pytest.mark.asyncio
async def test_emits_row_completed_per_row(
    tmp_path: Path,
    log_capture: LogCapture,
) -> None:
    """Each ok result produces an image_batch.row_completed event with
    the new schema (prompt_hash, project_id, outcome fields)."""
    # Give the transport stub images so _download_results has something to emit.
    from gflow_cli.api.dto import BatchSubmissionResult, GeneratedImage

    fake_image = GeneratedImage(
        media_name="m1",
        workflow_id="wf1",
        seed=1,
        prompt="cat",
        model_name_type="NARWHAL",
        aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
        fife_url="https://example.com/img.png",
        dimensions=(64, 64),
    )

    class _ClientWithImage:
        def __init__(self, **_: object) -> None:
            transport = UiAutomationTransport.__new__(UiAutomationTransport)

            async def fake_batch(prompts, *, jitter_range, continue_on_error=False):  # type: ignore[no-untyped-def]
                return [
                    BatchSubmissionResult(
                        status="ok",
                        project_id="proj-xyz",
                        prompt_idx=0,
                        prompt_hash="aabbccdd",
                        images=(fake_image,),
                    )
                ]

            transport.generate_images_batch = fake_batch  # type: ignore[method-assign]
            self.transport = transport

        async def __aenter__(self) -> _ClientWithImage:
            return self

        async def __aexit__(self, *_: object) -> None:
            pass

        async def download_image(self, img: object, target: Path) -> Path:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"\x89PNG\r\n\x1a\n")
            return target

    prompts = (
        BatchPromptItem(
            text="cat", count=1, aspect_ratio="1:1", model="nano2", output_filename="p0"
        ),
    )
    await run_manifest_image_batch(
        profile_dir=tmp_path,
        headless=True,
        transport=None,
        prompts=prompts,
        output_dir=tmp_path / "out",
        continue_on_error=False,
        project_title="t",
        jitter_range=(0.0, 0.0),
        client_factory=_ClientWithImage,
    )
    completed = [e for e in log_capture.entries if e["event"] == "image_batch.row_completed"]
    assert len(completed) >= 1
    assert completed[0]["outcome"] == "ok"
    assert "sha256_prefix" in completed[0]
    assert "prompt_hash" in completed[0]
    assert "project_id" in completed[0]
