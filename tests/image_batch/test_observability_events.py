"""Unit tests for the application-layer observability events emitted by
``run_manifest_image_batch``. Each event MUST fire once per submission row
(with the documented field schema).

Spec: docs/superpowers/specs/2026-05-22-stay-mounted-batch-session-design.md §4.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import pytest
from structlog.testing import LogCapture

from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.image_batch import BatchPromptItem, run_manifest_image_batch

# ---------------------------------------------------------------------------
# FakeRecorder for recorder-observability tests
# ---------------------------------------------------------------------------


class FakeRecorder:
    def __init__(self) -> None:
        self.generated: list[dict] = []
        self.uploads: list[dict] = []
        self.closed = False
        self.fail_index: int | None = None  # if set, raise DataStoreError on that 0-based call
        # media_names the #281 pre-download guard should treat as already-recorded.
        self.recorded_media_ids: set[str] = set()

    def close(self) -> None:
        self.closed = True

    def record_upload_image(self, **kwargs):  # type: ignore[no-untyped-def]
        self.uploads.append(kwargs)

    def record_generated_images(self, **kwargs):  # type: ignore[no-untyped-def]
        idx = len(self.generated)
        self.generated.append(kwargs)
        if self.fail_index is not None and idx == self.fail_index:
            from gflow_cli.errors import DataStoreError

            raise DataStoreError(detail="boom", route="test")

    def is_media_recorded(self, *, profile_name: str, flow_media_id: str) -> bool:
        return flow_media_id in self.recorded_media_ids

    def verify_media_attribution(
        self, *, profile_name: str, images: Sequence[GeneratedImage]
    ) -> None:
        """Mirrors ``OperationRecorder.verify_media_attribution`` (issue #283)."""
        from gflow_cli.errors import MediaAttributionError

        already_recorded = [
            img.media_name
            for img in images
            if self.is_media_recorded(profile_name=profile_name, flow_media_id=img.media_name)
        ]
        if already_recorded:
            msg = (
                "the driver returned media that already exists in local history — "
                "wrong-media attribution (#281); nothing was downloaded: "
                f"{', '.join(already_recorded)}"
            )
            raise MediaAttributionError(msg)


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
    install_log_capture: LogCapture,
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
        jitter_range=(0.0, 0.0),
        client_factory=_make_client_factory(),
    )
    attempts = [
        e for e in install_log_capture.entries if e["event"] == "image_batch.submission_attempt"
    ]  # noqa: E501
    assert len(attempts) == 2
    assert attempts[0]["row_idx"] == 0
    assert attempts[1]["row_idx"] == 1
    expected_hash = hashlib.sha256(b"cat").hexdigest()[:12]
    assert attempts[0]["prompt_hash"] == expected_hash


@pytest.mark.asyncio
async def test_emits_row_completed_per_row(
    tmp_path: Path,
    install_log_capture: LogCapture,
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
        jitter_range=(0.0, 0.0),
        client_factory=_ClientWithImage,
    )
    completed = [
        e for e in install_log_capture.entries if e["event"] == "image_batch.row_completed"
    ]  # noqa: E501
    assert len(completed) >= 1
    assert completed[0]["outcome"] == "ok"
    assert "sha256_prefix" in completed[0]
    assert "prompt_hash" in completed[0]
    assert "project_id" in completed[0]


@pytest.mark.asyncio
async def test_recorder_persistence_failure_emits_event_and_continues(
    tmp_path: Path,
    install_log_capture: LogCapture,
) -> None:
    """DataStoreError on row 0 record emits data.persistence_failed_after_success,
    does NOT stop row 1 from being recorded, and both files are saved on disk."""
    recorder = FakeRecorder()
    recorder.fail_index = 0  # row 0 recorder call raises DataStoreError

    prompts = (
        BatchPromptItem(
            text="cat", count=1, aspect_ratio="1:1", model="nano2", output_filename="p0", index=0
        ),
        BatchPromptItem(
            text="dog", count=1, aspect_ratio="1:1", model="nano2", output_filename="p1", index=1
        ),
    )
    out_dir = tmp_path / "out"

    await run_manifest_image_batch(
        profile_dir=tmp_path,
        headless=True,
        transport=None,
        prompts=prompts,
        output_dir=out_dir,
        continue_on_error=True,
        jitter_range=(0.0, 0.0),
        client_factory=_make_client_factory(project_id="flow-project-batch"),
        profile_name="default",
        recorder=recorder,
    )

    # structlog stream must contain a persistence_failed_after_success event for row 0
    warn_events = [
        e
        for e in install_log_capture.entries
        if e["event"] == "data.persistence_failed_after_success"
    ]
    assert len(warn_events) >= 1, f"Expected warning event, got: {install_log_capture.entries}"

    # Row 1 must still be recorded despite row 0 failure
    assert len(recorder.generated) == 2, (
        f"Expected 2 recorder entries (row 0 fails but is still appended, row 1 succeeds), "
        f"got {len(recorder.generated)}"
    )

    # Both files must exist on disk
    saved_files = list(out_dir.rglob("*.png"))
    assert len(saved_files) == 2, f"Expected 2 saved files, got {saved_files}"
