"""Tests for the temporal contact-sheet strip generator in clip_qa.py."""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
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


@pytest.fixture
def clip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A relative clip path, and an ffmpeg process that writes the file it is told to."""

    def fake_ffmpeg(args: list[str], *, cwd: Path, **_: object) -> MagicMock:
        assert (Path(cwd) / args[args.index("-i") + 1]).is_file()
        assert Path(cwd).resolve() != tmp_path.resolve(), "scratch beside the clip"
        if args[-1] != "-":
            (Path(cwd) / args[-1]).write_text("shifted")
            return MagicMock(returncode=0, stderr="")
        name = re.search(r"file=(.+)$", args[args.index("-f") - 1])
        assert name is not None
        assert re.fullmatch(r"[\w.]+", name[1])  # a drive colon would end the filter option
        (Path(cwd) / name[1]).write_text(
            "lavfi.signalstats.YAVG=1.5\nlavfi.signalstats.YAVG=2.5\n"
            "lavfi.astats.Overall.RMS_level=-20\nlavfi.astats.Overall.RMS_level=0\n"
        )
        return MagicMock(returncode=0, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_ffmpeg)
    monkeypatch.chdir(tmp_path)
    path = Path("clip with space.mp4")
    path.touch()
    return path


def test_yavg_leaves_the_users_own_file_alone(clip: Path) -> None:
    mine = clip.parent / "_m.txt"
    mine.write_text("mine")

    assert clip_qa.yavg(clip, "scale=180:320") == [1.5, 2.5]

    assert mine.is_file(), "the user's own _m.txt was deleted"
    assert mine.read_text() == "mine"


def test_audio_envelope_leaves_the_users_own_file_alone(clip: Path) -> None:
    mine = clip.parent / "_a.txt"
    mine.write_text("mine")

    assert clip_qa.audio_envelope(clip, 24.0) == pytest.approx([0.1, 1.0])

    assert mine.is_file(), "the user's own _a.txt was deleted"
    assert mine.read_text() == "mine"


def test_selftest_leaves_the_users_own_clip_alone(
    clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine = clip.parent / "_selftest.mp4"
    mine.write_text("mine")

    def measured(path: Path) -> tuple[dict[str, float], dict[str, float]]:
        lag = 0.0 if path == clip else 0.2
        values = {"sync_lag_s": lag, "sync_r": 0.9, "speech_onset_s": 0.0}
        return {}, values | {"face_motion_p10": 1.0, "av_duration_delta_s": 0.0}

    monkeypatch.setattr(clip_qa, "measure", measured)

    clip_qa.selftest(clip)

    assert mine.read_text() == "mine"
    assert sorted(Path().iterdir()) == sorted([clip, mine])


@pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs system ffmpeg"
)
def test_selftest_leaves_the_users_files_alone(tmp_path: Path) -> None:
    # Motion and loudness share one irregular envelope, so the sync detector can align them.
    gate = "(0.2+0.8*gt(sin(2*PI*{t}*0.9)+sin(2*PI*{t}*1.7),0))"
    noise = f"lum='128+{gate.format(t='T')}*100*(random(1)-0.5)':cb=128:cr=128"
    video = f"color=gray:s=64x96:r=24:d=4,geq={noise}"
    audio = f"aevalsrc='0.5*sin(2*PI*440*t)*{gate.format(t='t')}':s=48000:d=4"
    clip = tmp_path / "my clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", video, "-f", "lavfi", "-i", audio]
        + ["-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )
    mine = [tmp_path / n for n in ("_m.txt", "_a.txt", "_selftest.mp4")]
    for path in mine:
        path.write_text("mine")

    done = subprocess.run(
        [sys.executable, str(_MOD_PATH), "--selftest", clip.name],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert done.returncode == 0, done.stdout + done.stderr
    assert sorted(tmp_path.iterdir()) == sorted([clip, *mine])
    assert [path.read_text() for path in mine] == ["mine"] * 3
