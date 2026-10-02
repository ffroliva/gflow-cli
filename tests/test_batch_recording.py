"""Every successful batch row is recorded, and a reference is tracked (#913).

SCENARIO #31, #31a (metadata half), #31b. `gflow run --config` and multi-prompt
`gflow image t2i` share ``run_image_batch``, which recorded failures only; their
successes never reached the catalog.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from gflow_cli.api.image import Aspect, GenerateImageRequest, ImageRef, Model
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.errors import DataStoreError, MediaAttributionError
from gflow_cli.image_batch import BatchPromptItem, run_image_batch
from tests.test_batch_outcomes import FakeClient

PROJECT = "11111111-1111-1111-1111-111111111111"


def _run(tmp_path: Path, recorder: MagicMock, *, fail_download: set[str] | None = None):
    client = FakeClient(fail_generate=set(), fail_download=fail_download or set())
    return asyncio.run(
        run_image_batch(
            profile_dir=tmp_path,
            headless=True,
            transport=None,
            prompts=(BatchPromptItem(text="a", index=0), BatchPromptItem(text="b", index=1)),
            output_dir=tmp_path / "out",
            continue_on_error=True,
            project_title="t",
            client_factory=lambda **_: client,
            jitter_range=(0, 0),
            _profile_name="p",
            _recorder=recorder,
            _command="run",
        )
    )


def test_successful_rows_are_recorded(tmp_path: Path) -> None:
    recorder = MagicMock()
    outcomes = _run(tmp_path, recorder)
    assert [o.status for o in outcomes] == ["ok", "ok"]
    calls = recorder.record_generated_images.call_args_list
    assert len(calls) == 2
    for call, text in zip(calls, ["a", "b"], strict=True):
        kw = call.kwargs
        assert kw["request"].prompt == text
        assert kw["project"].project_id == PROJECT
        assert kw["operation_kind"] == "t2i"
        assert kw["input_media_ids"] == []
        assert [i.media_name for i in kw["images"]] == [f"media-{text}"]
        assert len(kw["saved_paths"]) == 1


def test_a_recorder_failure_does_not_fail_the_row(tmp_path: Path) -> None:
    recorder = MagicMock()
    recorder.record_generated_images.side_effect = DataStoreError(detail="db locked")
    outcomes = _run(tmp_path, recorder)
    assert [o.status for o in outcomes] == ["ok", "ok"]


def test_a_failed_download_is_recorded_as_a_failure_not_a_success(tmp_path: Path) -> None:
    recorder = MagicMock()
    _run(tmp_path, recorder, fail_download={"a"})
    recorded = [c.kwargs["request"].prompt for c in recorder.record_generated_images.call_args_list]
    assert recorded == ["b"]
    assert recorder.record_failed_operation.called


def test_reference_media_ids_are_kept_in_operation_metadata() -> None:
    # Lineage survives even when the parent's asset row is missing (its download
    # failed), because the ids are stored on the child's operation itself.
    request = GenerateImageRequest(
        prompt="green",
        aspect=Aspect.from_cli("1:1"),
        model=Model.from_cli("nano2"),
        refs=(ImageRef(name="22222222-2222-2222-2222-222222222222", display_name="red"),),
    )
    metadata = OperationRecorder._generation_metadata(MagicMock(), request)  # noqa: SLF001
    assert metadata["reference_media_ids"] == ["22222222-2222-2222-2222-222222222222"]


def test_a_media_collision_fails_that_row_and_the_run_goes_on(tmp_path: Path) -> None:
    # Council #913 review: the run path did not catch it, so one collision ended the run.
    recorder = MagicMock()
    recorder.record_generated_images.side_effect = [
        MediaAttributionError(detail="media-a already belongs to another asset"),
        None,
    ]
    outcomes = _run(tmp_path, recorder)
    assert [o.status for o in outcomes] == ["fail", "ok"]
    assert outcomes[0].images  # the generation happened; the child-facing handle is kept
    assert not recorder.record_failed_operation.called  # not a failed generation
