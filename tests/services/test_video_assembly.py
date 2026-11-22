"""Local video assembly behavior tests using tiny generated media files."""

from __future__ import annotations

import builtins
import errno
import os
import time
from pathlib import Path

import av
import numpy as np
import pytest

from gflow_cli.data.models import AssetKind
from gflow_cli.errors import DataStoreError, VideoAssemblyError
from gflow_cli.services import video_assembly
from gflow_cli.services.video_assembly import assemble_videos, resolve_assembly_input


def _clip(
    path: Path,
    *,
    size: tuple[int, int],
    rate: int,
    frames: int,
    audio: bool,
    audio_frequency: int | None = None,
) -> None:
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
            for start in range(0, frames * 4410, 1024):
                samples = np.arange(start, min(start + 1024, frames * 4410)) / 44100
                tone = (
                    np.zeros(len(samples), dtype=np.float32)
                    if audio_frequency is None
                    else (0.3 * np.sin(2 * np.pi * audio_frequency * samples)).astype(np.float32)
                )
                sample = av.AudioFrame.from_ndarray(
                    np.vstack([tone, tone]), format="fltp", layout="stereo"
                )
                sample.sample_rate = 44100
                sample.pts = start
                for packet in sound.encode(sample):
                    output.mux(packet)
            for packet in sound.encode():
                output.mux(packet)


def _audio(path: Path, *, frequency: float, duration: float = 0.5, level: float = 0.3) -> None:
    rate = 44100
    samples = np.arange(int(rate * duration), dtype=np.float32) / rate
    tone = (level * np.sin(2 * np.pi * frequency * samples)).astype(np.float32)
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


def _quiet_bursts(path: Path) -> None:
    rate = 44100
    samples = np.arange(rate * 4, dtype=np.float32) / rate
    signal = (0.09 * np.sqrt(2) * np.sin(2 * np.pi * 880 * samples)).astype(np.float32)
    signal *= ((samples % 2) < 0.5).astype(np.float32)
    with av.open(str(path), "w", format="mp4") as output:
        stream = output.add_stream("aac", rate=rate)
        stream.layout = "stereo"
        stream.format = "fltp"
        for start in range(0, len(signal), 1024):
            block = np.vstack([signal[start : start + 1024]] * 2)
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


def test_silent_clip_spool_does_not_overwrite_next_clips_audio(tmp_path: Path) -> None:
    silent = tmp_path / "silent.mp4"
    audible = tmp_path / "audible.mp4"
    output = tmp_path / "joined.mp4"
    _clip(silent, size=(64, 48), rate=10, frames=10, audio=False)
    _clip(
        audible,
        size=(64, 48),
        rate=10,
        frames=10,
        audio=True,
        audio_frequency=660,
    )

    assemble_videos([silent, audible], output)

    assert _band_rms(output, 660, 0.1, 0.9) < 0.005
    assert _band_rms(output, 660, 1.1, 1.8) > 0.05


def test_realistic_clips_preserve_duration_frames_and_streams(
    tmp_path: Path, assembly_media: dict[str, Path]
) -> None:
    clips = [assembly_media["clip"], assembly_media["clip"]]
    output = tmp_path / "realistic.mp4"

    result = assemble_videos(clips, output)

    with av.open(str(output)) as container:
        assert len(container.streams.video) == len(container.streams.audio) == 1
        assert len(list(container.decode(video=0))) == 192
    assert result.duration_seconds == pytest.approx(8, abs=0.1)


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


def test_refuses_destination_created_during_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = tmp_path / "clip.mp4"
    output = tmp_path / "joined.mp4"
    _clip(clip, size=(64, 48), rate=10, frames=2, audio=False)
    original_write = video_assembly._write_output

    def create_destination(*args: object, **kwargs: object) -> None:
        original_write(*args, **kwargs)
        output.write_bytes(b"another assembly won")

    monkeypatch.setattr(video_assembly, "_write_output", create_destination)

    with pytest.raises(VideoAssemblyError, match="Output already exists"):
        assemble_videos([clip], output)

    assert output.read_bytes() == b"another assembly won"


def test_fallback_publish_removes_its_reservation_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp_output = tmp_path / ".joined.tmp.mp4"
    temp_output.write_bytes(b"rendered")
    output = tmp_path / "joined.mp4"

    def no_hard_links(*_args: object) -> None:
        raise OSError("hard links unsupported")

    def replace_denied(*_args: object) -> None:
        raise PermissionError("replace denied")

    monkeypatch.setattr(video_assembly.os, "link", no_hard_links)
    monkeypatch.setattr(video_assembly, "_platform_no_replace_rename", no_hard_links)
    monkeypatch.setattr(video_assembly.os, "replace", replace_denied)

    with pytest.raises(PermissionError):
        video_assembly.publish_output(temp_output, output)

    assert not output.exists()
    assert temp_output.read_bytes() == b"rendered"


def test_platform_no_replace_rename_direct(tmp_path: Path) -> None:
    temp_output = tmp_path / ".joined.tmp.mp4"
    temp_output.write_bytes(b"rendered")
    output = tmp_path / "joined.mp4"

    video_assembly._platform_no_replace_rename(temp_output, output)
    assert output.read_bytes() == b"rendered"
    assert not temp_output.exists()

    temp_output2 = tmp_path / ".joined.tmp2.mp4"
    temp_output2.write_bytes(b"rendered 2")
    with pytest.raises(FileExistsError):
        video_assembly._platform_no_replace_rename(temp_output2, output)
    assert output.read_bytes() == b"rendered"
    assert temp_output2.exists()


def test_fallback_publish_preserves_existing_non_empty_output_without_hard_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp_output = tmp_path / ".joined.tmp.mp4"
    temp_output.write_bytes(b"newly rendered")
    output = tmp_path / "joined.mp4"
    output.write_bytes(b"existing completed output")

    def no_hard_links(*_args: object) -> None:
        raise OSError("hard links unsupported")

    monkeypatch.setattr(video_assembly.os, "link", no_hard_links)

    with pytest.raises(VideoAssemblyError, match="Output already exists"):
        video_assembly.publish_output(temp_output, output)

    assert output.read_bytes() == b"existing completed output"
    assert temp_output.read_bytes() == b"newly rendered"


def test_windows_uses_os_rename_and_drops_custom_ctypes() -> None:
    assert not hasattr(video_assembly, "_windows_no_replace_rename")


@pytest.mark.parametrize(
    "error",
    [
        AttributeError("missing symbol"),
        TypeError("invalid ctypes arguments"),
        OSError(errno.ENOSYS, "Function not implemented"),
        OSError(errno.EINVAL, "Invalid argument"),
    ],
)
def test_fallback_publish_falls_back_to_reservation_on_platform_rename_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    temp_output = tmp_path / ".joined.tmp.mp4"
    temp_output.write_bytes(b"newly rendered")
    output = tmp_path / "joined.mp4"

    def no_hard_links(*_args: object) -> None:
        raise OSError("hard links unsupported")

    def broken_platform_rename(*_args: object) -> None:
        raise error

    monkeypatch.setattr(video_assembly.os, "link", no_hard_links)
    monkeypatch.setattr(video_assembly, "_platform_no_replace_rename", broken_platform_rename)

    video_assembly.publish_output(temp_output, output)

    assert output.read_bytes() == b"newly rendered"
    assert not temp_output.exists()


def test_fallback_publish_refuses_fresh_zero_byte_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp_output = tmp_path / ".joined.tmp.mp4"
    temp_output.write_bytes(b"newly rendered")
    output = tmp_path / "joined.mp4"
    output.touch()

    def no_hard_links(*_args: object) -> None:
        raise OSError("hard links unsupported")

    monkeypatch.setattr(video_assembly.os, "link", no_hard_links)

    with pytest.raises(VideoAssemblyError, match="Output already exists"):
        video_assembly.publish_output(temp_output, output)

    assert output.stat().st_size == 0
    assert temp_output.read_bytes() == b"newly rendered"


def test_validate_assembly_refuses_fresh_zero_byte_output(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    output = tmp_path / "joined.mp4"
    clip.touch()
    output.touch()

    with pytest.raises(VideoAssemblyError, match="Output already exists"):
        video_assembly._validate_assembly([clip], output, None, None)


def test_fallback_publish_overcomes_stale_zero_byte_reservation_without_hard_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp_output = tmp_path / ".joined.tmp.mp4"
    temp_output.write_bytes(b"newly rendered")
    output = tmp_path / "joined.mp4"
    output.touch()
    past = time.time() - 120.0
    os.utime(output, (past, past))

    def no_hard_links(*_args: object) -> None:
        raise OSError("hard links unsupported")

    monkeypatch.setattr(video_assembly.os, "link", no_hard_links)

    video_assembly.publish_output(temp_output, output)

    assert output.read_bytes() == b"newly rendered"
    assert not temp_output.exists()


def test_validate_assembly_permits_stale_zero_byte_output(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    output = tmp_path / "joined.mp4"
    clip.touch()
    output.touch()
    past = time.time() - 120.0
    os.utime(output, (past, past))

    video_assembly._validate_assembly([clip], output, None, None)


def test_fallback_publish_completes_atomically_without_hard_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp_output = tmp_path / ".joined.tmp.mp4"
    temp_output.write_bytes(b"newly rendered")
    output = tmp_path / "joined.mp4"

    def no_hard_links(*_args: object) -> None:
        raise OSError("hard links unsupported")

    monkeypatch.setattr(video_assembly.os, "link", no_hard_links)

    video_assembly.publish_output(temp_output, output)

    assert output.read_bytes() == b"newly rendered"
    assert not temp_output.exists()


def test_assemble_videos_retries_over_stale_zero_byte_output(
    tmp_path: Path,
) -> None:
    clip = tmp_path / "clip.mp4"
    output = tmp_path / "joined.mp4"
    _clip(clip, size=(64, 48), rate=10, frames=2, audio=False)
    output.touch()
    past = time.time() - 120.0
    os.utime(output, (past, past))

    result = assemble_videos([clip], output)

    assert output.exists()
    assert result.bytes > 0
    assert output.stat().st_size == result.bytes


def test_keyboard_interrupt_cleans_audio_spools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_mkstemp = video_assembly.tempfile.mkstemp

    def in_temp_dir(*args: object, **kwargs: object) -> tuple[int, str]:
        return original_mkstemp(*args, dir=tmp_path, **kwargs)

    def interrupt(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(video_assembly.tempfile, "mkstemp", in_temp_dir)
    monkeypatch.setattr(video_assembly, "_write_audio_pcm", interrupt)

    with pytest.raises(KeyboardInterrupt):
        video_assembly._make_audio([(tmp_path / "clip.mp4", 100)], None, None, 100, av)

    assert not list(tmp_path.glob("*.f32le"))


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


def _band_rms(path: Path, frequency: int, start: float = 0, end: float = 4) -> float:
    with av.open(str(path)) as container:
        audio = np.concatenate(
            [frame.to_ndarray().astype(np.float32) for frame in container.decode(audio=0)], axis=1
        )
    signal = audio.mean(axis=0)
    segment = signal[int(start * 44100) : int(end * 44100)]
    spectrum = np.fft.rfft(segment)
    bins = np.fft.rfftfreq(len(segment), 1 / 44100)
    selected = np.abs(spectrum[np.argmin(np.abs(bins - frequency))])
    return float(selected * 2 / len(segment) / np.sqrt(2))


def test_clip_only_preserves_gain_and_limiter_caps_peak(
    assembly_media: dict[str, Path], tmp_path: Path
) -> None:
    output = tmp_path / "clip-only.mp4"
    assemble_videos([assembly_media["clip"]], output, clip_volume=1)
    source_rms = _band_rms(assembly_media["clip"], 440)
    output_rms = _band_rms(output, 440)
    with av.open(str(output)) as container:
        peak = max(float(np.abs(frame.to_ndarray()).max()) for frame in container.decode(audio=0))
    assert 20 * np.log10(output_rms / source_rms) == pytest.approx(0, abs=1)
    assert peak <= 1.0


def test_music_gain_without_voiceover(assembly_media: dict[str, Path], tmp_path: Path) -> None:
    output = tmp_path / "music.mp4"
    assemble_videos(
        [assembly_media["clip"]], output, music=assembly_media["music"], music_volume=0.3
    )
    measured = _band_rms(output, 220) / _band_rms(assembly_media["music"], 220)
    assert 20 * np.log10(measured / 0.3) == pytest.approx(0, abs=1.5)


def test_limiter_caps_hot_sum(assembly_media: dict[str, Path], tmp_path: Path) -> None:
    output = tmp_path / "hot-sum.mp4"
    assemble_videos(
        [assembly_media["clip"]],
        output,
        music=assembly_media["music"],
        voiceover=assembly_media["voice"],
        music_volume=1,
        duck=False,
    )
    with av.open(str(output)) as container:
        peak = max(float(np.abs(frame.to_ndarray()).max()) for frame in container.decode(audio=0))
    assert peak <= 1.0


def test_ducking_reduces_music_during_voiceover_bursts(
    assembly_media: dict[str, Path], tmp_path: Path
) -> None:
    output = tmp_path / "ducked.mp4"
    assemble_videos(
        [assembly_media["clip"]],
        output,
        music=assembly_media["music"],
        voiceover=assembly_media["voice"],
        duck=True,
    )
    burst = _band_rms(output, 220, 0.1, 0.4)
    gap = _band_rms(output, 220, 0.6, 0.9)
    assert 20 * np.log10(burst / gap) <= -6


@pytest.mark.parametrize("bed", ["clip", "music"])
def test_ducking_keeps_quiet_voice_above_loud_bed(tmp_path: Path, bed: str) -> None:
    clip = tmp_path / "clip.mp4"
    music = tmp_path / "music.m4a"
    voice = tmp_path / "voice.m4a"
    output = tmp_path / "mix.mp4"
    _clip(clip, size=(64, 48), rate=10, frames=40, audio=bed == "clip", audio_frequency=440)
    _audio(music, frequency=220, duration=4, level=0.6)
    _quiet_bursts(voice)

    assemble_videos([clip], output, music=music if bed == "music" else None, voiceover=voice)

    bed_frequency = 440 if bed == "clip" else 220
    burst_bed = _band_rms(output, bed_frequency, 0.1, 0.4)
    gap_bed = _band_rms(output, bed_frequency, 0.9, 1.2)
    burst_voice = _band_rms(output, 880, 0.1, 0.4)
    reduction_db = 20 * np.log10(burst_bed / gap_bed)
    margin_db = 20 * np.log10(burst_voice / burst_bed)
    assert reduction_db <= -12, f"bed reduction was {reduction_db:.1f} dB"
    assert margin_db >= 6, f"voice-over-bed margin was {margin_db:.1f} dB"


@pytest.mark.parametrize("duck", [True, False])
def test_ducking_reduces_clip_audio_only_when_enabled(
    assembly_media: dict[str, Path], tmp_path: Path, duck: bool
) -> None:
    output = tmp_path / f"clip-duck-{duck}.mp4"
    assemble_videos([assembly_media["clip"]], output, voiceover=assembly_media["voice"], duck=duck)

    burst = _band_rms(output, 440, 0.1, 0.4)
    gap = _band_rms(output, 440, 0.6, 0.9)
    change_db = 20 * np.log10(burst / gap)
    assert change_db <= -6 if duck else abs(change_db) <= 1.5, (
        f"clip audio changed {change_db:.1f} dB"
    )


def test_assembly_does_not_import_numpy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clip = tmp_path / "clip.mp4"
    output = tmp_path / "joined.mp4"
    _clip(clip, size=(64, 48), rate=24, frames=4, audio=True)
    original_import = builtins.__import__

    def without_numpy(name: str, *args: object, **kwargs: object) -> object:
        if name == "numpy" or name.startswith("numpy."):
            raise ImportError("NumPy disabled for this regression test")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_numpy)

    result = assemble_videos([clip], output)

    assert result.output_file == output


def test_rejects_missing_non_uuid_path() -> None:
    with pytest.raises(VideoAssemblyError, match="Input file does not exist"):
        resolve_assembly_input("missing/../missing.mp4")


def test_unexpected_service_exception_is_not_recast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = tmp_path / "clip.mp4"
    _clip(clip, size=(64, 48), rate=10, frames=2, audio=False)

    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError("unexpected implementation failure")

    monkeypatch.setattr(video_assembly, "_inspect_videos", fail)
    with pytest.raises(RuntimeError, match="unexpected implementation failure"):
        assemble_videos([clip], tmp_path / "out.mp4")


def test_missing_pyav_error_is_uncharged_and_names_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_import = builtins.__import__

    def without_av(name: str, *args: object, **kwargs: object) -> object:
        if name == "av":
            raise ImportError("PyAV blocked")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_av)
    with pytest.raises(VideoAssemblyError) as captured:
        assemble_videos([tmp_path / "unused.mp4"], tmp_path / "out.mp4")
    assert captured.value.charged == "no"
    assert "gflow-cli[chain]" in captured.value.detail
