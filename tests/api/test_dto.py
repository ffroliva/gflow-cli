"""DTO parser tests — every `from_*_response` validated against captured shapes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gflow_cli.api.dto import AssetInfo, ProjectInfo, VideoStatus

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


class TestVideoStatus:
    def test_parse_real_check_status_item(self) -> None:
        sample = _load("03_batchCheckAsyncVideoGenerationStatus.json")
        items = sample["response_body_parsed"]["media"]
        statuses = [VideoStatus.from_check_status_item(it) for it in items]
        assert len(statuses) == 1
        s = statuses[0]
        assert s.media_name == "<UUID>"
        assert s.project_id == "<UUID>"
        assert s.status == "MEDIA_GENERATION_STATUS_PENDING"
        assert s.operation_name == "<UUID>"
        assert s.is_terminal is False
        assert s.succeeded is False

    def test_terminal_completed(self) -> None:
        s = VideoStatus(
            media_name="m",
            project_id="p",
            status="MEDIA_GENERATION_STATUS_COMPLETED",
            operation_name=None,
            workflow_id=None,
        )
        assert s.is_terminal is True
        assert s.succeeded is True

    def test_terminal_failed(self) -> None:
        s = VideoStatus(
            media_name="m",
            project_id="p",
            status="MEDIA_GENERATION_STATUS_FAILED",
            operation_name=None,
            workflow_id=None,
        )
        assert s.is_terminal is True
        assert s.succeeded is False
