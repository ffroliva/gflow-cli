"""Local transcription and burned-in caption processing."""

from __future__ import annotations

import io
import re
import tempfile
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
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
    nothing_submitted,
)
from gflow_cli.services.media_recovery import resolve_asset
from gflow_cli.storage import refuse_cloud_storage_for_local_file

log = structlog.get_logger(__name__)
_TIMESTAMP = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")
_BLOCK = re.compile(
    r"(?m)^\s*(\d+)\s*\n\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"(\d{2}:\d{2}:\d{2}[,.]\d{3})[^\n]*\n(.*?)(?=\n\s*\n|\Z)",
    re.S,
)


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
    position = 0
    for block_number, match in enumerate(_BLOCK.finditer(content), 1):
        if content[position : match.start()].strip():
            raise ConfigurationError(
                detail=f"Invalid SubRip file {source_name!r} at block {block_number}.", charged="no"
            )
        start, end = _seconds(match.group(2)), _seconds(match.group(3))
        text = match.group(4).strip()
        if start < 0 or end <= start or not text:
            raise ConfigurationError(
                detail=f"Invalid SubRip file {source_name!r} at block {block_number}.", charged="no"
            )
        entries.append(CaptionEntry(int(match.group(1)), start, end, text))
        position = match.end()
    if not entries or content[position:].strip():
        raise ConfigurationError(
            detail=f"Invalid SubRip file {source_name!r} at block 1.", charged="no"
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
                detail=f"Video file does not exist: {path}", charged="no", route=route
            )
        return path
    with nothing_submitted():
        asset = resolve_asset(file_or_id, profile=profile, route=route)
        if asset.kind is not AssetKind.VIDEO:
            raise ConfigurationError(
                detail=f"Catalog media {file_or_id} is not a video.", charged="no"
            )
        for local_file in asset.local_files:
            if (verified := verified_local_path(local_file)) is not None:
                return verified
        raise DataStoreError(
            detail=f"No verified local video copy for {file_or_id}.",
            route=route,
            charged="no",
            remediation_hint=f"Run `gflow data download {file_or_id}` first, or pass a local video path.",
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
                    detail="Video has no audio track to transcribe.", charged="no"
                )
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            samples: list[bytes] = []
            duration = float(container.duration or 0) / av.time_base
            for frame in container.decode(stream):
                for converted in resampler.resample(frame):
                    samples.append(converted.to_ndarray().tobytes())
            if not samples:
                raise ConfigurationError(
                    detail="Video audio track contains no decodable audio.", charged="no"
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
    refuse_cloud_storage_for_local_file(settings.storage_uri, "Video transcription")
    base_url = (settings.llm_base_url or DEFAULT_LLM_BASE_URL).rstrip("/")
    if base_url == DEFAULT_LLM_BASE_URL.rstrip("/"):
        raise ConfigurationError(
            detail=(
                "Google's default endpoint does not offer /audio/transcriptions; audio was not sent. "
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
            detail="SRT output must not overwrite the input video.", charged="no"
        )
    audio, duration = extract_wav_bytes(source)
    headers = {}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key.get_secret_value()}"
    data: dict[str, str] = {
        "model": settings.llm_model or "whisper-1",
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
        if 300 <= response.status_code < 400:
            raise ConfigurationError(
                detail="Transcription endpoint redirected; request refused.", charged="no"
            )
        if response.status_code == 429:
            raise RateLimitError(
                detail="Transcription endpoint rate limit reached.", retry_after=None
            )
        if response.status_code >= 500:
            raise NetworkError(
                detail=f"Transcription endpoint failed: {redact_error_detail(response.text)}",
                charged="no",
            )
        if response.status_code >= 400:
            raise ConfigurationError(
                detail=f"Transcription request rejected: {redact_error_detail(response.text)}",
                charged="no",
            )
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("segments"), list):
            segments = tuple(
                CaptionEntry(i, float(seg["start"]), float(seg["end"]), str(seg["text"]).strip())
                for i, seg in enumerate(payload["segments"], 1)
                if str(seg.get("text", "")).strip()
            )
        elif isinstance(payload, dict) and payload.get("text"):
            segments = tuple(parse_srt(str(payload["text"]), source_name="transcription response"))
        else:
            segments = tuple(parse_srt(response.text, source_name="transcription response"))
    except (ConfigurationError, RateLimitError, NetworkError):
        raise
    except httpx.HTTPError as exc:
        raise NetworkError(
            detail="Transcription endpoint could not be reached.", charged="no"
        ) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise ConfigurationError(
            detail="Transcription endpoint returned an invalid response.", charged="no"
        ) from exc
    _atomic_write(destination, format_srt(segments).encode("utf-8"))
    log.info("video.transcribe.completed", segments=len(segments), host=urlsplit(base_url).hostname)
    return TranscribeResult(source, destination, language, duration, segments)


def caption_video(
    file_or_id: str,
    *,
    srt_path: Path | None = None,
    out_file: Path | None = None,
    language: str | None = None,
    profile: str | None = None,
) -> CaptionsResult:
    av, (Image, ImageDraw, ImageFont) = _require_chain_extra()
    settings = get_settings()
    refuse_cloud_storage_for_local_file(settings.storage_uri, "Video caption burn-in")
    source = resolve_video_source(file_or_id, profile=profile, route="video.captions")
    if srt_path is None:
        transcribed = transcribe_video(file_or_id, language=language, profile=profile)
        caption_path, entries = transcribed.srt_file, list(transcribed.segments)
    else:
        caption_path = srt_path.expanduser().resolve()
        try:
            entries = parse_srt(
                caption_path.read_text(encoding="utf-8-sig"), source_name=caption_path.name
            )
        except OSError as exc:
            raise ConfigurationError(
                detail=f"Could not read SubRip file {caption_path.name!r}.", charged="no"
            ) from exc
    destination = (
        (out_file or source.with_name(f"{source.stem}-captions.mp4")).expanduser().resolve()
    )
    if destination in {source.resolve(), caption_path.resolve()}:
        raise ConfigurationError(
            detail="Caption output must not overwrite an input file.", charged="no"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}.tmp{destination.suffix}")
    frames = burned = 0
    try:
        with (
            av.open(str(source)) as input_container,
            av.open(str(temporary), mode="w", format="mp4") as output,
        ):
            video = input_container.streams.video[0]
            audio_stream = (
                input_container.streams.audio[0] if input_container.streams.audio else None
            )
            fps = video.average_rate or 25
            encoded = output.add_stream("libx264", rate=fps)
            encoded.width, encoded.height, encoded.pix_fmt = video.width, video.height, "yuv420p"
            audio_output = output.add_stream_from_template(audio_stream) if audio_stream else None
            time_base = video.time_base
            for frame in input_container.decode(video):
                frames += 1
                at = (
                    float(frame.pts * time_base)
                    if frame.pts is not None
                    else (frames - 1) / float(fps)
                )
                active = next(
                    (entry.text for entry in entries if entry.start_s <= at <= entry.end_s), None
                )
                if active:
                    burned += 1
                    original_pts, original_time_base = frame.pts, frame.time_base
                    image = frame.to_image().convert("RGBA")
                    draw = ImageDraw.Draw(image, "RGBA")
                    font = ImageFont.load_default(size=max(14, round(frame.height * 0.045)))
                    lines = active.splitlines()
                    box = draw.multiline_textbbox(
                        (0, 0), "\n".join(lines), font=font, align="center", spacing=2
                    )
                    x = max(8, (frame.width - (box[2] - box[0])) // 2)
                    y = max(8, frame.height - (box[3] - box[1]) - 28)
                    draw.rounded_rectangle(
                        (x - 10, y - 6, x + box[2] - box[0] + 10, y + box[3] - box[1] + 6),
                        8,
                        fill=(0, 0, 0, 180),
                    )
                    draw.multiline_text(
                        (x, y),
                        "\n".join(lines),
                        font=font,
                        fill=(255, 255, 255, 255),
                        align="center",
                        spacing=2,
                    )
                    frame = av.VideoFrame.from_image(image.convert("RGB"))
                    frame.pts = original_pts
                    frame.time_base = original_time_base
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
        temporary.replace(destination)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, ConfigurationError):
            raise
        raise FrameExtractionError(
            detail="Could not render captions into the video.", charged="no"
        ) from exc
    return CaptionsResult(
        source, destination, caption_path, 0.0, frames, burned, destination.stat().st_size
    )
