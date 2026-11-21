"""Behavioral contract for local video transcription and captioning."""

from __future__ import annotations

import io
import sys
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gflow_cli import config
from gflow_cli.data.models import AssetKind
from gflow_cli.errors import (
    ConfigurationError,
    DataStoreError,
    FrameExtractionError,
    NetworkError,
    RateLimitError,
    VideoProcessingError,
)
from gflow_cli.services import video_captions as service
from gflow_cli.services.speech_ports import Transcript, TranscriptionRequest, TranscriptSegment
from gflow_cli.services.video_captions import (
    CaptionEntry,
    TranscribeResult,
    caption_video,
    extract_wav_bytes,
    format_srt,
    parse_srt,
    resolve_video_source,
    transcribe_video,
)


@pytest.fixture
def media_files(tmp_path: Path) -> tuple[Path, Path]:
    av = pytest.importorskip("av")
    audio_video = tmp_path / "clip.mkv"
    silent_video = tmp_path / "silent.mkv"
    for path, with_audio in ((audio_video, True), (silent_video, False)):
        # PCM in Matroska avoids AAC encoder flush failures under Windows xdist.
        with av.open(str(path), mode="w", format="matroska") as output:
            video = output.add_stream("libx264", rate=10)
            video.width, video.height, video.pix_fmt = 160, 90, "yuv420p"
            audio = output.add_stream("pcm_s16le", rate=16000) if with_audio else None
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
                audio_frame = av.AudioFrame(format="s16", layout="mono", samples=1600)
                audio_frame.sample_rate = 16000
                audio_frame.pts = 0
                for packet in audio.encode(audio_frame):
                    output.mux(packet)
    return audio_video, silent_video


@pytest.fixture(scope="session")
def realistic_media(tmp_path_factory: pytest.TempPathFactory) -> Path:
    av = pytest.importorskip("av")
    path = tmp_path_factory.mktemp("realistic-video") / "clip.mp4"
    with av.open(str(path), mode="w") as output:
        video = output.add_stream("libx264", rate=24)
        video.width, video.height, video.pix_fmt = 1280, 720, "yuv420p"
        audio = output.add_stream("aac", rate=48000)
        audio.layout = "stereo"
        for index in range(96):
            frame = av.VideoFrame(1280, 720, "yuv420p")
            frame.pts = index
            frame.time_base = Fraction(1, 24)
            for packet in video.encode(frame):
                output.mux(packet)
        for index in range(188):
            frame = av.AudioFrame(format="fltp", layout="stereo", samples=1024)
            frame.sample_rate = 48000
            frame.pts = index * 1024
            frame.time_base = Fraction(1, 48000)
            for plane in frame.planes:
                plane.update(bytes(plane.buffer_size))
            for packet in audio.encode(frame):
                output.mux(packet)
        for packet in video.encode():
            output.mux(packet)
        for packet in audio.encode():
            output.mux(packet)
    return path


def test_parse_and_format_srt_roundtrip() -> None:
    entries = parse_srt("\ufeff1\r\n00:00:00,500 --> 00:00:01.200\r\nHello\r\nworld\r\n")
    assert entries == [CaptionEntry(1, 0.5, 1.2, "Hello\nworld")]
    assert parse_srt(format_srt(entries)) == entries


def test_usage_documents_caption_commands_once() -> None:
    usage = Path("docs/USAGE.md").read_text(encoding="utf-8")
    assert usage.count("## `gflow video transcribe` and `gflow video captions`") == 1


def test_parse_srt_skips_empty_cue_without_merging_next() -> None:
    content = "1\n00:00:00,000 --> 00:00:01,000\n\n2\n00:00:01,000 --> 00:00:02,000\nkept"
    assert [entry.text for entry in parse_srt(content)] == ["kept"]


@pytest.mark.parametrize("content", ["", "PRIVATE SENTINEL", "1\n00:00:02,000 --> 00:00:01,000\nx"])
def test_parse_srt_rejects_invalid_input_without_echoing(content: str) -> None:
    with pytest.raises(ConfigurationError) as caught:
        parse_srt(content, source_name="private.srt")
    assert caught.value.charged == "no"
    assert caught.value.resolved_next_action() == "fix_input"
    assert "SRT" in caught.value.remediation_hint or "timestamp" in caught.value.remediation_hint
    assert "PRIVATE SENTINEL" not in caught.value.detail


def test_missing_chain_extra_fails_before_transcription(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "av", None)
    with pytest.raises(FrameExtractionError) as caught:
        transcribe_video("clip.mp4")
    assert caught.value.charged == "no"
    assert caught.value.resolved_next_action() == "fix_input"
    assert "gflow-cli[chain]" in caught.value.detail
    with pytest.raises(FrameExtractionError):
        caption_video("clip.mp4", srt_path=Path("captions.srt"))


def test_transcribe_refuses_default_google_endpoint(media_files: tuple[Path, Path]) -> None:
    with pytest.raises(ConfigurationError, match="GFLOW_CLI_LLM_BASE_URL"):
        transcribe_video(str(media_files[0]))


def test_transcribe_refuses_existing_srt_before_network(
    media_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setenv("GFLOW_CLI_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    config.reset_settings()
    existing = media_files[0].with_suffix(".srt")
    existing.write_text("keep me", encoding="utf-8")
    monkeypatch.setattr(
        "gflow_cli.services.transcribers.openai_compatible.httpx.Client",
        lambda *_a, **_k: pytest.fail("network called"),
    )
    try:
        with pytest.raises(ConfigurationError, match="already exists") as caught:
            transcribe_video(str(media_files[0]))
    finally:
        config.reset_settings()
    assert caught.value.charged == "no"
    assert "--srt" in caught.value.remediation_hint
    assert existing.read_text(encoding="utf-8") == "keep me"


def test_transcribe_no_clobber_when_srt_created_during_transcription(
    media_files: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _ = media_files
    destination = tmp_path / "race.srt"
    other_temp = tmp_path / ".race.srt.other"
    other_temp.write_text("other run temp", encoding="utf-8")

    class RacingTranscriber:
        def transcribe(self, _request: TranscriptionRequest) -> Transcript:
            destination.write_text("winner", encoding="utf-8")
            return Transcript((TranscriptSegment(0.0, 0.5, "late"),))

    with pytest.raises(ConfigurationError, match="already exists"):
        transcribe_video(str(source), out_file=destination, transcriber=RacingTranscriber())
    assert destination.read_text(encoding="utf-8") == "winner"
    assert other_temp.read_text(encoding="utf-8") == "other run temp"
    assert [p for p in tmp_path.glob(".race.srt.*") if p != other_temp] == []

    # Fallback path when hard links are unsupported on the target filesystem
    destination.unlink()

    def no_hardlink(_src: object, _dst: object) -> None:
        raise OSError("hard links unsupported")

    monkeypatch.setattr("gflow_cli.services.video_captions.os.link", no_hardlink)
    with pytest.raises(ConfigurationError, match="already exists"):
        transcribe_video(str(source), out_file=destination, transcriber=RacingTranscriber())
    assert destination.read_text(encoding="utf-8") == "winner"
    assert [p for p in tmp_path.glob(".race.srt.*") if p != other_temp] == []


def test_atomic_write_fallback_never_exposes_empty_final_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    destination = tmp_path / "published.srt"
    observed_at_publish: list[tuple[bool, int | None]] = []
    real_rename = os.rename
    real_replace = os.replace

    def no_hardlink(_src: object, _dst: object) -> None:
        raise OSError("hard links unsupported")

    def spy_publish(
        fn: Any,
    ) -> Any:
        def wrapper(src: Path, dst: Path) -> None:
            dst_path = Path(dst)
            exists = dst_path.exists()
            observed_at_publish.append((exists, dst_path.stat().st_size if exists else None))
            fn(src, dst)

        return wrapper

    monkeypatch.setattr("gflow_cli.services.video_captions.os.link", no_hardlink)
    monkeypatch.setattr("gflow_cli.services.video_captions.os.rename", spy_publish(real_rename))
    monkeypatch.setattr("gflow_cli.services.video_captions.os.replace", spy_publish(real_replace))

    service._atomic_write(destination, b"1\n00:00:00,000 --> 00:00:01,000\nhello\n")
    assert observed_at_publish == [(False, None)]
    assert destination.read_bytes().startswith(b"1\n")


def test_atomic_write_cleans_own_temp_file_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tempfile

    destination = tmp_path / "failed.srt"
    sibling_temp = tmp_path / ".failed.srt.other"
    sibling_temp.write_text("keep sibling", encoding="utf-8")
    real_ntf = tempfile.NamedTemporaryFile

    def failing_ntf(*args: Any, **kwargs: Any) -> Any:
        handle = real_ntf(*args, **kwargs)

        class BrokenHandle:
            name = handle.name

            def __enter__(self) -> BrokenHandle:
                return self

            def __exit__(self, *exc: object) -> None:
                handle.close()

            def write(self, _data: bytes) -> int:
                raise OSError("disk full")

        return BrokenHandle()

    monkeypatch.setattr(
        "gflow_cli.services.video_captions.tempfile.NamedTemporaryFile", failing_ntf
    )
    with pytest.raises(ConfigurationError, match="Could not write SubRip file"):
        service._atomic_write(destination, b"content")
    assert not destination.exists()
    assert sibling_temp.read_text(encoding="utf-8") == "keep sibling"
    assert [p for p in tmp_path.glob(".failed.srt.*") if p != sibling_temp] == []


def test_captions_without_srt_refuses_existing_default_srt_before_network(
    media_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:

    source, _ = media_files
    srt = source.with_suffix(".srt")
    srt.write_text("preserve", encoding="utf-8")
    monkeypatch.setenv("GFLOW_CLI_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    config.reset_settings()
    monkeypatch.setattr(
        "gflow_cli.services.transcribers.openai_compatible.httpx.Client",
        lambda *_a, **_k: pytest.fail("network called"),
    )
    try:
        with pytest.raises(ConfigurationError, match="already exists") as caught:
            caption_video(str(source))
    finally:
        config.reset_settings()
    assert caught.value.charged == "no"
    assert srt.read_text(encoding="utf-8") == "preserve"


def test_outputs_and_parents_validated_before_transcriber(
    media_files: tuple[Path, Path], tmp_path: Path
) -> None:
    source, _ = media_files
    regular_file = tmp_path / "not_a_dir"
    regular_file.write_text("file", encoding="utf-8")

    class ForbiddenTranscriber:
        def transcribe(self, _request: TranscriptionRequest) -> Transcript:
            pytest.fail("transcriber should not be called when output paths are invalid")

    # 1. Parent is a regular file (transcribe_video and caption_video)
    with pytest.raises(ConfigurationError) as exc_transcribe_parent:
        transcribe_video(
            str(source),
            out_file=regular_file / "out.srt",
            transcriber=ForbiddenTranscriber(),
        )
    assert exc_transcribe_parent.value.charged == "no"

    with pytest.raises(ConfigurationError) as exc_caption_parent:
        caption_video(
            str(source),
            out_file=regular_file / "out.mp4",
            transcriber=ForbiddenTranscriber(),
        )
    assert exc_caption_parent.value.charged == "no"

    # 2. --out-file equal to the would-be generated SRT path when --srt is absent
    with pytest.raises(ConfigurationError, match="must not overwrite") as exc_collide:
        caption_video(
            str(source),
            out_file=source.with_suffix(".srt"),
            transcriber=ForbiddenTranscriber(),
        )
    assert exc_collide.value.charged == "no"


def test_default_output_directories_rejected_before_transcription(
    media_files: tuple[Path, Path],
) -> None:
    source, _ = media_files
    default_mp4 = source.with_name(f"{source.stem}-captions.mp4")
    default_srt = source.with_suffix(".srt")

    class ForbiddenTranscriber:
        def transcribe(self, _request: TranscriptionRequest) -> Transcript:
            pytest.fail("transcriber should not be called when default output is a directory")

    default_mp4.mkdir()
    try:
        with pytest.raises(ConfigurationError, match="not a regular file") as exc_mp4:
            caption_video(str(source), transcriber=ForbiddenTranscriber())
        assert exc_mp4.value.charged == "no"
    finally:
        default_mp4.rmdir()

    default_srt.mkdir()
    try:
        with pytest.raises(ConfigurationError, match="not a regular file") as exc_srt_caption:
            caption_video(str(source), transcriber=ForbiddenTranscriber())
        assert exc_srt_caption.value.charged == "no"
        with pytest.raises(ConfigurationError, match="not a regular file") as exc_srt_transcribe:
            transcribe_video(str(source), transcriber=ForbiddenTranscriber())
        assert exc_srt_transcribe.value.charged == "no"
    finally:
        default_srt.rmdir()


def test_transcribe_refuses_video_without_audio(media_files: tuple[Path, Path]) -> None:
    class UnreachableTranscriber:
        def transcribe(self, _request: TranscriptionRequest) -> Transcript:
            pytest.fail("transcriber should not be called on a clip without audio")

    with pytest.raises(ConfigurationError, match="Video has no audio track to transcribe"):
        transcribe_video(str(media_files[1]), transcriber=UnreachableTranscriber())


def test_audio_decode_does_not_require_numpy(
    media_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    av = pytest.importorskip("av")
    monkeypatch.setattr(av.AudioFrame, "to_ndarray", lambda *_: pytest.fail("NumPy path used"))
    wav, _ = extract_wav_bytes(media_files[0])
    assert wav.startswith(b"RIFF")


def test_extract_wav_bytes_preserves_leading_audio_offset_and_gaps(
    media_files: tuple[Path, Path], tmp_path: Path
) -> None:
    import wave

    av = pytest.importorskip("av")
    delayed = tmp_path / "delayed.mkv"
    with av.open(str(delayed), mode="w", format="matroska") as output:
        video = output.add_stream("libx264", rate=10)
        video.width, video.height, video.pix_fmt = 160, 90, "yuv420p"
        audio = output.add_stream("pcm_s16le", rate=16000)
        audio.layout = "mono"
        for index in range(20):
            vframe = av.VideoFrame(160, 90, "rgb24")
            vframe.pts = index
            vframe.time_base = Fraction(1, 10)
            for packet in video.encode(vframe):
                output.mux(packet)
        for packet in video.encode():
            output.mux(packet)
        for start_sample in (16000, 28000):
            aframe = av.AudioFrame(format="s16", layout="mono", samples=4000)
            aframe.sample_rate = 16000
            aframe.pts = start_sample
            aframe.time_base = Fraction(1, 16000)
            aframe.planes[0].update(b"\x10\x20" * 4000)
            for packet in audio.encode(aframe):
                output.mux(packet)
        for packet in audio.encode():
            output.mux(packet)

    wav, _ = extract_wav_bytes(delayed)
    with wave.open(io.BytesIO(wav)) as decoded:
        assert decoded.getnframes() == 32000
        pcm = decoded.readframes(decoded.getnframes())
    assert pcm[: 16000 * 2] == bytes(16000 * 2)
    assert any(pcm[16000 * 2 : 20000 * 2])
    assert pcm[20000 * 2 : 28000 * 2] == bytes(8000 * 2)
    assert any(pcm[28000 * 2 : 32000 * 2])

    zero_wav, _ = extract_wav_bytes(media_files[0])
    with wave.open(io.BytesIO(zero_wav)) as zero_decoded:
        assert zero_decoded.getnframes() == 1600


def test_non_zero_start_time_aligns_wav_tone_and_caption_overlay(tmp_path: Path) -> None:
    import wave

    av = pytest.importorskip("av")
    shifted = tmp_path / "shifted.mkv"
    with av.open(str(shifted), mode="w", format="matroska") as output:
        video = output.add_stream("libx264", rate=10)
        video.width, video.height, video.pix_fmt = 160, 90, "yuv420p"
        audio = output.add_stream("pcm_s16le", rate=16000)
        audio.layout = "mono"
        for index in range(20):
            vframe = av.VideoFrame(160, 90, "rgb24")
            vframe.planes[0].update(bytes([120]) * vframe.planes[0].buffer_size)
            vframe.pts = 50 + index
            vframe.time_base = Fraction(1, 10)
            for packet in video.encode(vframe):
                output.mux(packet)
        for packet in video.encode():
            output.mux(packet)
        for start_sample, payload in ((80000, bytes(16000 * 2)), (96000, b"\x10\x20" * 8000)):
            aframe = av.AudioFrame(format="s16", layout="mono", samples=len(payload) // 2)
            aframe.sample_rate = 16000
            aframe.pts = start_sample
            aframe.time_base = Fraction(1, 16000)
            aframe.planes[0].update(payload)
            for packet in audio.encode(aframe):
                output.mux(packet)
        for packet in audio.encode():
            output.mux(packet)

    wav, _ = extract_wav_bytes(shifted)
    with wave.open(io.BytesIO(wav)) as decoded:
        pcm = decoded.readframes(decoded.getnframes())
    assert pcm[: 16000 * 2] == bytes(16000 * 2)
    assert any(pcm[16000 * 2 : 24000 * 2])

    srt = tmp_path / "shifted.srt"
    srt.write_text("1\n00:00:00,950 --> 00:00:01,050\nShifted cue\n", encoding="utf-8")
    rendered = caption_video(str(shifted), srt_path=srt)
    assert rendered.frames_burned == 1
    with av.open(str(rendered.output_file)) as container:
        decoded_frames = [frame.to_image() for frame in container.decode(video=0)]
    assert decoded_frames[10].tobytes() != decoded_frames[0].tobytes()


def test_extract_wav_bytes_small_gaps_do_not_accumulate_drift(tmp_path: Path) -> None:
    import wave

    av = pytest.importorskip("av")
    gapped = tmp_path / "gapped.mkv"
    with av.open(str(gapped), mode="w", format="matroska") as output:
        video = output.add_stream("libx264", rate=10)
        video.width, video.height, video.pix_fmt = 160, 90, "yuv420p"
        audio = output.add_stream("pcm_s16le", rate=16000)
        audio.layout = "mono"
        for index in range(100):
            vframe = av.VideoFrame(160, 90, "rgb24")
            vframe.pts = index
            vframe.time_base = Fraction(1, 10)
            for packet in video.encode(vframe):
                output.mux(packet)
        for packet in video.encode():
            output.mux(packet)
        for index in range(400):
            aframe = av.AudioFrame(format="s16", layout="mono", samples=320)
            aframe.sample_rate = 16000
            aframe.pts = index * 400
            aframe.time_base = Fraction(1, 16000)
            payload = b"\x40\x20" * 320 if index == 360 else bytes(320 * 2)
            aframe.planes[0].update(payload)
            for packet in audio.encode(aframe):
                output.mux(packet)
        for packet in audio.encode():
            output.mux(packet)

    wav, _ = extract_wav_bytes(gapped)
    with wave.open(io.BytesIO(wav)) as decoded:
        pcm = decoded.readframes(decoded.getnframes())
    first_nonzero_byte = next(i for i, b in enumerate(pcm) if b != 0)
    tone_seconds = (first_nonzero_byte // 2) / 16000
    assert tone_seconds == pytest.approx(9.0, abs=0.03)


def test_extract_wav_bytes_flushes_buffered_resampler_on_very_short_clip(tmp_path: Path) -> None:
    import wave

    av = pytest.importorskip("av")
    short = tmp_path / "short.mkv"
    with av.open(str(short), mode="w", format="matroska") as output:
        video = output.add_stream("libx264", rate=10)
        video.width, video.height, video.pix_fmt = 160, 90, "yuv420p"
        audio = output.add_stream("pcm_s16le", rate=48000)
        audio.layout = "mono"
        vframe = av.VideoFrame(160, 90, "rgb24")
        vframe.pts = 0
        vframe.time_base = Fraction(1, 10)
        for packet in video.encode(vframe):
            output.mux(packet)
        for packet in video.encode():
            output.mux(packet)
        aframe = av.AudioFrame(format="s16", layout="mono", samples=72)
        aframe.sample_rate = 48000
        aframe.pts = 0
        aframe.time_base = Fraction(1, 48000)
        aframe.planes[0].update(b"\x10\x20" * 72)
        for packet in audio.encode(aframe):
            output.mux(packet)
        for packet in audio.encode():
            output.mux(packet)

    wav, _ = extract_wav_bytes(short)
    with wave.open(io.BytesIO(wav)) as decoded:
        assert decoded.getnframes() > 0


def test_audio_decode_failure_has_media_processing_error(tmp_path: Path) -> None:
    from gflow_cli.services.video_captions import extract_wav_bytes

    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    with pytest.raises(VideoProcessingError, match="Could not decode") as caught:
        extract_wav_bytes(broken)
    assert caught.value.charged == "no"
    assert caught.value.__cause__ is not None


def test_caption_font_renders_latin_diacritics_and_cyrillic() -> None:

    from gflow_cli.services.video_captions import _load_caption_font

    font = _load_caption_font(720)
    tofu = font.getmask("\U0010ffff").getbbox()
    glyphs = [
        font.getmask(char).getbbox() for char in "Ação café naïve Привет" if not char.isspace()
    ]
    assert sum(glyph is not None and glyph != tofu for glyph in glyphs) >= 15


@pytest.mark.parametrize(
    ("width", "height", "expected_initial", "expected_fallback"),
    [(1920, 1080, 49, 43), (1280, 720, 32, 29)],
)
def test_caption_overlay_fallback_font_scales_once(
    width: int,
    height: int,
    expected_initial: int,
    expected_fallback: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import ImageDraw, ImageFont

    loaded_sizes: list[int] = []
    original_load = service._load_caption_font

    def record_load(*args: Any, **kwargs: Any) -> Any:
        font = original_load(*args, **kwargs)
        loaded_sizes.append(font.size)
        return font

    monkeypatch.setattr(service, "_load_caption_font", record_load)
    service._caption_overlay(width, height, "one\ntwo\nthree\nfour", ImageDraw, ImageFont)
    assert loaded_sizes == [expected_initial, expected_fallback]


def test_caption_background_is_translucent_on_rgb_frame() -> None:
    av = pytest.importorskip("av")
    from PIL import Image, ImageDraw, ImageFont

    from gflow_cli.services.video_captions import _draw_caption

    frame = av.VideoFrame.from_image(Image.new("RGB", (320, 180), (160, 160, 160)))
    frame.time_base = Fraction(1, 25)
    result = _draw_caption(av, frame, "X", ImageDraw, ImageFont)
    pixel = result.to_image().getpixel((145, 145))
    assert pixel[0] not in (0, 160)


@pytest.mark.parametrize("size", [(1280, 720), (720, 1280)])
def test_long_caption_wraps_within_frame(size: tuple[int, int]) -> None:
    from PIL import ImageDraw, ImageFont

    from gflow_cli.services.video_captions import _wrap_caption

    image = __import__("PIL").Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=30)
    lines = _wrap_caption("word " * 21, font, draw, size[0] * 0.9)
    assert 1 < len(lines) <= 3
    assert all(draw.textlength(line, font=font) <= size[0] * 0.9 for line in lines)


def test_resolve_video_source_accepts_local_and_verified_catalog_video(
    media_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:

    source, _ = media_files
    assert resolve_video_source(str(source), profile=None, route="test") == source.resolve()
    with pytest.raises(ConfigurationError):
        resolve_video_source(str(source.with_name("missing.mp4")), profile=None, route="test")

    monkeypatch.setattr(
        service,
        "resolve_asset",
        lambda *_args, **_kwargs: SimpleNamespace(kind=AssetKind.VIDEO, local_files=[object()]),
    )
    monkeypatch.setattr(service, "verified_local_path", lambda _local_file: source)
    assert (
        resolve_video_source(
            "11111111-2222-3333-4444-555555555555", profile="default", route="test"
        )
        == source
    )

    monkeypatch.setattr(service, "verified_local_path", lambda _local_file: None)
    with pytest.raises(DataStoreError):
        resolve_video_source(
            "11111111-2222-3333-4444-555555555555", profile="default", route="test"
        )


def test_transcribe_accepts_raw_srt_response(
    media_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setenv("GFLOW_CLI_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    config.reset_settings()

    class Response:
        status_code = 200
        text = "1\n00:00:00,200 --> 00:00:00,900\nspoken words\n"

        def json(self) -> object:
            raise ValueError("not JSON")

    class Client:
        def __init__(self, **_: Any) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def post(self, *_: Any, **__: Any) -> Response:
            return Response()

    monkeypatch.setattr("gflow_cli.services.transcribers.openai_compatible.httpx.Client", Client)
    try:
        result = transcribe_video(str(media_files[0]))
    finally:
        config.reset_settings()
    assert result.segments[0].text == "spoken words"


@pytest.mark.parametrize("segments", [[], [{"start": 0, "end": 0, "text": "bad"}], [None]])
def test_transcription_refuses_empty_or_invalid_segments(segments: list[Any]) -> None:
    from gflow_cli.services.transcribers.openai_compatible import _parse_transcription_response

    response = SimpleNamespace(status_code=200, json=lambda: {"segments": segments})
    with pytest.raises(ConfigurationError, match="no speech found") as caught:
        _parse_transcription_response(response)
    assert caught.value.charged == "no"


def test_silent_transcription_does_not_write_srt(
    media_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:

    source, _ = media_files
    monkeypatch.setenv("GFLOW_CLI_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    config.reset_settings()

    class Response:
        status_code = 200
        text = ""

        def json(self) -> dict[str, Any]:
            return {"segments": []}

    class Client:
        def __init__(self, **_: Any) -> None: ...

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def post(self, *_: Any, **__: Any) -> Response:
            return Response()

    monkeypatch.setattr("gflow_cli.services.transcribers.openai_compatible.httpx.Client", Client)
    try:
        with pytest.raises(ConfigurationError, match="no speech found") as caught:
            transcribe_video(str(source))
    finally:
        config.reset_settings()
    assert caught.value.charged == "no"
    assert not source.with_suffix(".srt").exists()


def test_transcription_requires_verbose_json_segments() -> None:
    from gflow_cli.services.transcribers.openai_compatible import _parse_transcription_response

    response = SimpleNamespace(status_code=200, json=lambda: {"text": "hello"})
    with pytest.raises(ConfigurationError, match="verbose_json"):
        _parse_transcription_response(response)


@pytest.mark.parametrize("bad_text", [None, ["array"], {"text": "object"}])
def test_transcription_rejects_non_string_segment_text(bad_text: Any) -> None:
    from gflow_cli.services.transcribers.openai_compatible import _parse_transcription_response

    response = SimpleNamespace(
        status_code=200,
        json=lambda: {"segments": [{"start": 0.1, "end": 0.9, "text": bad_text}]},
    )
    with pytest.raises(ConfigurationError, match="invalid response") as caught:
        _parse_transcription_response(response)
    assert caught.value.charged == "no"


def test_transcription_json_cue_text_collapses_blank_lines_for_srt_roundtrip() -> None:
    from gflow_cli.services.transcribers.openai_compatible import _parse_transcription_response

    response = SimpleNamespace(
        status_code=200,
        json=lambda: {"segments": [{"start": 0.1, "end": 0.9, "text": "  First\n\nSecond  "}]},
    )
    transcript = _parse_transcription_response(response)
    entries = [
        CaptionEntry(i, seg.start, seg.end, seg.text)
        for i, seg in enumerate(transcript.segments, 1)
    ]
    assert parse_srt(format_srt(entries)) == [CaptionEntry(1, 0.1, 0.9, "First\nSecond")]


def test_transcribe_posts_wav_and_writes_timed_json_segments(
    media_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setenv("GFLOW_CLI_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("GFLOW_CLI_TRANSCRIBE_MODEL", "test-whisper")
    config.reset_settings()
    captured: dict[str, Any] = {}

    class Response:
        status_code = 200
        text = ""

        def json(self) -> dict[str, Any]:
            return {"segments": [{"start": 0.2, "end": 0.9, "text": "spoken words"}]}

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            captured["client_options"] = kwargs

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def post(self, url: str, **kwargs: Any) -> Response:
            captured.update(url=url, **kwargs)
            return Response()

    monkeypatch.setattr("gflow_cli.services.transcribers.openai_compatible.httpx.Client", Client)
    try:
        result = transcribe_video(str(media_files[0]), language="en")
    finally:
        config.reset_settings()

    assert captured["url"].endswith("/audio/transcriptions")
    assert captured["client_options"]["follow_redirects"] is False
    assert captured["data"] == {
        "model": "test-whisper",
        "response_format": "verbose_json",
        "language": "en",
    }
    assert captured["files"]["file"][2] == "audio/wav"
    assert result.srt_file.read_text(encoding="utf-8").startswith("1\n00:00:00,200")


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (302, ConfigurationError),
        (400, ConfigurationError),
        (429, RateLimitError),
        (503, NetworkError),
    ],
)
def test_transcribe_maps_http_errors_without_exposing_response_text(
    media_files: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    error_type: type[Exception],
) -> None:

    monkeypatch.setenv("GFLOW_CLI_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    config.reset_settings()

    class Response:
        text = "HTTP 403 denied for Bearer ya29.a0Af-secret123 token"

        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    class Client:
        def __init__(self, **_: Any) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def post(self, *_: Any, **__: Any) -> Response:
            return Response(status)

    monkeypatch.setattr("gflow_cli.services.transcribers.openai_compatible.httpx.Client", Client)
    try:
        with pytest.raises(error_type) as caught:
            transcribe_video(str(media_files[0]))
    finally:
        config.reset_settings()
    assert caught.value.charged == "no"
    assert "ya29.a0Af-secret123" not in caught.value.detail


def test_caption_video_burns_frames_and_keeps_audio(
    media_files: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    av = pytest.importorskip("av")

    source, _ = media_files
    overlays = 0
    original_overlay = service._caption_overlay

    def count_overlay(*args: Any) -> Any:
        nonlocal overlays
        overlays += 1
        return original_overlay(*args)

    monkeypatch.setattr(service, "_caption_overlay", count_overlay)
    srt = tmp_path / "sample.srt"
    srt.write_text("1\n00:00:00,200 --> 00:00:01,000\nA caption\n", encoding="utf-8")
    result = caption_video(str(source), srt_path=srt)
    assert result.frames_burned > 0
    assert overlays == 1
    with av.open(str(result.output_file)) as output:
        assert output.streams.audio


def test_realistic_video_transcription_decode_and_burn_preserve_media(
    realistic_media: Path, tmp_path: Path
) -> None:
    av = pytest.importorskip("av")
    from gflow_cli.services.video_captions import extract_wav_bytes

    wav, source_duration = extract_wav_bytes(realistic_media)
    import wave

    with wave.open(io.BytesIO(wav)) as decoded:
        assert decoded.getnframes() / decoded.getframerate() == pytest.approx(4.0, abs=0.1)
    srt = tmp_path / "captions.srt"
    srt.write_text("1\n00:00:00,200 --> 00:00:01,000\nA caption", encoding="utf-8")
    rendered = caption_video(str(realistic_media), srt_path=srt)
    with av.open(str(realistic_media)) as source, av.open(str(rendered.output_file)) as output:
        source_frames = len(list(source.decode(video=0)))
        output_frames = len(list(output.decode(video=0)))
        assert output_frames == source_frames == 96
    with av.open(str(realistic_media)) as source, av.open(str(rendered.output_file)) as output:
        source_audio = list(source.demux(audio=0))
        output_audio = list(output.demux(audio=0))
        source_audio_count = sum(packet.size > 0 for packet in source_audio)
        assert source_audio_count > 0
        assert sum(packet.size > 0 for packet in output_audio) >= source_audio_count
        assert output.duration / av.time_base == pytest.approx(source_duration, abs=0.1)


def test_local_caption_burnin_works_with_cloud_storage_configured(
    media_files: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFLOW_CLI_STORAGE_URI", "s3://bucket/output")
    config.reset_settings()
    srt = tmp_path / "captions.srt"
    srt.write_text("1\n00:00:00,200 --> 00:00:01,000\nhello", encoding="utf-8")
    try:
        result = caption_video(str(media_files[0]), srt_path=srt)
    finally:
        config.reset_settings()
    assert result.output_file.is_file()


def test_caption_video_refuses_non_utf8_srt_with_clear_error(
    media_files: tuple[Path, Path], tmp_path: Path
) -> None:
    srt = tmp_path / "broken.srt"
    srt.write_bytes(b"\xff\xfe invalid")
    with pytest.raises(ConfigurationError, match="UTF-8") as caught:
        caption_video(str(media_files[0]), srt_path=srt)
    assert caught.value.charged == "no"


def test_caption_video_maps_local_render_failure_to_processing_error(
    media_files: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    srt = tmp_path / "captions.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello", encoding="utf-8")
    monkeypatch.setattr(
        service, "_render_video", lambda *_: (_ for _ in ()).throw(ValueError("decode"))
    )
    with pytest.raises(VideoProcessingError, match="Could not process the video") as caught:
        caption_video(str(media_files[0]), srt_path=srt)
    assert caught.value.charged == "no"
    assert isinstance(caught.value.__cause__, ValueError)


def test_caption_video_uses_unique_temp_file_and_preserves_dot_tmp_source(
    media_files: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_bytes = media_files[0].read_bytes()
    tricky_source = tmp_path / ".out.tmp.mp4"
    tricky_source.write_bytes(source_bytes)
    srt = tmp_path / "captions.srt"
    srt.write_text("1\n00:00:00,200 --> 00:00:00,800\nhello", encoding="utf-8")
    destination = tmp_path / "out.mp4"

    seen_temps: list[Path] = []
    sibling_temp = tmp_path / ".out.other.mp4"
    sibling_temp.write_bytes(b"in-flight run")

    def fake_render(_av: Any, src: Path, temp_dest: Path, *_args: Any) -> tuple[int, int, float]:
        seen_temps.append(temp_dest)
        assert temp_dest != src
        assert temp_dest.parent == destination.parent
        assert temp_dest.suffix == ".mp4"
        if len(seen_temps) == 1:
            raise ValueError("first run fails")
        temp_dest.write_bytes(b"rendered-mp4")
        return 20, 5, 2.0

    monkeypatch.setattr(service, "_render_video", fake_render)
    with pytest.raises(VideoProcessingError):
        caption_video(str(tricky_source), srt_path=srt, out_file=destination)
    assert tricky_source.exists() and tricky_source.read_bytes() == source_bytes
    assert sibling_temp.read_bytes() == b"in-flight run"
    assert not seen_temps[0].exists()

    result = caption_video(str(tricky_source), srt_path=srt, out_file=destination)
    assert len(seen_temps) == 2
    assert seen_temps[0] != seen_temps[1]
    assert result.output_file == destination
    assert tricky_source.read_bytes() == source_bytes
    assert sibling_temp.read_bytes() == b"in-flight run"


def test_render_video_pyav12_audio_stream_fallback_uses_template_only(tmp_path: Path) -> None:
    video_stream = SimpleNamespace(average_rate=25, width=160, height=90, time_base=Fraction(1, 25))
    audio_stream = SimpleNamespace(codec_context=SimpleNamespace(name="aac"))
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    class StubInput:
        duration = 1000000
        streams = SimpleNamespace(video=[video_stream], audio=[audio_stream])

        def __enter__(self) -> StubInput:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def decode(self, _stream: Any) -> list[Any]:
            return []

        def seek(self, _offset: int) -> None:
            return None

        def demux(self, _stream: Any) -> list[Any]:
            return []

    class StubEncodedStream:
        width = 0
        height = 0
        pix_fmt = ""

        def encode(self, _frame: Any = None) -> list[Any]:
            return []

    class StubOutput:
        def __enter__(self) -> StubOutput:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def add_stream(self, *args: Any, **kwargs: Any) -> StubEncodedStream:
            calls.append((args, kwargs))
            if "template" in kwargs and args:
                raise ValueError("PyAV 12 rejects codec_name with template")
            return StubEncodedStream()

        def mux(self, _packet: Any) -> None:
            return None

    stub_av = SimpleNamespace(
        time_base=1000000,
        open=lambda _path, mode="r", **_kw: StubOutput() if mode == "w" else StubInput(),
    )
    service._render_video(
        stub_av, tmp_path / "in.mp4", tmp_path / "out.mp4", [], object(), object()
    )
    assert calls[1] == ((), {"template": audio_stream})


def test_caption_video_transcribes_first_and_writes_both_outputs(
    media_files: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    source, _ = media_files
    transcript = TranscribeResult(
        source, tmp_path / "clip.srt", "en", 2.0, (CaptionEntry(1, 0.2, 1.0, "Hello"),)
    )
    monkeypatch.setattr(service, "transcribe_video", lambda *_args, **_kwargs: transcript)
    result = caption_video(str(source), srt_out_file=transcript.srt_file)
    assert result.srt_file == transcript.srt_file
    assert result.output_file.is_file()
    assert result.frames_burned > 0


def test_caption_and_transcribe_accept_transcriber_port(
    media_files: tuple[Path, Path], tmp_path: Path
) -> None:
    class FakeTranscriber:
        def transcribe(self, request: TranscriptionRequest) -> Transcript:
            assert request.audio_bytes.startswith(b"RIFF")
            assert request.model == config.get_settings().transcribe_model
            return Transcript((TranscriptSegment(0.2, 0.9, "spoken words"),))

    source, _ = media_files
    fake = FakeTranscriber()
    transcript = transcribe_video(
        str(source), out_file=tmp_path / "standalone.srt", transcriber=fake
    )
    assert transcript.segments == (CaptionEntry(1, 0.2, 0.9, "spoken words"),)
    rendered = caption_video(
        str(source),
        srt_out_file=tmp_path / "caption.srt",
        out_file=tmp_path / "captioned.mkv",
        transcriber=fake,
    )
    assert rendered.srt_file.read_text(encoding="utf-8").startswith("1\n00:00:00,200")
    assert rendered.output_file.is_file()
