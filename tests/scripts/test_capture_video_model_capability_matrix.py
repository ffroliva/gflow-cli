"""Tests for the live capability matrix classifier."""

from scripts.dev.capture_video_model_capability_matrix import _classify


def test_classify_detects_interactive_duration_labels() -> None:
    result = _classify(
        [
            {"label": "4s"},
            {"label": "6s"},
            {"label": "8s"},
            {"label": "x1"},
            {"label": "16:9"},
        ]
    )
    assert result["duration"] == ["4s", "6s", "8s"]
    assert result["count"] == ["x1"]
    assert result["aspect"] == ["16:9"]
