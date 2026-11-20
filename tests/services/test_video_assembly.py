"""Local video assembly behavior tests using tiny generated media files."""

from __future__ import annotations

from pathlib import Path

import av
import numpy as np
import pytest

from gflow_cli.data.models import AssetKind
from gflow_cli.errors import DataStoreError, VideoAssemblyError
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


def test_assembles_varied_clips_and_missing_audio_as_silence(tmp_path: Path) -> None:
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    output = tmp_path / "joined.mp4"
    _clip(first, size=(64, 48), rate=10, frames=10, audio=True)
    _clip(second, size=(96, 64), rate=15, frames=15, audio=False)

    result = assemble_videos([first, second], output)

    with av.open(str(output)) as container:
        stream = container.streams.video[0]
        assert (stream.width, stream.height) == (64, 48)
        assert stream.average_rate == 10
        assert len(list(container.decode(stream))) == 20
        assert container.streams.audio
    assert result.clips_count == 2
    assert result.duration_seconds == pytest.approx(2, abs=0.2)


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


def test_rejects_missing_non_uuid_path() -> None:
    with pytest.raises(VideoAssemblyError, match="Input file does not exist"):
        resolve_assembly_input("missing/../missing.mp4")
