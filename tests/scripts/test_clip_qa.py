"""Tests for the temporal contact-sheet strip generator in clip_qa.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_MOD_PATH = Path(__file__).resolve().parents[2] / "skills" / "video-production" / "clip_qa.py"
_spec = importlib.util.spec_from_file_location("clip_qa", _MOD_PATH)
assert _spec is not None and _spec.loader is not None
clip_qa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(clip_qa)


def test_strip_computes_grid_and_invokes_ffmpeg(tmp_path: Path) -> None:
    clip = tmp_path / "ka01.mp4"
    clip.touch()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    calls: list[list[str]] = []

    def fake_subprocess_run(cmd: list[str], **kwargs: object) -> MagicMock:
        if "ffprobe" in cmd:
            return MagicMock(stdout="8.0\n", returncode=0)
        return MagicMock(returncode=0)

    def fake_run(args: list[str], cwd: Path) -> str:
        calls.append(args)
        return ""

    with (
        patch("subprocess.run", side_effect=fake_subprocess_run),
        patch.object(clip_qa, "run", side_effect=fake_run),
    ):
        result = clip_qa.strip(clip, out_dir, fps=1.0)

    assert result == out_dir / "ka01_1fps.png"
    assert len(calls) == 1
    ffmpeg_cmd = calls[0]
    assert "-i" in ffmpeg_cmd and str(clip) in ffmpeg_cmd
    assert "-vf" in ffmpeg_cmd
    vf_idx = ffmpeg_cmd.index("-vf")
    # 8 frames at 6 cols -> 2 rows: tile=6x2
    assert ffmpeg_cmd[vf_idx + 1] == "fps=1.0,scale=420:-1,tile=6x2"
    assert ffmpeg_cmd[ffmpeg_cmd.index("-frames:v") + 1] == "1"


def test_strip_short_duration_single_row(tmp_path: Path) -> None:
    clip = tmp_path / "short.mp4"
    clip.touch()

    calls: list[list[str]] = []

    def fake_subprocess_run(cmd: list[str], **kwargs: object) -> MagicMock:
        if "ffprobe" in cmd:
            return MagicMock(stdout="3.0\n", returncode=0)
        return MagicMock(returncode=0)

    def fake_run(args: list[str], cwd: Path) -> str:
        calls.append(args)
        return ""

    with (
        patch("subprocess.run", side_effect=fake_subprocess_run),
        patch.object(clip_qa, "run", side_effect=fake_run),
    ):
        result = clip_qa.strip(clip, tmp_path, fps=1.0)

    assert result == tmp_path / "short_1fps.png"
    assert len(calls) == 1
    # 3 frames at 3 cols -> 1 row: tile=3x1
    assert calls[0][calls[0].index("-vf") + 1] == "fps=1.0,scale=420:-1,tile=3x1"


def test_run_failure_raises_runtime_error(tmp_path: Path) -> None:
    with patch("subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=1, stderr="Unknown error")
        with pytest.raises(RuntimeError, match="ffmpeg failed"):
            clip_qa.run(["ffmpeg", "-i", "input.mp4"], tmp_path)
