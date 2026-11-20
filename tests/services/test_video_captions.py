"""Behavioral contract for local video transcription and captioning."""

from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path

import pytest

from gflow_cli.errors import ConfigurationError, FrameExtractionError
from gflow_cli.services.video_captions import (
    CaptionEntry,
    caption_video,
    format_srt,
    parse_srt,
    transcribe_video,
)


@pytest.fixture
def media_files(tmp_path: Path) -> tuple[Path, Path]:
    av = pytest.importorskip("av")
    audio_video = tmp_path / "clip.mp4"
    silent_video = tmp_path / "silent.mp4"
    for path, with_audio in ((audio_video, True), (silent_video, False)):
        with av.open(str(path), mode="w") as output:
            video = output.add_stream("libx264", rate=10)
            video.width, video.height, video.pix_fmt = 160, 90, "yuv420p"
            audio = output.add_stream("aac", rate=16000) if with_audio else None
            if audio:
                audio.layout = "mono"
            for index in range(20):
                frame = av.VideoFrame(160, 90, "rgb24")
                frame.pts = index
                frame.time_base = Fraction(1, 10)
                for packet in video.encode(frame):
                    output.mux(packet)
            for packet in video.encode():
                output.mux(packet)
            if audio:
                audio_frame = av.AudioFrame(format="fltp", layout="mono", samples=1600)
                audio_frame.sample_rate = 16000
                audio_frame.pts = 0
                for packet in audio.encode(audio_frame):
                    output.mux(packet)
                for packet in audio.encode():
                    output.mux(packet)
    return audio_video, silent_video


def test_parse_and_format_srt_roundtrip() -> None:
    entries = parse_srt("\ufeff1\r\n00:00:00,500 --> 00:00:01.200\r\nHello\r\nworld\r\n")
    assert entries == [CaptionEntry(1, 0.5, 1.2, "Hello\nworld")]
    assert parse_srt(format_srt(entries)) == entries


@pytest.mark.parametrize("content", ["", "PRIVATE SENTINEL", "1\n00:00:02,000 --> 00:00:01,000\nx"])
def test_parse_srt_rejects_invalid_input_without_echoing(content: str) -> None:
    with pytest.raises(ConfigurationError) as caught:
        parse_srt(content, source_name="private.srt")
    assert caught.value.charged == "no"
    assert caught.value.resolved_next_action() == "fix_input"
    assert "PRIVATE SENTINEL" not in caught.value.detail


def test_missing_chain_extra_fails_before_transcription(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "av", None)
    with pytest.raises(FrameExtractionError) as caught:
        transcribe_video("clip.mp4")
    assert caught.value.charged == "no"
    assert caught.value.resolved_next_action() == "fix_input"
    assert "gflow-cli[chain]" in caught.value.detail


def test_transcribe_refuses_default_google_endpoint(media_files: tuple[Path, Path]) -> None:
    with pytest.raises(ConfigurationError, match="GFLOW_CLI_LLM_BASE_URL"):
        transcribe_video(str(media_files[0]))


def test_transcribe_refuses_video_without_audio(media_files: tuple[Path, Path]) -> None:
    with pytest.raises(ConfigurationError, match="audio"):
        transcribe_video(str(media_files[1]))


def test_caption_video_burns_frames_and_keeps_audio(
    media_files: tuple[Path, Path], tmp_path: Path
) -> None:
    av = pytest.importorskip("av")
    source, _ = media_files
    srt = tmp_path / "sample.srt"
    srt.write_text("1\n00:00:00,200 --> 00:00:01,000\nA caption\n", encoding="utf-8")
    result = caption_video(str(source), srt_path=srt)
    assert result.frames_burned > 0
    with av.open(str(result.output_file)) as output:
        assert output.streams.audio
