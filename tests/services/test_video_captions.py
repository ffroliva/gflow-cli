"""Behavioral contract for local video transcription and captioning."""

from __future__ import annotations

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
)
from gflow_cli.services.video_captions import (
    CaptionEntry,
    TranscribeResult,
    caption_video,
    format_srt,
    extract_wav_bytes,
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


def test_parse_and_format_srt_roundtrip() -> None:
    entries = parse_srt("\ufeff1\r\n00:00:00,500 --> 00:00:01.200\r\nHello\r\nworld\r\n")
    assert entries == [CaptionEntry(1, 0.5, 1.2, "Hello\nworld")]
    assert parse_srt(format_srt(entries)) == entries


def test_parse_srt_skips_empty_cue_without_merging_next() -> None:
    content = "1\n00:00:00,000 --> 00:00:01,000\n\n2\n00:00:01,000 --> 00:00:02,000\nkept"
    assert [entry.text for entry in parse_srt(content)] == ["kept"]


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
    with pytest.raises(FrameExtractionError):
        caption_video("clip.mp4", srt_path=Path("captions.srt"))


def test_transcribe_refuses_default_google_endpoint(media_files: tuple[Path, Path]) -> None:
    with pytest.raises(ConfigurationError, match="GFLOW_CLI_LLM_BASE_URL"):
        transcribe_video(str(media_files[0]))


def test_transcribe_refuses_existing_srt_before_network(
    media_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.services import video_captions as service

    monkeypatch.setenv("GFLOW_CLI_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    config.reset_settings()
    existing = media_files[0].with_suffix(".srt")
    existing.write_text("keep me", encoding="utf-8")
    monkeypatch.setattr(service, "_request_segments", lambda *_: pytest.fail("network called"))
    try:
        with pytest.raises(ConfigurationError, match="already exists") as caught:
            transcribe_video(str(media_files[0]))
    finally:
        config.reset_settings()
    assert caught.value.charged == "no"
    assert "--srt" in caught.value.remediation_hint
    assert existing.read_text(encoding="utf-8") == "keep me"


def test_transcribe_refuses_video_without_audio(media_files: tuple[Path, Path]) -> None:
    with pytest.raises(ConfigurationError, match="audio"):
        transcribe_video(str(media_files[1]))


def test_audio_decode_does_not_require_numpy(
    media_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    av = pytest.importorskip("av")
    monkeypatch.setattr(av.AudioFrame, "to_ndarray", lambda *_: pytest.fail("NumPy path used"))
    wav, _ = extract_wav_bytes(media_files[0])
    assert wav.startswith(b"RIFF")


def test_caption_font_renders_latin_diacritics_and_cyrillic() -> None:
    from gflow_cli.services.video_captions import _load_caption_font
    from PIL import ImageFont

    font = _load_caption_font(720)
    rendered = font.getmask("Ação café naïve Привет").getbbox()
    tofu = ImageFont.load_default(size=font.size).getmask("\ufffd").getbbox()
    assert rendered is not None and rendered != tofu


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
    from gflow_cli.services import video_captions as service

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
    from gflow_cli.services import video_captions as service

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

    monkeypatch.setattr(service.httpx, "Client", Client)
    try:
        result = transcribe_video(str(media_files[0]))
    finally:
        config.reset_settings()
    assert result.segments[0].text == "spoken words"


@pytest.mark.parametrize("segments", [[], [{"start": 0, "end": 0, "text": "bad"}], [None]])
def test_transcription_refuses_empty_or_invalid_segments(segments: list[Any]) -> None:
    from gflow_cli.services.video_captions import _parse_transcription_response

    response = SimpleNamespace(status_code=200, json=lambda: {"segments": segments})
    with pytest.raises(ConfigurationError, match="no speech found") as caught:
        _parse_transcription_response(response)
    assert caught.value.charged == "no"


def test_transcription_requires_verbose_json_segments() -> None:
    from gflow_cli.services.video_captions import _parse_transcription_response

    response = SimpleNamespace(status_code=200, json=lambda: {"text": "hello"})
    with pytest.raises(ConfigurationError, match="verbose_json"):
        _parse_transcription_response(response)


def test_transcribe_posts_wav_and_writes_timed_json_segments(
    media_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.services import video_captions as service

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

    monkeypatch.setattr(service.httpx, "Client", Client)
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
    from gflow_cli.services import video_captions as service

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

    monkeypatch.setattr(service.httpx, "Client", Client)
    try:
        with pytest.raises(error_type) as caught:
            transcribe_video(str(media_files[0]))
    finally:
        config.reset_settings()
    assert caught.value.charged == "no"
    assert "ya29.a0Af-secret123" not in caught.value.detail


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


def test_caption_video_refuses_non_utf8_srt_with_clear_error(
    media_files: tuple[Path, Path], tmp_path: Path
) -> None:
    srt = tmp_path / "broken.srt"
    srt.write_bytes(b"\xff\xfe invalid")
    with pytest.raises(ConfigurationError, match="UTF-8") as caught:
        caption_video(str(media_files[0]), srt_path=srt)
    assert caught.value.charged == "no"


def test_caption_video_transcribes_first_and_writes_both_outputs(
    media_files: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.services import video_captions as service

    source, _ = media_files
    transcript = TranscribeResult(
        source, tmp_path / "clip.srt", "en", 2.0, (CaptionEntry(1, 0.2, 1.0, "Hello"),)
    )
    monkeypatch.setattr(service, "transcribe_video", lambda *_args, **_kwargs: transcript)
    result = caption_video(str(source), srt_out_file=transcript.srt_file)
    assert result.srt_file == transcript.srt_file
    assert result.output_file.is_file()
    assert result.frames_burned > 0
