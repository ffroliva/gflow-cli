"""Credit-free local assembly of video and audio tracks."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from gflow_cli.api.video import is_media_uuid
from gflow_cli.data.models import AssetKind
from gflow_cli.data.repository import verified_local_path
from gflow_cli.errors import DataStoreError, FrameExtractionError, VideoAssemblyError
from gflow_cli.services.media_recovery import resolve_asset

_SAMPLE_RATE = 44100
_CHANNELS = 2
_CHUNK = 1024


@dataclass(frozen=True)
class AssemblyResult:
    output_file: Path
    duration_seconds: float
    clips_count: int
    has_music: bool
    has_voiceover: bool
    ducking_applied: bool
    width: int
    height: int
    frame_rate: float
    bytes: int


def resolve_assembly_input(value: str, *, profile: str | None = None) -> Path:
    """Resolve a local file or a catalog video UUID with an integrity-verified copy."""
    path = Path(value).expanduser()
    if path.is_file():
        return path.resolve()
    if not is_media_uuid(value):
        raise VideoAssemblyError(
            detail=f"Input file does not exist: {path}", route="video.assemble"
        )
    asset = resolve_asset(value, profile=profile, route="video.assemble")
    if asset.kind is not AssetKind.VIDEO:
        raise DataStoreError(
            detail=f"Catalog media {value!r} is not a video.", route="video.assemble"
        )
    for local_file in asset.local_files:
        if verified := verified_local_path(local_file):
            return verified.resolve()
    raise DataStoreError(
        detail=f"Catalog video {value!r} has no verified local file.",
        route="video.assemble",
    )


def _pcm_spool(path: Path, *, samples: int, av: Any, audio_stream: Any | None = None) -> Path:
    """Decode one audio stream to bounded, normalized float PCM on disk."""
    descriptor, name = tempfile.mkstemp(suffix=".f32le")
    os.close(descriptor)
    spool = Path(name)
    try:
        with spool.open("wb") as target:
            if audio_stream is None:
                target.truncate(samples * _CHANNELS * 4)
            else:
                resampler = av.AudioResampler(format="fltp", layout="stereo", rate=_SAMPLE_RATE)
                written = 0
                with av.open(str(path)) as source:
                    stream = source.streams.audio[0]
                    for decoded in source.decode(stream):
                        for frame in resampler.resample(decoded):
                            pcm = frame.to_ndarray().astype(np.float32, copy=False)
                            pcm = pcm[:, : samples - written]
                            target.write(pcm.T.tobytes())
                            written += pcm.shape[1]
                            if written >= samples:
                                break
                        if written >= samples:
                            break
                if written < samples:
                    target.truncate(samples * _CHANNELS * 4)
        return spool
    except Exception:
        spool.unlink(missing_ok=True)
        raise


def _make_audio(
    path: Path,
    clips: list[tuple[Path, int]],
    music: Path | None,
    voiceover: Path | None,
    total_samples: int,
    av: Any,
) -> list[Path]:
    """Write concatenated clip audio plus optional tracks to bounded disk spools."""
    descriptor, name = tempfile.mkstemp(suffix=".f32le")
    os.close(descriptor)
    clip_spool = Path(name)
    spools = [clip_spool]
    try:
        with clip_spool.open("wb") as target:
            for clip, samples in clips:
                with av.open(str(clip)) as source:
                    stream = source.streams.audio[0] if source.streams.audio else None
                part = _pcm_spool(clip, samples=samples, av=av, audio_stream=stream)
                try:
                    with part.open("rb") as audio:
                        shutil.copyfileobj(audio, target)
                finally:
                    part.unlink(missing_ok=True)
        if music is not None:
            spools.append(_pcm_spool(music, samples=total_samples, av=av, audio_stream=True))
        else:
            descriptor, name = tempfile.mkstemp(suffix=".f32le")
            os.close(descriptor)
            silence = Path(name)
            with silence.open("wb") as target:
                target.truncate(total_samples * _CHANNELS * 4)
            spools.append(silence)
        if voiceover is not None:
            spools.append(_pcm_spool(voiceover, samples=total_samples, av=av, audio_stream=True))
        else:
            descriptor, name = tempfile.mkstemp(suffix=".f32le")
            os.close(descriptor)
            silence = Path(name)
            with silence.open("wb") as target:
                target.truncate(total_samples * _CHANNELS * 4)
            spools.append(silence)
        return spools
    except Exception:
        for spool in spools:
            spool.unlink(missing_ok=True)
        raise


def assemble_videos(
    clips: list[Path],
    output: Path,
    *,
    music: Path | None = None,
    voiceover: Path | None = None,
    clip_volume: float = 1.0,
    music_volume: float = 0.3,
    voiceover_volume: float = 1.0,
    duck: bool = True,
) -> AssemblyResult:
    """Join clips, normalize to the first clip, and mix optional audio locally."""
    try:
        import av
    except ImportError as exc:
        raise FrameExtractionError(detail=str(exc), route="video.assemble") from exc

    if not clips:
        raise VideoAssemblyError(detail="At least one clip is required.", route="video.assemble")
    if any(not 0 <= value <= 1 for value in (clip_volume, music_volume, voiceover_volume)):
        raise VideoAssemblyError(
            detail="Track volumes must be between 0 and 1.", route="video.assemble"
        )
    sources = [
        path.resolve()
        for path in [*clips, *([music] if music else []), *([voiceover] if voiceover else [])]
    ]
    destination = output.resolve()
    if destination in sources:
        raise VideoAssemblyError(
            detail="Output path must differ from every input.", route="video.assemble"
        )
    if output.exists():
        raise VideoAssemblyError(detail=f"Output already exists: {output}", route="video.assemble")
    if not output.parent.is_dir():
        raise VideoAssemblyError(
            detail=f"Output directory does not exist: {output.parent}", route="video.assemble"
        )
    if any(not path.is_file() for path in sources):
        raise VideoAssemblyError(
            detail="Every input must be an existing local file.", route="video.assemble"
        )

    spools: list[Path] = []
    try:
        video_info: list[tuple[int, float]] = []
        first_size: tuple[int, int] | None = None
        first_rate: float | None = None
        frame_counts: list[int] = []
        for path in clips:
            with av.open(str(path)) as source:
                stream = next(iter(source.streams.video), None)
                if stream is None:
                    raise ValueError(f"No video stream in {path}")
                rate = float(stream.average_rate or stream.base_rate or 25)
                first_size = first_size or (stream.width, stream.height)
                first_rate = first_rate or rate
                count = 0
                for _ in source.decode(stream):
                    count += 1
                if count == 0:
                    raise ValueError(f"No video frames in {path}")
                frame_counts.append(count)
                video_info.append((count, rate))

        assert first_size is not None and first_rate is not None
        normalized_counts = [round(count * first_rate / rate) for count, rate in video_info]
        total_frames = sum(normalized_counts)
        total_samples = round(total_frames / first_rate * _SAMPLE_RATE)
        audio_spools = _make_audio(
            output,
            [
                (clip, round(normalized * _SAMPLE_RATE / first_rate))
                for clip, normalized in zip(clips, normalized_counts, strict=True)
            ],
            music,
            voiceover,
            total_samples,
            av,
        )
        spools.extend(audio_spools)

        with av.open(str(output), "w", format="mp4") as target:
            video_out = target.add_stream(
                "libx264", rate=Fraction(first_rate).limit_denominator(1000)
            )
            video_out.time_base = Fraction(1, 1) / Fraction(first_rate).limit_denominator(1000)
            video_out.width, video_out.height = first_size
            video_out.pix_fmt = "yuv420p"
            audio_out = target.add_stream("aac", rate=_SAMPLE_RATE)
            audio_out.format = "fltp"
            audio_out.layout = "stereo"

            pts = 0
            for path, target_count, source_count in zip(
                clips, normalized_counts, frame_counts, strict=True
            ):
                with av.open(str(path)) as source:
                    stream = source.streams.video[0]
                    frames = source.decode(stream)
                    source_index = 0
                    frame = next(frames, None)
                    for index in range(target_count):
                        wanted_index = min(
                            round(index * float(stream.average_rate or first_rate) / first_rate),
                            source_count - 1,
                        )
                        while source_index < wanted_index:
                            frame = next(frames, None)
                            source_index += 1
                        if frame is None:
                            break
                        normalized = frame.reformat(
                            width=first_size[0], height=first_size[1], format="yuv420p"
                        )
                        normalized.pts = pts
                        normalized.time_base = video_out.time_base
                        pts += 1
                        for packet in video_out.encode(normalized):
                            target.mux(packet)
            for packet in video_out.encode():
                target.mux(packet)

            graph = av.filter.Graph()
            clip_in = graph.add_abuffer(
                sample_rate=_SAMPLE_RATE, format="fltp", layout="stereo", time_base="1/44100"
            )
            music_in = graph.add_abuffer(
                sample_rate=_SAMPLE_RATE, format="fltp", layout="stereo", time_base="1/44100"
            )
            voice_in = graph.add_abuffer(
                sample_rate=_SAMPLE_RATE, format="fltp", layout="stereo", time_base="1/44100"
            )
            clip_vol = graph.add("volume", str(clip_volume))
            music_vol = graph.add("volume", str(music_volume))
            voice_vol = graph.add("volume", str(voiceover_volume))
            if duck and voiceover is not None and music is not None:
                split = graph.add("asplit")
                compressor = graph.add(
                    "sidechaincompress", "threshold=0.05:ratio=4:attack=20:release=300"
                )
                voice_vol.link_to(split)
                music_vol.link_to(compressor, 0, 0)
                split.link_to(compressor, 0, 1)
                music_input = compressor
                voice_input = split
                voice_pad = 1
            else:
                music_input = music_vol
                voice_input = voice_vol
                voice_pad = 0
            mixer = graph.add("amix", "inputs=3:duration=first:dropout_transition=0")
            sink = graph.add("abuffersink")
            clip_in.link_to(clip_vol)
            music_in.link_to(music_vol)
            voice_in.link_to(voice_vol)
            clip_vol.link_to(mixer, 0, 0)
            music_input.link_to(mixer, 0, 1)
            voice_input.link_to(mixer, voice_pad, 2)
            mixer.link_to(sink)
            graph.configure()

            files = [spool.open("rb") for spool in audio_spools]
            try:
                for offset in range(0, total_samples, _CHUNK):
                    count = min(_CHUNK, total_samples - offset)
                    for input_filter, file in zip(
                        (clip_in, music_in, voice_in), files, strict=True
                    ):
                        raw = file.read(count * _CHANNELS * 4)
                        samples = (
                            np.frombuffer(raw, dtype=np.float32).reshape(-1, _CHANNELS).T.copy()
                        )
                        if samples.shape[1] < _CHUNK:
                            samples = np.pad(samples, ((0, 0), (0, _CHUNK - samples.shape[1])))
                        audio = av.AudioFrame.from_ndarray(samples, format="fltp", layout="stereo")
                        audio.sample_rate = _SAMPLE_RATE
                        audio.pts = offset
                        input_filter.push(audio)
                    while True:
                        try:
                            for packet in audio_out.encode(sink.pull()):
                                target.mux(packet)
                        except (av.error.BlockingIOError, av.error.EOFError):
                            break
                for input_filter in (clip_in, music_in, voice_in):
                    input_filter.push(None)
                while True:
                    try:
                        for packet in audio_out.encode(sink.pull()):
                            target.mux(packet)
                    except (av.error.BlockingIOError, av.error.EOFError):
                        break
            finally:
                for file in files:
                    file.close()
            for packet in audio_out.encode():
                target.mux(packet)

        return AssemblyResult(
            output_file=output,
            duration_seconds=total_frames / first_rate,
            clips_count=len(clips),
            has_music=music is not None,
            has_voiceover=voiceover is not None,
            ducking_applied=duck and music is not None and voiceover is not None,
            width=first_size[0],
            height=first_size[1],
            frame_rate=first_rate,
            bytes=output.stat().st_size,
        )
    except VideoAssemblyError:
        output.unlink(missing_ok=True)
        raise
    except Exception as exc:
        output.unlink(missing_ok=True)
        raise VideoAssemblyError(detail=str(exc), route="video.assemble") from exc
    finally:
        for spool in spools:
            spool.unlink(missing_ok=True)
