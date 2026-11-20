"""Local transcription and burned-in caption processing."""

from __future__ import annotations

import io
import math
import os
import re
import tempfile
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import httpx
import structlog

from gflow_cli.api.video import is_media_uuid
from gflow_cli.config import DEFAULT_LLM_BASE_URL, get_settings
from gflow_cli.data._repo_base import verified_local_path
from gflow_cli.data.models import AssetKind
from gflow_cli.data.redaction import redact_error_detail
from gflow_cli.errors import (
    ConfigurationError,
    DataStoreError,
    FrameExtractionError,
    NetworkError,
    RateLimitError,
    VideoProcessingError,
    nothing_submitted,
)
from gflow_cli.services.media_recovery import resolve_asset

log = structlog.get_logger(__name__)
_TIMESTAMP = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


@dataclass(frozen=True)
class CaptionEntry:
    index: int
    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True)
class TranscribeResult:
    source_file: Path
    srt_file: Path
    language: str | None
    duration_seconds: float
    segments: tuple[CaptionEntry, ...]


@dataclass(frozen=True)
class CaptionsResult:
    source_file: Path
    output_file: Path
    srt_file: Path
    duration_seconds: float
    total_frames: int
    frames_burned: int
    bytes: int


def _seconds(value: str) -> float:
    match = _TIMESTAMP.fullmatch(value)
    assert match
    h, m, s, ms = map(int, match.groups())
    return h * 3600 + m * 60 + s + ms / 1000


def parse_srt(content: str, *, source_name: str = "<srt>") -> list[CaptionEntry]:
    content = content.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    entries: list[CaptionEntry] = []
    for block_number, block in enumerate(re.split(r"\n\s*\n", content), 1):
        lines = block.splitlines()
        match = re.fullmatch(
            r"\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})[^\n]*",
            lines[1] if len(lines) >= 2 and lines[0].strip().isdigit() else "",
        )
        if not match:
            raise ConfigurationError(
                detail=f"Invalid SubRip file {source_name!r} at block {block_number}.",
                charged="no",
                remediation_hint="Correct the SRT block index and timestamp lines, then retry.",
            )
        text = "\n".join(lines[2:]).strip()
        if not text:
            continue
        start, end = _seconds(match.group(1)), _seconds(match.group(2))
        if end <= start:
            raise ConfigurationError(
                detail=f"Invalid SubRip file {source_name!r} at block {block_number}.",
                charged="no",
                remediation_hint="Correct the SRT timestamps so the end follows the start.",
            )
        entries.append(CaptionEntry(int(lines[0].strip()), start, end, text))
    if not entries:
        raise ConfigurationError(
            detail=f"Invalid SubRip file {source_name!r} at block 1.",
            charged="no",
            remediation_hint="Provide an SRT file with at least one timed caption cue.",
        )
    return entries


def format_srt(entries: Sequence[CaptionEntry]) -> str:
    def timestamp(seconds: float) -> str:
        millis = round(seconds * 1000)
        hours, millis = divmod(millis, 3_600_000)
        minutes, millis = divmod(millis, 60_000)
        secs, millis = divmod(millis, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    return (
        "\n\n".join(
            f"{i}\n{timestamp(item.start_s)} --> {timestamp(item.end_s)}\n{item.text}"
            for i, item in enumerate(entries, 1)
        )
        + "\n"
    )


def _require_chain_extra() -> tuple[Any, Any]:
    try:
        import av
        from PIL import Image, ImageDraw, ImageFont

        return av, (Image, ImageDraw, ImageFont)
    except ImportError as exc:
        raise FrameExtractionError(
            detail="Install video processing support with `pip install 'gflow-cli[chain]'`.",
            charged="no",
        ) from exc


def resolve_video_source(file_or_id: str, *, profile: str | None, route: str) -> Path:
    path = Path(file_or_id).expanduser()
    if not is_media_uuid(file_or_id):
        path = path.resolve()
        if not path.is_file():
            raise ConfigurationError(
                detail=f"Video file does not exist: {path}",
                charged="no",
                route=route,
                remediation_hint="Check the path and pass an existing local video file.",
            )
        return path
    with nothing_submitted():
        asset = resolve_asset(file_or_id, profile=profile, route=route)
        if asset.kind is not AssetKind.VIDEO:
            raise ConfigurationError(
                detail=f"Catalog media {file_or_id} is not a video.",
                charged="no",
                remediation_hint="Pass a catalog UUID whose media type is video.",
            )
        for local_file in asset.local_files:
            if (verified := verified_local_path(local_file)) is not None:
                return verified
        raise DataStoreError(
            detail=f"No verified local video copy for {file_or_id}.",
            route=route,
            charged="no",
            remediation_hint=(
                f"Run `gflow data download {file_or_id}` first, or pass a local video path."
            ),
        )


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as f:
        temporary = Path(f.name)
        f.write(data)
    temporary.replace(path)


def extract_wav_bytes(video_path: Path) -> tuple[bytes, float]:
    av, _ = _require_chain_extra()
    try:
        with av.open(str(video_path)) as container:
            if not container.streams.audio:
                raise ConfigurationError(
                    detail="Video has no audio track to transcribe.",
                    charged="no",
                    remediation_hint="Pass a video containing an audio track.",
                )
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            samples: list[bytes] = []
            duration = float(container.duration or 0) / av.time_base
            for frame in container.decode(stream):
                for converted in resampler.resample(frame):
                    samples.append(bytes(converted.planes[0])[: converted.samples * 2])
            if not samples:
                raise ConfigurationError(
                    detail="Video audio track contains no decodable audio.",
                    charged="no",
                    remediation_hint="Check the audio codec and re-encode the clip if needed.",
                )
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"".join(samples))
        return buffer.getvalue(), duration
    except ConfigurationError:
        raise
    except Exception as exc:
        raise FrameExtractionError(
            detail="Could not decode the video's audio track.", charged="no"
        ) from exc


def transcribe_video(
    file_or_id: str,
    *,
    out_file: Path | None = None,
    language: str | None = None,
    profile: str | None = None,
) -> TranscribeResult:
    _require_chain_extra()
    settings = get_settings()
    base_url = (settings.llm_base_url or DEFAULT_LLM_BASE_URL).rstrip("/")
    if base_url == DEFAULT_LLM_BASE_URL.rstrip("/"):
        raise ConfigurationError(
            detail=(
                "Google's default endpoint does not offer /audio/transcriptions; "
                "audio was not sent. "
                "Configure GFLOW_CLI_LLM_BASE_URL for a transcription provider or use --srt."
            ),
            charged="no",
            remediation_hint=(
                "Configure GFLOW_CLI_LLM_BASE_URL, GFLOW_CLI_LLM_API_KEY, and "
                "GFLOW_CLI_LLM_MODEL for an OpenAI, Groq, or local Whisper server; "
                "or pass --srt to video captions."
            ),
        )
    source = resolve_video_source(file_or_id, profile=profile, route="video.transcribe")
    destination = (out_file or source.with_suffix(".srt")).expanduser().resolve()
    if destination == source.resolve():
        raise ConfigurationError(
            detail="SRT output must not overwrite the input video.",
            charged="no",
            remediation_hint="Choose a separate --out-file path for the SRT.",
        )
    if destination.exists():
        raise ConfigurationError(
            detail=f"SRT output already exists: {destination.name}.",
            charged="no",
            remediation_hint="Pass --srt to video captions to reuse it, or remove the existing file.",
        )
    audio, duration = extract_wav_bytes(source)
    segments = _request_segments(base_url, settings, audio, language)
    _atomic_write(destination, format_srt(segments).encode("utf-8"))
    log.info("video.transcribe.completed", segments=len(segments), host=urlsplit(base_url).hostname)
    return TranscribeResult(source, destination, language, duration, segments)


def _request_segments(
    base_url: str, settings: Any, audio: bytes, language: str | None
) -> tuple[CaptionEntry, ...]:
    headers: dict[str, str] = {}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key.get_secret_value()}"
    data: dict[str, str] = {
        "model": settings.transcribe_model,
        "response_format": "verbose_json",
    }
    if language:
        data["language"] = language
    try:
        with httpx.Client(follow_redirects=False, timeout=120) as client:
            response = client.post(
                f"{base_url}/audio/transcriptions",
                headers=headers,
                data=data,
                files={"file": ("audio.wav", audio, "audio/wav")},
            )
        return _parse_transcription_response(response)
    except (ConfigurationError, RateLimitError, NetworkError):
        raise
    except httpx.HTTPError as exc:
        raise NetworkError(
            detail="Transcription endpoint could not be reached.", charged="no"
        ) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise ConfigurationError(
            detail="Transcription endpoint returned an invalid response.",
            charged="no",
            remediation_hint="Configure the endpoint to return valid verbose_json segments or SRT.",
        ) from exc
    raise AssertionError("unreachable")


def _parse_transcription_response(response: httpx.Response) -> tuple[CaptionEntry, ...]:
    if 300 <= response.status_code < 400:
        raise ConfigurationError(
            detail="Transcription endpoint redirected; request refused.",
            charged="no",
            remediation_hint="Configure the exact transcription endpoint URL without redirects.",
        )
    if response.status_code == 429:
        raise RateLimitError(detail="Transcription endpoint rate limit reached.", retry_after=None)
    if response.status_code >= 500:
        raise NetworkError(
            detail=f"Transcription endpoint failed: {redact_error_detail(response.text)}",
            charged="no",
        )
    if response.status_code >= 400:
        raise ConfigurationError(
            detail=f"Transcription request rejected: {redact_error_detail(response.text)}",
            charged="no",
            remediation_hint="Check the transcription endpoint URL, model, and API key.",
        )
    try:
        payload: Any = response.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return tuple(parse_srt(response.text, source_name="transcription response"))
    parsed = cast(dict[str, Any], payload)
    raw = parsed.get("segments")
    if isinstance(raw, list):
        entries: list[CaptionEntry] = []
        for seg in raw:
            if not isinstance(seg, dict):
                continue
            try:
                start, end = float(seg["start"]), float(seg["end"])
                text = str(seg["text"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
            if text and math.isfinite(start) and math.isfinite(end) and 0 <= start < end:
                entries.append(CaptionEntry(len(entries) + 1, start, end, text))
        if entries:
            return tuple(entries)
        raise ConfigurationError(
            detail="Transcription completed but no speech found.",
            charged="no",
            remediation_hint="Use a clip with speech and retry.",
        )
    raise ConfigurationError(
        detail="Transcription response has no timed segments; request verbose_json.",
        charged="no",
        remediation_hint="Configure the transcription endpoint to return verbose_json segments.",
    )


def caption_video(
    file_or_id: str,
    *,
    srt_path: Path | None = None,
    srt_out_file: Path | None = None,
    out_file: Path | None = None,
    language: str | None = None,
    profile: str | None = None,
) -> CaptionsResult:
    av, (_, image_draw, image_font) = _require_chain_extra()
    settings = get_settings()
    source = resolve_video_source(file_or_id, profile=profile, route="video.captions")
    destination = (
        (out_file or source.with_name(f"{source.stem}-captions.mp4")).expanduser().resolve()
    )
    if destination in {
        source.resolve(),
        (srt_path or srt_out_file or Path()).expanduser().resolve(),
    }:
        raise ConfigurationError(
            detail="Caption output must not overwrite an input file.",
            charged="no",
            remediation_hint="Choose a distinct --out-file path for the captioned video.",
        )
    if destination.exists():
        raise ConfigurationError(
            detail=f"Caption output already exists: {destination.name}.",
            charged="no",
            remediation_hint="Choose another --out path or remove the existing file.",
        )
    if srt_path is None:
        transcribed = transcribe_video(
            file_or_id, out_file=srt_out_file, language=language, profile=profile
        )
        caption_path, entries = transcribed.srt_file, list(transcribed.segments)
    else:
        caption_path = srt_path.expanduser().resolve()
        try:
            entries = parse_srt(
                caption_path.read_text(encoding="utf-8-sig"), source_name=caption_path.name
            )
        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigurationError(
                detail=f"Could not read SubRip file {caption_path.name!r} as UTF-8.",
                charged="no",
                remediation_hint="Save the SubRip file as UTF-8 and retry.",
            ) from exc
    if destination in {source.resolve(), caption_path.resolve()}:
        raise ConfigurationError(
            detail="Caption output must not overwrite an input file.",
            charged="no",
            remediation_hint="Choose a distinct --out-file path for the captioned video.",
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}.tmp{destination.suffix}")
    try:
        frames, burned, duration = _render_video(
            av, source, temporary, entries, image_draw, image_font
        )
        temporary.replace(destination)
    except ConfigurationError:
        temporary.unlink(missing_ok=True)
        raise
    except (av.error.FFmpegError, OSError, ValueError) as exc:
        temporary.unlink(missing_ok=True)
        raise VideoProcessingError(
            detail="Could not process the video while rendering captions.",
            charged="no",
            remediation_hint="Check that the local video is readable and its codecs are supported.",
        ) from exc
    return CaptionsResult(
        source, destination, caption_path, duration, frames, burned, destination.stat().st_size
    )


def _render_video(
    av: Any,
    source: Path,
    destination: Path,
    entries: list[CaptionEntry],
    draw_module: Any,
    font_module: Any,
) -> tuple[int, int, float]:
    frames = burned = 0
    with (
        av.open(str(source)) as input_container,
        av.open(str(destination), mode="w", format="mp4") as output,
    ):
        video = input_container.streams.video[0]
        audio_stream = input_container.streams.audio[0] if input_container.streams.audio else None
        encoded = output.add_stream("libx264", rate=video.average_rate or 25)
        encoded.width, encoded.height, encoded.pix_fmt = video.width, video.height, "yuv420p"
        audio_output = None
        if audio_stream:
            audio_output = (
                output.add_stream_from_template(audio_stream)
                if hasattr(output, "add_stream_from_template")
                else output.add_stream(audio_stream.codec_context.name, template=audio_stream)
            )
        duration = float(input_container.duration or 0) / av.time_base
        for frame in input_container.decode(video):
            frames += 1
            seconds = (
                float(frame.pts * video.time_base)
                if frame.pts is not None
                else (frames - 1) / float(video.average_rate or 25)
            )
            text = next((e.text for e in entries if e.start_s <= seconds <= e.end_s), None)
            if text:
                burned += 1
                frame = _draw_caption(av, frame, text, draw_module, font_module)
            for packet in encoded.encode(frame):
                output.mux(packet)
        for packet in encoded.encode():
            output.mux(packet)
        if audio_stream and audio_output:
            input_container.seek(0)
            for packet in input_container.demux(audio_stream):
                if packet.dts is not None:
                    packet.stream = audio_output
                    output.mux(packet)
    return frames, burned, duration


def _draw_caption(av: Any, frame: Any, text: str, draw_module: Any, font_module: Any) -> Any:
    original_pts, original_time_base = frame.pts, frame.time_base
    image = frame.to_image()
    draw = draw_module.Draw(image, "RGBA")
    font = _load_caption_font(frame.height, font_module)
    lines = _wrap_caption(text, font, draw, frame.width * 0.9)
    if len(lines) > 3:
        font = _load_caption_font(max(12, round(frame.height * 0.04)), font_module)
        lines = _wrap_caption(text, font, draw, frame.width * 0.9)
    box = draw.multiline_textbbox((0, 0), "\n".join(lines), font=font, align="center", spacing=2)
    x = max(8, (frame.width - (box[2] - box[0])) // 2)
    y = max(8, frame.height - (box[3] - box[1]) - 28)
    draw.rounded_rectangle(
        (x - 10, y - 6, x + box[2] - box[0] + 10, y + box[3] - box[1] + 6), 8, fill=(0, 0, 0, 180)
    )
    draw.multiline_text(
        (x, y), "\n".join(lines), font=font, fill=(255, 255, 255, 255), align="center", spacing=2
    )
    result = av.VideoFrame.from_image(image)
    result.pts, result.time_base = original_pts, original_time_base
    return result


def _wrap_caption(text: str, font: Any, draw: Any, max_width: float) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [text]:
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}".strip()
            if line and draw.textlength(candidate, font=font) > max_width:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    return lines


def _load_caption_font(height: int, font_module: Any | None = None) -> Any:
    if font_module is None:
        from PIL import ImageFont as font_module

    size = max(14, round(height * 0.045))
    candidates = (
        [r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"]
        if os.name == "nt"
        else [
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/Library/Fonts/Arial Unicode.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        ]
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            try:
                return font_module.truetype(candidate, size=size)
            except OSError:
                continue
    return font_module.load_default(size=size)
