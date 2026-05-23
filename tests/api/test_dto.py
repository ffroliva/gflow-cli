"""DTO parser tests — every `from_*_response` validated against captured shapes."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from gflow_cli.api.dto import AssetInfo, ProjectInfo

CAPTURED = Path(__file__).parent.parent.parent / "samples" / "captured"


def _load(filename: str) -> dict:
    """Load a captured sample. Files use a {request_*, response_body_parsed} envelope."""
    return json.loads((CAPTURED / filename).read_text(encoding="utf-8"))


class TestProjectInfo:
    def test_parse_real_create_response(self) -> None:
        sample = _load("05_createProject.json")
        body = sample["response_body_parsed"]
        p = ProjectInfo.from_create_response(body)
        assert p.project_id == "<UUID>"  # sanitised in fixture
        assert p.title == "May 08, 11:54 PM"

    def test_missing_keys_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="createProject"):
            ProjectInfo.from_create_response({"unrelated": True})

    def test_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        p = ProjectInfo(project_id="x", title="y")
        with pytest.raises(FrozenInstanceError):
            p.project_id = "other"  # type: ignore[misc]


class TestAssetInfo:
    def test_parse_real_upload_response(self) -> None:
        sample = _load("01_upload_image.json")
        body = sample["response_body_parsed"]
        a = AssetInfo.from_upload_response(body)
        assert a.name == "<UUID>"
        assert a.project_id == "<UUID>"
        assert a.workflow_id == "<UUID>"
        assert a.display_name == "s2c1.png"
        assert a.width == 768
        assert a.height == 1376

    def test_missing_media_raises(self) -> None:
        with pytest.raises(ValueError, match="uploadImage"):
            AssetInfo.from_upload_response({"workflow": {}})


# ---------------------------------------------------------------------------
# BatchSubmissionResult
# ---------------------------------------------------------------------------


def test_batch_submission_result_is_frozen() -> None:
    from gflow_cli.api.dto import BatchSubmissionResult, GeneratedImage

    img = GeneratedImage(
        media_name="m1",
        workflow_id="w1",
        seed=0,
        prompt="hi",
        model_name_type="NARWHAL",
        aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
        fife_url="https://x",
        dimensions=(1, 1),
    )
    result = BatchSubmissionResult(
        status="ok",
        project_id="proj-1",
        prompt_idx=0,
        prompt_hash="abc12345",
        images=(img,),
        error=None,
    )
    assert result.status == "ok"
    assert result.project_id == "proj-1"
    assert result.prompt_idx == 0
    assert result.prompt_hash == "abc12345"
    assert result.images == (img,)
    assert result.error is None

    # Frozen — mutation must raise
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = "fail"  # type: ignore[misc]


def test_batch_submission_result_fail_status() -> None:
    from gflow_cli.api.dto import BatchSubmissionResult
    from gflow_cli.errors import GFlowError

    err = GFlowError(detail="boom", route="x")
    result = BatchSubmissionResult(
        status="fail",
        project_id="proj-1",
        prompt_idx=2,
        prompt_hash="deadbeef",
        images=(),
        error=err,
    )
    assert result.status == "fail"
    assert result.images == ()
    assert result.error is err
