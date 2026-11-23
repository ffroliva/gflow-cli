"""Local transcription and burned-in caption processing."""

from __future__ import annotations

import io
import itertools
import os
import re
import tempfile
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from gflow_cli.api.video import is_media_uuid
from gflow_cli.config import get_settings
from gflow_cli.data._repo_base import verified_local_path
from gflow_cli.data.models import AssetKind
from gflow_cli.errors import (
    ConfigurationError,
    DataStoreError,
    FrameExtractionError,
    VideoProcessingError,
    nothing_submitted,
)
from gflow_cli.services.media_recovery import resolve_asset
from gflow_cli.services.speech_ports import Transcriber, TranscriptionRequest
from gflow_cli.services.transcribers.openai_compatible import make_transcriber
from gflow_cli.storage import refuse_cloud_storage_for_local_file

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


def result_payload(result: TranscribeResult | CaptionsResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "ok",
        "source_file": str(result.source_file),
        "srt_file": str(result.srt_file),
    }
    if isinstance(result, TranscribeResult):
        payload.update(
            language=result.language,
            duration_seconds=result.duration_seconds,
            segments_count=len(result.segments),
            segments=[
                {
                    "index": item.index,
                    "start_s": item.start_s,
                    "end_s": item.end_s,
                    "text": item.text,
                }
                for item in result.segments
            ],
        )
    else:
        payload.update(
            output_file=str(result.output_file),
            duration_seconds=result.duration_seconds,
            total_frames=result.total_frames,
            frames_burned=result.frames_burned,
            bytes=result.bytes,
        )
    return payload


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


def _srt_destination(source: Path, out_file: Path | None) -> Path:
    return (out_file or source.with_suffix(".srt")).expanduser().resolve()


def reject_explicit_out_file_under_cloud_storage(
    out_file: Path | str | None, flag: str = "--out-file"
) -> None:
    if out_file is not None:
        refuse_cloud_storage_for_local_file(get_settings().storage_uri, flag)


def _prepare_output_parent(destination: Path) -> None:
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigurationError(
            detail=f"Could not prepare output directory for {destination.name!r}.",
            charged="no",
            remediation_hint="Choose an --out-file path inside a writable directory.",
        ) from exc
    if (destination.exists() or destination.is_symlink()) and not destination.is_file():
        raise ConfigurationError(
            detail=f"Output path {destination.name!r} exists and is not a regular file.",
            charged="no",
            remediation_hint="Remove the existing non-file path or choose a different --out-file.",
        )


def _srt_exists_error(destination: Path) -> ConfigurationError:
    return ConfigurationError(
        detail=f"SRT output already exists: {destination.name}.",
        charged="no",
        remediation_hint=("Pass --srt to video captions to reuse it, or remove the existing file."),
    )


def _atomic_write(path: Path, data: bytes) -> None:
    _prepare_output_parent(path)
    temporary: Path | None = None
    try:
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=f".{path.name}.", delete=False
            ) as f:
                temporary = Path(f.name)
                f.write(data)
        except OSError as exc:
            raise ConfigurationError(
                detail=f"Could not write SubRip file {path.name!r}.",
                charged="no",
                remediation_hint="Choose an --out-file path inside a writable directory.",
            ) from exc
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise _srt_exists_error(path) from exc
        except OSError:
            # shortcut: exists() + os.rename() leaves a tiny TOCTOU window on filesystems
            # without hard links; upgrade to platform-specific atomic no-clobber rename
            # (renameat2 RENAME_NOREPLACE / MoveFileExW) if cross-process races appear on FAT/exFAT.
            if path.exists():
                raise _srt_exists_error(path) from None
            try:
                os.rename(temporary, path)
            except FileExistsError as exc:
                raise _srt_exists_error(path) from exc
            except OSError as exc:
                raise ConfigurationError(
                    detail=f"Could not write SubRip file {path.name!r}.",
                    charged="no",
                    remediation_hint="Choose an --out-file path inside a writable directory.",
                ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _stream_start_seconds(stream: Any) -> float:
    start_time = getattr(stream, "start_time", None)
    time_base = getattr(stream, "time_base", None)
    if start_time is None or time_base is None:
        return 0.0
    return float(start_time * time_base)


def _append_resampled_frame(
    frame: Any,
    stream: Any,
    video_origin: float,
    samples: list[bytes],
    written_samples: int,
) -> int:
    tb = frame.time_base or stream.time_base
    target_sample = (
        round((float(frame.pts * tb) - video_origin) * 16000)
        if frame.pts is not None and tb is not None
        else written_samples
    )
    if target_sample > written_samples:
        gap = target_sample - written_samples
        samples.append(bytes(gap * 2))
        written_samples += gap
    chunk = bytes(frame.planes[0])[: frame.samples * 2]
    if target_sample < written_samples:
        chunk = chunk[(written_samples - target_sample) * 2 :]
    if chunk:
        samples.append(chunk)
        written_samples += len(chunk) // 2
    return written_samples


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
            video = container.streams.video[0] if container.streams.video else None
            video_origin = _stream_start_seconds(video)
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            samples: list[bytes] = []
            written_samples = 0
            duration = float(container.duration or 0) / av.time_base
            # chain keeps decoding lazy; None drains the resampler at the end.
            for raw_frame in itertools.chain(container.decode(stream), (None,)):
                if raw_frame is not None and raw_frame.time_base is None:
                    raw_frame.time_base = stream.time_base
                for frame in resampler.resample(raw_frame):
                    written_samples = _append_resampled_frame(
                        frame, stream, video_origin, samples, written_samples
                    )
            if not written_samples:
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
    except (av.error.FFmpegError, OSError, ValueError) as exc:
        raise VideoProcessingError(
            detail="Could not decode the video's audio track.", charged="no"
        ) from exc


def transcribe_video(
    file_or_id: str,
    *,
    out_file: Path | None = None,
    language: str | None = None,
    profile: str | None = None,
    _source: Path | None = None,
    transcriber: Transcriber | None = None,
) -> TranscribeResult:
    _require_chain_extra()
    active_transcriber = transcriber or make_transcriber()
    source = _source or resolve_video_source(file_or_id, profile=profile, route="video.transcribe")
    destination = _srt_destination(source, out_file)
    if destination == source.resolve():
        raise ConfigurationError(
            detail="SRT output must not overwrite the input video.",
            charged="no",
            remediation_hint="Choose a separate --out-file path for the SRT.",
        )
    _prepare_output_parent(destination)
    if destination.exists():
        raise _srt_exists_error(destination)
    audio, duration = extract_wav_bytes(source)
    transcript = active_transcriber.transcribe(
        TranscriptionRequest(
            audio_bytes=audio, language=language, model=get_settings().transcribe_model
        )
    )
    segments = tuple(
        CaptionEntry(index, item.start, item.end, item.text)
        for index, item in enumerate(transcript.segments, 1)
    )
    _atomic_write(destination, format_srt(segments).encode("utf-8"))
    return TranscribeResult(source, destination, language, duration, segments)


def caption_video(
    file_or_id: str,
    *,
    srt_path: Path | None = None,
    srt_out_file: Path | None = None,
    out_file: Path | None = None,
    language: str | None = None,
    profile: str | None = None,
    transcriber: Transcriber | None = None,
) -> CaptionsResult:
    if srt_path is not None:
        _require_chain_extra()
    source = resolve_video_source(file_or_id, profile=profile, route="video.captions")
    destination = (
        (out_file or source.with_name(f"{source.stem}-captions.mp4")).expanduser().resolve()
    )
    planned_srt = (
        srt_path.expanduser().resolve()
        if srt_path is not None
        else _srt_destination(source, srt_out_file)
    )
    if destination in {source.resolve(), planned_srt}:
        raise ConfigurationError(
            detail="Caption output must not overwrite an input file.",
            charged="no",
            remediation_hint="Choose a distinct --out-file path for the captioned video.",
        )
    _prepare_output_parent(destination)
    if srt_path is None:
        _prepare_output_parent(planned_srt)
        transcribed = transcribe_video(
            file_or_id,
            out_file=srt_out_file,
            language=language,
            profile=profile,
            _source=source,
            transcriber=transcriber,
        )
        caption_path, entries = transcribed.srt_file, list(transcribed.segments)
    else:
        caption_path = planned_srt
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
    av, (_, image_draw, image_font) = _require_chain_extra()
    temporary: Path | None = None
    try:
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{destination.stem}.",
            suffix=".mp4",
            dir=destination.parent,
        )
        os.close(descriptor)
        temporary = Path(temp_name)
        frames, burned, duration = _render_video(
            av, source, temporary, entries, image_draw, image_font
        )
        temporary.replace(destination)
    except ConfigurationError:
        raise
    except (av.error.FFmpegError, OSError, ValueError) as exc:
        raise VideoProcessingError(
            detail="Could not process the video while rendering captions.",
            charged="no",
            remediation_hint="Check that the local video is readable and its codecs are supported.",
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
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
                else output.add_stream(template=audio_stream)
            )
        duration = float(input_container.duration or 0) / av.time_base
        video_origin = _stream_start_seconds(video)
        overlays: dict[str, tuple[Any, int]] = {}
        for frame in input_container.decode(video):
            frames += 1
            frame_time = (
                float(frame.time)
                if getattr(frame, "time", None) is not None
                else (
                    float(frame.pts * video.time_base)
                    if frame.pts is not None and video.time_base is not None
                    else (frames - 1) / float(video.average_rate or 25) + video_origin
                )
            )
            seconds = frame_time - video_origin
            text = next((e.text for e in entries if e.start_s <= seconds <= e.end_s), None)
            if text:
                burned += 1
                if text not in overlays:
                    overlays[text] = _caption_overlay(
                        frame.width, frame.height, text, draw_module, font_module
                    )
                frame = _draw_caption(av, frame, text, draw_module, font_module, overlays[text])
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


def _draw_caption(
    av: Any,
    frame: Any,
    text: str,
    draw_module: Any,
    font_module: Any,
    overlay: tuple[Any, int] | None = None,
) -> Any:
    original_pts, original_time_base = frame.pts, frame.time_base
    return _blend_caption(
        av,
        frame,
        *(overlay or _caption_overlay(frame.width, frame.height, text, draw_module, font_module)),
        original_pts=original_pts,
        original_time_base=original_time_base,
    )


def _caption_overlay(
    width: int, height: int, text: str, draw_module: Any, font_module: Any
) -> tuple[Any, int]:
    font = _load_caption_font(height, font_module)
    band_height = min(height, max(round(height * 0.25), round(font.size * 4.5) + 40))
    from PIL import Image

    overlay = Image.new("RGBA", (width, band_height), (0, 0, 0, 0))
    draw = draw_module.Draw(overlay, "RGBA")
    lines = _wrap_caption(text, font, draw, width * 0.9)
    if len(lines) > 3:
        font = _load_caption_font(height, font_module, size=max(12, round(height * 0.04)))
        lines = _wrap_caption(text, font, draw, width * 0.9)
    box = draw.multiline_textbbox((0, 0), "\n".join(lines), font=font, align="center", spacing=2)
    x = max(8, (width - (box[2] - box[0])) // 2)
    y = max(8, band_height - (box[3] - box[1]) - 28)
    draw.rounded_rectangle(
        (x - 10, y - 6, x + box[2] - box[0] + 10, y + box[3] - box[1] + 6), 8, fill=(0, 0, 0, 180)
    )
    draw.multiline_text(
        (x, y), "\n".join(lines), font=font, fill=(255, 255, 255, 255), align="center", spacing=2
    )
    return overlay, height - band_height


def _blend_caption(
    av: Any,
    frame: Any,
    overlay: Any,
    top: int,
    *,
    original_pts: int | None = None,
    original_time_base: Any = None,
) -> Any:
    if original_pts is None:
        original_pts, original_time_base = frame.pts, frame.time_base
    image = frame.to_image()
    image.paste(overlay, (0, top), overlay)
    result = av.VideoFrame.from_image(image)
    result.pts, result.time_base = original_pts, original_time_base
    return result


_NO_LINE_START = frozenset("，。、；：！？）」』》〉】,.;:!?)")


def _wrap_caption(text: str, font: Any, draw: Any, max_width: float) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [text]:
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}" if line else word
            if draw.textlength(candidate, font=font) <= max_width:
                line = candidate
                continue
            if line:
                lines.append(line)
                line = ""
            # A token wider than the line is broken between characters: CJK text has no spaces
            # to break at, and a long URL is a single token. Closing punctuation is not left at
            # the start of a line; the character before it moves down with it.
            for char in word:
                if line and draw.textlength(line + char, font=font) > max_width:
                    if char in _NO_LINE_START and len(line) > 1:
                        lines.append(line[:-1])
                        line = line[-1] + char
                    else:
                        lines.append(line)
                        line = char
                else:
                    line += char
        lines.append(line)
    return lines


def _load_caption_font(
    height: int, image_font_module: Any | None = None, *, size: int | None = None
) -> Any:
    if image_font_module is None:
        from PIL import ImageFont

        image_font_module = ImageFont

    resolved_size = size if size is not None else max(14, round(height * 0.045))
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
                return image_font_module.truetype(candidate, size=resolved_size)
            except OSError:
                continue
    return image_font_module.load_default(size=resolved_size)
