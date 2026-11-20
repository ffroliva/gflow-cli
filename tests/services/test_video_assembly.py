"""Local video assembly behavior tests using tiny generated media files."""

from __future__ import annotations

from pathlib import Path

import av
import numpy as np
import pytest

from gflow_cli.data.models import AssetKind
from gflow_cli.errors import DataStoreError, VideoAssemblyError
from gflow_cli.services import video_assembly
from gflow_cli.services.video_assembly import assemble_videos, resolve_assembly_input


def _clip(path: Path, *, size: tuple[int, int], rate: int, frames: int, audio: bool) -> None:
    with av.open(str(path), "w", format="mp4") as output:
        video = output.add_stream("mpeg4", rate=rate)
        video.width, video.height = size
        video.pix_fmt = "yuv420p"
        sound = output.add_stream("aac", rate=44100) if audio else None
        if sound is not None:
            sound.layout = "stereo"
            sound.format = "fltp"
        for index in range(frames):
            pixels = np.zeros((size[1], size[0], 3), dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts = index
            for packet in video.encode(frame):
                output.mux(packet)
        for packet in video.encode():
            output.mux(packet)
        if sound is not None:
            sample = av.AudioFrame.from_ndarray(
                np.zeros((2, 1024), dtype=np.float32), format="fltp", layout="stereo"
            )
            sample.sample_rate = 44100
            sample.pts = 0
            for packet in sound.encode(sample):
                output.mux(packet)
            for packet in sound.encode():
                output.mux(packet)


def _audio(path: Path, *, frequency: float) -> None:
    rate = 44100
    samples = np.arange(rate // 2, dtype=np.float32) / rate
    tone = (0.3 * np.sin(2 * np.pi * frequency * samples)).astype(np.float32)
    with av.open(str(path), "w", format="mp4") as output:
        stream = output.add_stream("aac", rate=rate)
        stream.layout = "stereo"
        stream.format = "fltp"
        for start in range(0, len(tone), 1024):
            block = np.vstack([tone[start : start + 1024], tone[start : start + 1024]])
            frame = av.AudioFrame.from_ndarray(block, format="fltp", layout="stereo")
            frame.sample_rate = rate
            frame.pts = start
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


def test_assembles_varied_clips_and_missing_audio_as_silence(tmp_path: Path) -> None:
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    output = tmp_path / "joined.mp4"
    _clip(first, size=(64, 48), rate=10, frames=10, audio=True)
    _clip(second, size=(96, 72), rate=15, frames=15, audio=False)

    result = assemble_videos([first, second], output)

    with av.open(str(output)) as container:
        stream = container.streams.video[0]
        assert (stream.width, stream.height) == (64, 48)
        assert stream.average_rate == 10
        assert len(list(container.decode(stream))) == 20
        assert container.streams.audio
    assert result.clips_count == 2
    assert result.duration_seconds == pytest.approx(2, abs=0.2)


def test_assembles_more_than_one_second_without_timestamp_regression(tmp_path: Path) -> None:
    clip = tmp_path / "long.mp4"
    output = tmp_path / "long-joined.mp4"
    _clip(clip, size=(64, 48), rate=24, frames=96, audio=True)

    result = assemble_videos([clip], output)

    assert result.duration_seconds == pytest.approx(4, abs=0.1)
    with av.open(str(output)) as container:
        assert len(list(container.decode(video=0))) == 96


def test_refuses_mismatched_aspect_ratio_before_writing_output(tmp_path: Path) -> None:
    landscape = tmp_path / "landscape.mp4"
    portrait = tmp_path / "portrait.mp4"
    output = tmp_path / "joined.mp4"
    _clip(landscape, size=(64, 48), rate=10, frames=2, audio=True)
    _clip(portrait, size=(48, 64), rate=10, frames=2, audio=True)

    with pytest.raises(VideoAssemblyError, match="portrait.mp4 aspect ratio.*first clip ratio"):
        assemble_videos([landscape, portrait], output)

    assert not output.exists()


def test_rounds_odd_output_dimensions_down_to_even(tmp_path: Path) -> None:
    clip = tmp_path / "odd.mp4"
    output = tmp_path / "even.mp4"
    with av.open(str(clip), "w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=10)
        stream.width, stream.height, stream.pix_fmt = 65, 49, "yuv444p"
        for index in range(2):
            frame = av.VideoFrame(65, 49, "yuv444p")
            frame.pts = index
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)

    result = assemble_videos([clip], output)

    assert result.width == 64
    assert result.height == 48


def test_rejects_output_alias_and_corrupt_input_without_partial_output(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    _clip(clip, size=(64, 48), rate=10, frames=2, audio=False)
    with pytest.raises(VideoAssemblyError):
        assemble_videos([clip], clip)

    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(b"not a video")
    output = tmp_path / "partial.mp4"
    with pytest.raises(VideoAssemblyError):
        assemble_videos([corrupt], output)
    assert not output.exists()
    assert clip.exists()


def test_keyboard_interrupt_removes_partial_output_and_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = tmp_path / "clip.mp4"
    output = tmp_path / "joined.mp4"
    _clip(clip, size=(64, 48), rate=10, frames=2, audio=True)

    def interrupt(*args: object, **kwargs: object) -> None:
        Path(args[1]).write_bytes(b"partial")
        raise KeyboardInterrupt

    monkeypatch.setattr(video_assembly, "_write_output", interrupt)
    with pytest.raises(KeyboardInterrupt):
        assemble_videos([clip], output)

    assert not output.exists()
    assert not list(tmp_path.glob(".joined.*.mp4"))


def test_rejects_music_file_without_audio_stream(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    music = tmp_path / "silent-video.mp4"
    _clip(clip, size=(64, 48), rate=10, frames=2, audio=True)
    _clip(music, size=(64, 48), rate=10, frames=2, audio=False)

    with pytest.raises(VideoAssemblyError, match="Audio input has no audio stream"):
        assemble_videos([clip], tmp_path / "joined.mp4", music=music)


def test_resolves_catalog_uuid_only_through_verified_video_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "catalog clip.mp4"
    local.touch()
    asset = type("Asset", (), {"kind": AssetKind.VIDEO, "local_files": [object()]})()
    monkeypatch.setattr("gflow_cli.services.video_assembly.resolve_asset", lambda *a, **k: asset)
    monkeypatch.setattr("gflow_cli.services.video_assembly.verified_local_path", lambda _: local)

    assert (
        resolve_assembly_input("123e4567-e89b-12d3-a456-426614174000", profile="default") == local
    )


def test_rejects_catalog_uuid_without_verified_local_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = type("Asset", (), {"kind": AssetKind.VIDEO, "local_files": [object()]})()
    monkeypatch.setattr("gflow_cli.services.video_assembly.resolve_asset", lambda *a, **k: asset)
    monkeypatch.setattr("gflow_cli.services.video_assembly.verified_local_path", lambda _: None)

    with pytest.raises(DataStoreError, match="no verified local file"):
        resolve_assembly_input("123e4567-e89b-12d3-a456-426614174000")


def test_mixes_music_and_voiceover_with_ducking(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    music = tmp_path / "music.m4a"
    voiceover = tmp_path / "voiceover.m4a"
    output = tmp_path / "mix.mp4"
    _clip(clip, size=(64, 48), rate=10, frames=5, audio=False)
    _audio(music, frequency=220)
    _audio(voiceover, frequency=1000)

    result = assemble_videos([clip], output, music=music, voiceover=voiceover)

    with av.open(str(output)) as container:
        audio = container.streams.audio[0]
        decoded = list(container.decode(audio))
    assert result.has_music and result.has_voiceover and result.ducking_applied
    assert decoded
    assert max(float(np.abs(frame.to_ndarray()).max()) for frame in decoded) > 0.1


def test_rejects_missing_non_uuid_path() -> None:
    with pytest.raises(VideoAssemblyError, match="Input file does not exist"):
        resolve_assembly_input("missing/../missing.mp4")
