"""Credit-free local assembly of video and audio tracks."""

from __future__ import annotations

import errno
import os
import secrets
import stat
import sys
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import structlog

from gflow_cli.api.video import is_media_uuid
from gflow_cli.data.models import AssetKind
from gflow_cli.data.repository import verified_local_path
from gflow_cli.errors import DataStoreError, VideoAssemblyError
from gflow_cli.services.media_recovery import resolve_asset

_log = structlog.get_logger(__name__)

_SAMPLE_RATE = 44100
_CHANNELS = 2
_CHUNK = 1024
_DUCKING = "threshold=0.01:ratio=10:attack=5:release=400"
# A live reservation lasts only for one os.replace; older empty files are abandoned.
_STALE_RESERVATION_S = 60.0


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


@dataclass(frozen=True)
class _VideoLayout:
    size: tuple[int, int]
    frame_rate: float
    frame_counts: list[int]
    normalized_counts: list[int]
    total_frames: int
    total_samples: int


def assembly_result_payload(result: AssemblyResult) -> dict[str, Any]:
    """The stable JSON result shape shared by the CLI and MCP adapters."""
    return {
        "status": "succeeded",
        "output_file": str(result.output_file),
        "duration_seconds": result.duration_seconds,
        "clips_count": result.clips_count,
        "has_music": result.has_music,
        "has_voiceover": result.has_voiceover,
        "ducking_applied": result.ducking_applied,
        "video_stream": {
            "codec": "h264",
            "width": result.width,
            "height": result.height,
            "fps": result.frame_rate,
        },
        "audio_stream": {"codec": "aac", "sample_rate": _SAMPLE_RATE, "channels": _CHANNELS},
        "bytes": result.bytes,
    }


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


def _write_audio_pcm(
    path: Path, target: Any, *, samples: int, av: Any, allow_silence: bool
) -> None:
    """Decode one stream to normalized float PCM, padding or trimming to its timeline."""
    written = 0
    start = target.tell()
    with av.open(str(path)) as source:
        if not source.streams.audio:
            if not allow_silence:
                raise VideoAssemblyError(
                    detail=f"Audio input has no audio stream: {path}", route="video.assemble"
                )
        else:
            resampler = av.AudioResampler(format="flt", layout="stereo", rate=_SAMPLE_RATE)
            for decoded in source.decode(source.streams.audio[0]):
                for frame in resampler.resample(decoded):
                    count = min(frame.samples, samples - written)
                    target.write(bytes(frame.planes[0])[: count * _CHANNELS * 4])
                    written += count
                    if written >= samples:
                        break
                if written >= samples:
                    break
    target.truncate(start + samples * _CHANNELS * 4)
    target.seek(start + samples * _CHANNELS * 4)


def _pcm_spool(path: Path, *, samples: int, av: Any) -> Path:
    """Decode one required audio track to a bounded float PCM spool."""
    descriptor, name = tempfile.mkstemp(suffix=".f32le")
    os.close(descriptor)
    spool = Path(name)
    try:
        with spool.open("wb") as target:
            _write_audio_pcm(path, target, samples=samples, av=av, allow_silence=False)
        return spool
    except BaseException:
        spool.unlink(missing_ok=True)
        raise


def _make_audio(
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
                _write_audio_pcm(clip, target, samples=samples, av=av, allow_silence=True)
        if music is not None:
            spools.append(_pcm_spool(music, samples=total_samples, av=av))
        if voiceover is not None:
            spools.append(_pcm_spool(voiceover, samples=total_samples, av=av))
        return spools
    except BaseException:
        for spool in spools:
            spool.unlink(missing_ok=True)
        raise


def _validate_assembly(
    clips: list[Path],
    output: Path,
    music: Path | None = None,
    voiceover: Path | None = None,
    clip_volume: float = 1.0,
    music_volume: float = 0.3,
    voiceover_volume: float = 1.0,
) -> None:
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
    if not output.parent.is_dir():
        raise VideoAssemblyError(
            detail=f"Output directory does not exist: {output.parent}", route="video.assemble"
        )
    if any(not path.is_file() for path in sources):
        raise VideoAssemblyError(
            detail="Every input must be an existing local file.", route="video.assemble"
        )
    clear_stale_reservation(output)
    if output.exists():
        raise VideoAssemblyError(detail=f"Output already exists: {output}", route="video.assemble")


def _inspect_videos(clips: list[Path], av: Any) -> _VideoLayout:
    """Read video metadata and calculate the first clip's normalized timeline."""
    info: list[tuple[int, float]] = []
    frame_counts: list[int] = []
    first_size: tuple[int, int] | None = None
    first_rate: float | None = None
    for path in clips:
        with av.open(str(path)) as source:
            stream = next(iter(source.streams.video), None)
            if stream is None:
                raise ValueError(f"No video stream in {path}")
            rate = float(stream.average_rate or stream.base_rate or 25)
            ratio = stream.width / stream.height
            if first_size is None:
                first_size = (stream.width, stream.height)
            elif abs(ratio / (first_size[0] / first_size[1]) - 1) > 0.01:
                raise VideoAssemblyError(
                    detail=(
                        f"Clip {path} aspect ratio {ratio:.4f} differs from first clip "
                        f"ratio {first_size[0] / first_size[1]:.4f}."
                    ),
                    route="video.assemble",
                )
            first_rate = first_rate or rate
            count = sum(1 for _ in source.decode(stream))
            if not count:
                raise ValueError(f"No video frames in {path}")
            frame_counts.append(count)
            info.append((count, rate))
    assert first_size is not None and first_rate is not None
    normalized = [max(1, round(count * first_rate / rate)) for count, rate in info]
    total_frames = sum(normalized)
    return _VideoLayout(
        (first_size[0] // 2 * 2, first_size[1] // 2 * 2),
        first_rate,
        frame_counts,
        normalized,
        total_frames,
        round(total_frames / first_rate * _SAMPLE_RATE),
    )


def _encode_clip(
    target: Any,
    stream_out: Any,
    path: Path,
    *,
    size: tuple[int, int],
    source_count: int,
    target_count: int,
    frame_rate: float,
    pts: int,
    av: Any,
) -> int:
    """Encode one clip at the output geometry/rate, returning the next output PTS."""
    with av.open(str(path)) as source:
        stream = source.streams.video[0]
        frames = source.decode(stream)
        source_index = 0
        frame = next(frames, None)
        source_rate = float(stream.average_rate or frame_rate)
        frame_time_base = Fraction(1, 1) / Fraction(frame_rate).limit_denominator(1000)
        for index in range(target_count):
            wanted = min(round(index * source_rate / frame_rate), source_count - 1)
            while source_index < wanted:
                frame = next(frames, None)
                source_index += 1
            if frame is None:
                break
            normalized = frame.reformat(width=size[0], height=size[1], format="yuv420p")
            normalized.pts = pts
            normalized.time_base = frame_time_base
            pts += 1
            for packet in stream_out.encode(normalized):
                target.mux(packet)
    return pts


def _encode_video(target: Any, clips: list[Path], layout: _VideoLayout, av: Any) -> Any:
    """Add output streams and encode normalized video clips in order."""
    fps = Fraction(layout.frame_rate).limit_denominator(1000)
    video_out = target.add_stream("libx264", rate=fps)
    video_out.time_base = Fraction(1, 1) / fps
    video_out.width, video_out.height = layout.size
    video_out.pix_fmt = "yuv420p"
    audio_out = target.add_stream("aac", rate=_SAMPLE_RATE)
    audio_out.format = "fltp"
    audio_out.layout = "stereo"
    pts = 0
    for path, target_count, source_count in zip(
        clips, layout.normalized_counts, layout.frame_counts, strict=True
    ):
        pts = _encode_clip(
            target,
            video_out,
            path,
            size=layout.size,
            source_count=source_count,
            target_count=target_count,
            frame_rate=layout.frame_rate,
            pts=pts,
            av=av,
        )
    for packet in video_out.encode():
        target.mux(packet)
    return audio_out


def _split_voice(graph: Any, voice_in: Any, voiceover_volume: float) -> Any:
    voice_vol = graph.add("volume", str(voiceover_volume))
    voice_in.link_to(voice_vol)
    split = graph.add("asplit")
    voice_vol.link_to(split)
    return split


def _audio_graph(
    av: Any,
    *,
    clip_volume: float,
    music_volume: float,
    voiceover_volume: float,
    duck: bool,
    music: Path | None,
    voiceover: Path | None,
) -> tuple[Any, list[Any], Any]:
    """Build the gain, optional sidechain and limited mix for available tracks."""
    graph = av.filter.Graph()
    clip_in = graph.add_abuffer(
        sample_rate=_SAMPLE_RATE, format="flt", layout="stereo", time_base="1/44100"
    )
    inputs = [clip_in]
    clip_vol = graph.add("volume", str(clip_volume))
    clip_in.link_to(clip_vol)
    tracks = [(clip_vol, 0)]
    clip_voice = None
    if music is not None:
        music_in = graph.add_abuffer(
            sample_rate=_SAMPLE_RATE, format="flt", layout="stereo", time_base="1/44100"
        )
        inputs.append(music_in)
        music_vol = graph.add("volume", str(music_volume))
        music_in.link_to(music_vol)
        if duck and voiceover is not None:
            voice_in = graph.add_abuffer(
                sample_rate=_SAMPLE_RATE, format="flt", layout="stereo", time_base="1/44100"
            )
            inputs.append(voice_in)
            split = _split_voice(graph, voice_in, voiceover_volume)
            compressor = graph.add("sidechaincompress", _DUCKING)
            music_vol.link_to(compressor, 0, 0)
            split.link_to(compressor, 0, 1)
            tracks.extend([(compressor, 0), (split, 1)])
        else:
            tracks.append((music_vol, 0))
    if voiceover is not None and not duck:
        voice_in = graph.add_abuffer(
            sample_rate=_SAMPLE_RATE, format="flt", layout="stereo", time_base="1/44100"
        )
        inputs.append(voice_in)
        voice_vol = graph.add("volume", str(voiceover_volume))
        voice_in.link_to(voice_vol)
        tracks.append((voice_vol, 0))
    if duck and voiceover is not None:
        clip_voice = graph.add_abuffer(
            sample_rate=_SAMPLE_RATE, format="flt", layout="stereo", time_base="1/44100"
        )
        inputs.append(clip_voice)
        if music is None:
            split = _split_voice(graph, clip_voice, voiceover_volume)
            tracks.append((split, 1))
            key = split
        else:
            key = clip_voice
        compressor = graph.add("sidechaincompress", _DUCKING)
        clip_vol.link_to(compressor, 0, 0)
        key.link_to(compressor, 0, 1)
        tracks[0] = (compressor, 0)
    mixer = graph.add(
        "amix", f"inputs={len(tracks)}:duration=first:dropout_transition=0:normalize=0"
    )
    for index, (track, output_pad) in enumerate(tracks):
        track.link_to(mixer, output_pad, index)
    limiter = graph.add("alimiter", "limit=1:level=disabled")
    sink = graph.add("abuffersink")
    mixer.link_to(limiter)
    limiter.link_to(sink)
    graph.configure()
    return graph, inputs, sink


def _drain_audio(target: Any, stream: Any, sink: Any, av: Any) -> None:
    """Encode every currently available filtered audio frame."""
    while True:
        try:
            for packet in stream.encode(sink.pull()):
                target.mux(packet)
        except (av.error.BlockingIOError, av.error.EOFError):
            return


def _push_audio_chunk(
    inputs: list[Any],
    files: list[Any],
    *,
    offset: int,
    count: int,
    av: Any,
) -> None:
    """Read one bounded PCM chunk from each spool and push synchronized frames."""
    for input_filter, file in zip(inputs, files, strict=True):
        raw = file.read(count * _CHANNELS * 4)
        raw += bytes(max(0, _CHUNK - len(raw) // (_CHANNELS * 4)) * _CHANNELS * 4)
        frame = av.AudioFrame(format="flt", layout="stereo", samples=_CHUNK)
        frame.planes[0].update(raw)
        frame.sample_rate = _SAMPLE_RATE
        frame.pts = offset
        input_filter.push(frame)


def _encode_audio(
    target: Any,
    stream: Any,
    inputs: list[Any],
    sink: Any,
    spools: list[Path],
    *,
    total_samples: int,
    av: Any,
) -> None:
    """Mix and encode PCM spools in fixed-size chunks without buffering a whole track."""
    files = [spool.open("rb") for spool in spools]
    files.extend(spools[-1].open("rb") for _ in range(len(inputs) - len(files)))
    try:
        for offset in range(0, total_samples, _CHUNK):
            _push_audio_chunk(
                inputs,
                files,
                offset=offset,
                count=min(_CHUNK, total_samples - offset),
                av=av,
            )
            _drain_audio(target, stream, sink, av)
        for input_filter in inputs:
            input_filter.push(None)
        _drain_audio(target, stream, sink, av)
    finally:
        for file in files:
            file.close()
    for packet in stream.encode():
        target.mux(packet)


def _write_output(
    clips: list[Path],
    output: Path,
    layout: _VideoLayout,
    spools: list[Path],
    *,
    clip_volume: float,
    music_volume: float,
    voiceover_volume: float,
    duck: bool,
    music: Path | None,
    voiceover: Path | None,
    av: Any,
) -> None:
    """Create the MP4 container and write normalized video plus the mixed audio bed."""
    with av.open(str(output), "w", format="mp4") as target:
        audio_out = _encode_video(target, clips, layout, av)
        _graph, inputs, sink = _audio_graph(
            av,
            clip_volume=clip_volume,
            music_volume=music_volume,
            voiceover_volume=voiceover_volume,
            duck=duck,
            music=music,
            voiceover=voiceover,
        )
        _encode_audio(
            target,
            audio_out,
            inputs,
            sink,
            spools,
            total_samples=layout.total_samples,
            av=av,
        )


def _probe_directory_mtime(parent: Path) -> float:
    """Create a temporary probe file to measure directory write time."""
    try:
        descriptor, probe_name = tempfile.mkstemp(prefix=".gflow-probe-", dir=parent)
        try:
            return os.stat(descriptor).st_mtime
        finally:
            os.close(descriptor)
            try:
                os.unlink(probe_name)
            except OSError:
                pass
    except OSError as exc:
        raise VideoAssemblyError(
            detail=f"Could not probe output directory {parent}: {exc}",
            route="video.assemble",
            charged="no",
        ) from exc


_FALLBACK_ERRNOS = {
    errno.ENOSYS,
    errno.EINVAL,
    errno.ENOTSUP,
    errno.EOPNOTSUPP,
    errno.EPERM,
    errno.EXDEV,
}
_CLEAR_FALLBACK_ERRNOS = _FALLBACK_ERRNOS - {errno.EPERM}


def _matches_pre_rename_reservation(output: Path, st: os.stat_result) -> bool:
    try:
        pre_st = os.lstat(output)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise VideoAssemblyError(
            detail=f"Could not remove stale output reservation: {output} ({exc})",
            route="video.assemble",
            charged="no",
        ) from exc

    if not (
        stat.S_ISREG(pre_st.st_mode)
        and pre_st.st_size == 0
        and (pre_st.st_dev, pre_st.st_ino) == (st.st_dev, st.st_ino)
        and pre_st.st_mtime == st.st_mtime
    ):
        raise VideoAssemblyError(
            detail=(
                f"Output already exists: {output} (the output is an empty leftover from an "
                "interrupted run, this filesystem cannot clear it safely, delete it manually)"
            ),
            route="video.assemble",
            charged="no",
        )
    return True


def _restore_stale_candidate(stale_candidate: Path, output: Path) -> None:
    try:
        _platform_no_replace_rename(stale_candidate, output)
    except OSError as restore_exc:
        raise VideoAssemblyError(
            detail=(
                f"Could not restore output {output} from temporary reservation "
                f"{stale_candidate}: {restore_exc}"
            ),
            route="video.assemble",
            charged="no",
            remediation_hint=(
                f"Check or restore the temporary reservation file manually: {stale_candidate}"
            ),
        ) from restore_exc


def _rename_dance_clear(output: Path, st: os.stat_result) -> None:
    if not _matches_pre_rename_reservation(output, st):
        return

    stale_candidate = output.with_name(f".gflow-stale-{secrets.token_hex(8)}")
    try:
        _platform_no_replace_rename(output, stale_candidate)
    except FileNotFoundError:
        return
    except (OSError, ImportError) as exc:
        if (
            isinstance(exc, ImportError)
            or (exc.errno is not None and exc.errno in _CLEAR_FALLBACK_ERRNOS)
            or exc.errno is None
        ):
            raise VideoAssemblyError(
                detail=(
                    f"Output already exists: {output} (the output is an empty leftover from an "
                    "interrupted run, this filesystem cannot clear it safely, delete it manually)"
                ),
                route="video.assemble",
                charged="no",
            ) from exc
        raise VideoAssemblyError(
            detail=f"Could not remove stale output reservation: {output} ({exc})",
            route="video.assemble",
            charged="no",
        ) from exc

    try:
        renamed_st = os.lstat(stale_candidate)
        is_reservation = (
            stat.S_ISREG(renamed_st.st_mode)
            and renamed_st.st_size == 0
            and (
                (renamed_st.st_dev, renamed_st.st_ino) == (st.st_dev, st.st_ino)
                or renamed_st.st_mtime == st.st_mtime
            )
        )
        if is_reservation:
            try:
                stale_candidate.unlink()
            except OSError as unlink_exc:
                _log.warning(
                    "stale_reservation_unlink_failed",
                    path=str(stale_candidate),
                    error=str(unlink_exc),
                )
            _log.info("cleared_stale_reservation", path=str(output))
        else:
            _restore_stale_candidate(stale_candidate, output)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise VideoAssemblyError(
            detail=f"Could not remove stale output reservation: {output} ({exc})",
            route="video.assemble",
            charged="no",
        ) from exc


def clear_stale_reservation(output: Path) -> None:
    """Clear an abandoned zero-byte reservation left by an interrupted publish."""
    try:
        st = os.lstat(output)
    except OSError:
        return
    if not stat.S_ISREG(st.st_mode) or st.st_size != 0:
        return

    probe_mtime = _probe_directory_mtime(output.parent)
    if (probe_mtime - st.st_mtime) < _STALE_RESERVATION_S:
        raise VideoAssemblyError(
            detail=(
                f"Output already exists: {output} (it is empty and may be another run's "
                "in-progress publish or an interrupted one; retry after 60 s or delete it)"
            ),
            route="video.assemble",
            charged="no",
        )

    _rename_dance_clear(output, st)


def _linux_no_replace_rename(temp_output: Path, output: Path) -> None:
    import ctypes

    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError as exc:
        raise OSError(errno.ENOSYS, f"Could not load libc: {exc}") from exc
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "renameat2 not found in libc")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    ret = renameat2(
        ctypes.c_int(-100),  # AT_FDCWD
        os.fsencode(temp_output),
        ctypes.c_int(-100),  # AT_FDCWD
        os.fsencode(output),
        ctypes.c_uint(1),  # RENAME_NOREPLACE
    )
    if ret != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


def _darwin_no_replace_rename(temp_output: Path, output: Path) -> None:
    import ctypes

    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError as exc:
        raise OSError(errno.ENOSYS, f"Could not load libc: {exc}") from exc
    renamex_np = getattr(libc, "renamex_np", None)
    if renamex_np is None:
        raise OSError(errno.ENOSYS, "renamex_np not found in libc")
    renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    renamex_np.restype = ctypes.c_int
    ret = renamex_np(
        os.fsencode(temp_output),
        os.fsencode(output),
        ctypes.c_uint(4),  # RENAME_EXCL
    )
    if ret != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


def _platform_no_replace_rename(temp_output: Path, output: Path) -> None:
    """Atomically rename temp_output to output without replacing an existing destination."""
    if sys.platform == "win32":
        os.rename(temp_output, output)
    elif sys.platform.startswith("linux"):
        _linux_no_replace_rename(temp_output, output)
    elif sys.platform == "darwin":
        _darwin_no_replace_rename(temp_output, output)
    else:
        raise OSError(errno.ENOSYS, f"No platform atomic no-replace rename for {sys.platform}")


def _reserved_replace_publish(temp_output: Path, output: Path) -> None:
    """Publish via exclusive reservation and replace as a last resort on unassisted filesystems.

    This path is best-effort: it assumes the reserving run is not suspended for longer
    than the 60 s stale threshold.
    """
    try:
        descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
    except FileExistsError as exc:
        raise VideoAssemblyError(
            detail=f"Output already exists: {output}", route="video.assemble"
        ) from exc
    try:
        st = os.fstat(descriptor)
        target_id = (st.st_dev, st.st_ino)
    finally:
        os.close(descriptor)

    published = False
    try:
        try:
            current_st = os.stat(output)
        except OSError as exc:
            raise VideoAssemblyError(
                detail=f"Output already exists: {output}", route="video.assemble"
            ) from exc
        if (current_st.st_dev, current_st.st_ino) != target_id or current_st.st_size != 0:
            raise VideoAssemblyError(
                detail=f"Output already exists: {output}", route="video.assemble"
            )
        os.replace(temp_output, output)
        published = True
    except BaseException:
        if not published:
            try:
                cur = os.stat(output)
                if (
                    (cur.st_dev, cur.st_ino) == target_id
                    and stat.S_ISREG(cur.st_mode)
                    and cur.st_size == 0
                ):
                    output.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _publish_fallback(temp_output: Path, output: Path) -> None:
    """Publish when hard links are unsupported, using platform rename or reservation."""
    try:
        _platform_no_replace_rename(temp_output, output)
    except FileExistsError as exc:
        raise VideoAssemblyError(
            detail=f"Output already exists: {output}", route="video.assemble"
        ) from exc
    except OSError as exc:
        if exc.errno is None or exc.errno in _FALLBACK_ERRNOS:
            _reserved_replace_publish(temp_output, output)
        else:
            raise
    except ImportError:
        _reserved_replace_publish(temp_output, output)


def publish_output(temp_output: Path, output: Path) -> None:
    """Atomically publish the completed file without replacing another writer's output."""
    try:
        os.link(temp_output, output)
    except FileExistsError as exc:
        raise VideoAssemblyError(
            detail=f"Output already exists: {output}", route="video.assemble"
        ) from exc
    except OSError:
        _publish_fallback(temp_output, output)
    else:
        try:
            temp_output.unlink()
        except OSError:
            pass


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
        raise VideoAssemblyError(
            detail="Video assembly requires PyAV. Install with `pip install gflow-cli[chain]`.",
            route="video.assemble",
            charged="no",
        ) from exc
    _av: Any = av
    _validate_assembly(clips, output, music, voiceover, clip_volume, music_volume, voiceover_volume)
    audio_spools: list[Path] = []
    temp_output: Path | None = None
    try:
        layout = _inspect_videos(clips, _av)
        audio_spools = _make_audio(
            [
                (clip, round(normalized * _SAMPLE_RATE / layout.frame_rate))
                for clip, normalized in zip(clips, layout.normalized_counts, strict=True)
            ],
            music,
            voiceover,
            layout.total_samples,
            _av,
        )
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{output.stem}.", suffix=output.suffix, dir=output.parent
        )
        os.close(descriptor)
        temp_output = Path(temp_name)
        _write_output(
            clips,
            temp_output,
            layout,
            audio_spools,
            clip_volume=clip_volume,
            music_volume=music_volume,
            voiceover_volume=voiceover_volume,
            duck=duck,
            music=music,
            voiceover=voiceover,
            av=_av,
        )
        publish_output(temp_output, output)
        return AssemblyResult(
            output_file=output,
            duration_seconds=layout.total_frames / layout.frame_rate,
            clips_count=len(clips),
            has_music=music is not None,
            has_voiceover=voiceover is not None,
            ducking_applied=duck and voiceover is not None,
            width=layout.size[0],
            height=layout.size[1],
            frame_rate=layout.frame_rate,
            bytes=output.stat().st_size,
        )
    except VideoAssemblyError:
        raise
    except (_av.error.FFmpegError, OSError, ValueError) as exc:
        raise VideoAssemblyError(detail=str(exc), route="video.assemble") from exc
    finally:
        if temp_output is not None:
            try:
                temp_output.unlink(missing_ok=True)
            except OSError as exc:
                _log.warning(
                    "could_not_remove_temp_output",
                    path=str(temp_output),
                    error=str(exc),
                )
        for spool in audio_spools:
            try:
                spool.unlink(missing_ok=True)
            except OSError:
                pass
