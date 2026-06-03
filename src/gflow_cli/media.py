"""Video media helpers for the video-chain feature.

Currently exposes :func:`extract_last_frame`, which decodes the final frame of
an mp4 and writes it as a JPEG. It is the seed image for the next link in a
sequential video chain (the Task-7 orchestrator calls it between links, wrapped
in ``asyncio.to_thread`` since decoding is blocking).

PyAV (``av``) is an OPTIONAL dependency shipped via the ``gflow-cli[chain]``
extra — it bundles ffmpeg, so no system ffmpeg is required. The import is
deferred to call time and guarded so a missing extra surfaces as a typed
:class:`~gflow_cli.errors.FrameExtractionError` (exit code 20) with an install
hint, never a bare ``ModuleNotFoundError``.

PyAV ships no type stubs, so its surface is confined to :func:`_decode_frame`
where ``av`` is imported as an explicitly ``Any``-typed module. The frame is
converted to a typed ``PIL.Image.Image`` at that boundary, keeping the rest of
the module strictly typed.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Any

from PIL import Image

from gflow_cli.errors import FrameExtractionError


def extract_last_frame(src: Path, dst: Path, *, offset_ms: int = 0) -> Path:
    """Decode the last frame of ``src`` (an mp4) and write it as a JPEG to ``dst``.

    Args:
        src: Path to a decodable mp4 video.
        dst: Destination path for the JPEG. Unicode and spaces are supported.
        offset_ms: When > 0, select the frame roughly ``offset_ms`` milliseconds
            BEFORE the end of the clip instead of the very last frame. This is the
            black/fade final-frame mitigation: many generated clips end on a fade
            to black, which makes a poor seed image for the next link.

    Returns:
        ``dst`` (the path that was written).

    Raises:
        FrameExtractionError: If the ``av`` package is unavailable (the
            ``gflow-cli[chain]`` extra was not installed) or ``src`` is
            undecodable / contains no video frames.
    """
    try:
        image = _decode_frame(src, offset_ms=offset_ms)
    except FrameExtractionError:
        raise
    except ImportError as exc:  # missing optional [chain] extra
        raise FrameExtractionError(
            detail="the optional `av` package (PyAV) is not installed",
        ) from exc
    except Exception as exc:  # undecodable / corrupt / non-mp4 input
        raise FrameExtractionError(
            detail=f"could not decode {src.name!r}: {exc}",
        ) from exc

    dst.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(dst), format="JPEG", quality=95)
    return dst


def _decode_frame(src: Path, *, offset_ms: int) -> Image.Image:
    """Open ``src``, seek near the end, and return the target frame as a PIL image.

    For ``offset_ms == 0`` this is the last decoded frame. For a positive
    ``offset_ms`` it is the decoded frame whose presentation timestamp is closest
    to ``end - offset_ms``. Decoding happens over a short window near EOF, so we
    never walk the whole stream.

    ``av`` is untyped (no stubs); it is imported as an ``Any`` module here so the
    untyped surface stays confined to this function. The returned PIL image is
    fully typed for callers.
    """
    import av  # type: ignore[import]  # optional [chain] extra, ships no stubs

    av_mod: Any = av  # confine the untyped PyAV surface to this local

    with av_mod.open(str(src)) as container:
        if not container.streams.video:
            raise FrameExtractionError(detail=f"{src.name!r} has no video stream")
        stream = container.streams.video[0]
        time_base = stream.time_base
        duration = stream.duration

        # Seek a window before EOF so we decode only the tail of the stream.
        # The window must cover offset_ms plus a margin back to a keyframe.
        if duration is not None and time_base:
            one_second = int(Fraction(1, 1) / time_base)
            offset_ticks = int(Fraction(max(offset_ms, 0), 1000) / time_base)
            back = max(0, int(duration) - one_second - offset_ticks)
            container.seek(back, stream=stream, any_frame=False)

        # Target presentation timestamp (in stream ticks) for a positive offset.
        target_pts: int | None = None
        if offset_ms > 0 and duration is not None and time_base:
            target_ticks = int(Fraction(offset_ms, 1000) / time_base)
            target_pts = max(0, int(duration) - target_ticks)

        best: Image.Image | None = None
        best_delta: int | None = None
        for frame in container.decode(stream):
            pts: int | None = frame.pts
            if target_pts is None or pts is None:
                # offset_ms == 0 (or no timestamp to compare): the latest frame
                # wins, so we never return None when frames exist.
                best = frame.to_image()
                continue
            delta = abs(int(pts) - target_pts)
            if best_delta is None or delta < best_delta:
                best, best_delta = frame.to_image(), delta

        if best is None:
            raise FrameExtractionError(detail=f"{src.name!r} contained no decodable frames")
        return best
