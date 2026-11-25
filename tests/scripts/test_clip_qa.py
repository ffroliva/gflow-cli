"""Tests for the temporal contact-sheet strip generator in clip_qa.py."""

from __future__ import annotations

import importlib.util
import json
import os
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


needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs system ffmpeg"
)


def ffmpeg(*args: str, cwd: Path) -> None:
    subprocess.run(["ffmpeg", "-v", "error", *args], cwd=cwd, check=True)


@pytest.fixture(scope="module")
def good_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real clip whose motion and loudness share one irregular envelope, so sync can align."""
    folder = tmp_path_factory.mktemp("good")
    gate = "(0.2+0.8*gt(sin(2*PI*{t}*0.9)+sin(2*PI*{t}*1.7),0))"
    noise = f"lum='128+{gate.format(t='T')}*100*(random(1)-0.5)':cb=128:cr=128"
    video = f"color=gray:s=64x96:r=24:d=4,geq={noise}"
    audio = f"aevalsrc='0.5*sin(2*PI*440*t)*{gate.format(t='t')}':s=48000:d=4"
    inputs = ("-f", "lavfi", "-i", video, "-f", "lavfi", "-i", audio)
    ffmpeg(*inputs, "-pix_fmt", "yuv420p", "-movflags", "+faststart", "sa01.mp4", cwd=folder)
    return folder / "sa01.mp4"


def clip_qa_cli(*args: str, cwd: Path, utf8: str = "1") -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONIOENCODING"}
    return subprocess.run(
        [sys.executable, str(_MOD_PATH), *args],
        cwd=cwd,
        env=env | {"PYTHONUTF8": utf8},
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


@needs_ffmpeg
def test_selftest_leaves_the_users_files_alone(good_clip: Path, tmp_path: Path) -> None:
    clip = tmp_path / "my clip.mp4"
    shutil.copy(good_clip, clip)
    mine = [tmp_path / n for n in ("_m.txt", "_a.txt", "_selftest.mp4")]
    for path in mine:
        path.write_text("mine")

    done = clip_qa_cli("--selftest", clip.name, cwd=tmp_path)

    assert done.returncode == 0, done.stdout + done.stderr
    assert sorted(tmp_path.iterdir()) == sorted([clip, *mine])
    assert [path.read_text() for path in mine] == ["mine"] * 3


@needs_ffmpeg
@pytest.mark.parametrize("args", [["sub"], ["--strip", "sub/sa01.mp4"]])
def test_a_relative_path_is_measured(good_clip: Path, tmp_path: Path, args: list[str]) -> None:
    (tmp_path / "sub").mkdir()
    shutil.copy(good_clip, tmp_path / "sub" / "sa01.mp4")

    done = clip_qa_cli(*args, cwd=tmp_path)

    assert done.returncode == 0, done.stderr
    assert (tmp_path / "sub" / "sa01_1fps.png").is_file()


@needs_ffmpeg
def test_a_clip_without_audio_is_named_as_such(good_clip: Path, tmp_path: Path) -> None:
    ffmpeg("-i", str(good_clip), "-an", "-c", "copy", "sa01.mp4", cwd=tmp_path)

    done = clip_qa_cli("sa01.mp4", cwd=tmp_path)

    assert done.returncode != 0
    assert "sa01.mp4: no audio stream" in done.stderr


@needs_ffmpeg
@pytest.mark.parametrize("name", ["Ágil.mp4", "日本.mp4"])
def test_a_non_ascii_name_works_without_utf8_mode(
    good_clip: Path, tmp_path: Path, name: str
) -> None:
    shutil.copy(good_clip, tmp_path / name)

    done = clip_qa_cli(name, cwd=tmp_path, utf8="0")

    assert done.returncode == 0, done.stderr


@needs_ffmpeg
def test_a_stored_audio_offset_counts_as_lag(good_clip: Path, tmp_path: Path) -> None:
    src = str(good_clip)
    late = ("-i", src, "-itsoffset", "0.3", "-i", src, "-map", "0:v", "-map", "1:a")
    ffmpeg(*late, "-c", "copy", "sa01.mp4", cwd=tmp_path)

    in_step = clip_qa.measure(good_clip)[1]
    shifted = clip_qa.measure(tmp_path / "sa01.mp4")[1]

    assert clip_qa.verdict(in_step) == "fluid"
    assert shifted["sync_lag_s"] - in_step["sync_lag_s"] == pytest.approx(0.3, abs=0.03)
    assert clip_qa.verdict(shifted) == "DRIFT"


def _packet_ends(clip: Path) -> list[int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "packet=pos,size", "-of", "json", str(clip)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(int(p["pos"]) + int(p["size"]) for p in json.loads(out)["packets"])


@needs_ffmpeg
@pytest.mark.parametrize("between_packets", [True, False])
def test_a_truncated_clip_is_refused(
    good_clip: Path, tmp_path: Path, between_packets: bool
) -> None:
    ends = _packet_ends(good_clip)
    cut = ends[len(ends) * 3 // 4] - (0 if between_packets else 40)
    (tmp_path / "sa01.mp4").write_bytes(good_clip.read_bytes()[:cut])

    done = clip_qa_cli("sa01.mp4", cwd=tmp_path)

    assert done.returncode != 0, done.stdout
    assert "truncated" in done.stderr
