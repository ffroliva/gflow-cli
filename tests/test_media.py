"""Unit tests for gflow_cli.media — PyAV last-frame extractor (Task 1, RED).

These tests are written against the Task-5 contract:

    extract_last_frame(src: Path, dst: Path, *, offset_ms: int = 0) -> Path

    - Decodes the LAST frame of ``src`` (an mp4), writes a JPEG to ``dst``,
      and returns ``dst``.
    - Raises ``FrameExtractionError`` when the ``av`` package is unavailable
      (install hint -> ``pip install 'gflow-cli[chain]'``) OR when ``src`` is
      undecodable.
    - ``offset_ms`` seeds a frame BEFORE the end (e.g. to avoid a black/fade
      final frame, scenario #11).

Until Task 5 lands ``src/gflow_cli/media.py`` this module fails at import /
collection — that is the EXPECTED red state.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from gflow_cli.media import extract_last_frame

from gflow_cli.errors import FrameExtractionError

# ``av`` is an OPTIONAL extra (``gflow-cli[chain]``). The happy-path tests that
# synthesise a real mp4 require it; skip them (rather than fail) when it is not
# installed in the current environment.
av = pytest.importorskip("av", reason="requires the optional `gflow-cli[chain]` extra (av)")


def _write_synthetic_mp4(
    dst: Path, *, frames: int = 10, width: int = 320, height: int = 240
) -> Path:
    """Encode a tiny solid-colour mp4 with PyAV so the extractor has a real,
    decodable input. The final frame is a DISTINCT colour so a test could in
    principle assert which frame was captured."""
    import numpy as np

    container = av.open(str(dst), mode="w")
    try:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        for i in range(frames):
            # ramp the green channel so the last frame differs from the first.
            shade = int(20 + (i / max(frames - 1, 1)) * 200)
            arr = np.full((height, width, 3), 0, dtype="uint8")
            arr[:, :, 1] = shade
            frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():  # flush
            container.mux(packet)
    finally:
        container.close()
    return dst


@pytest.fixture
def synthetic_mp4(tmp_path: Path) -> Path:
    pytest.importorskip("numpy")
    return _write_synthetic_mp4(tmp_path / "clip.mp4")


def _is_jpeg(path: Path) -> bool:
    """JPEG magic bytes: FF D8 ... FF D9."""
    data = path.read_bytes()
    return len(data) >= 3 and data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9"


def test_extract_last_frame_returns_valid_jpeg_path(synthetic_mp4: Path, tmp_path: Path) -> None:
    dst = tmp_path / "frame.jpg"
    result = extract_last_frame(synthetic_mp4, dst)
    assert result == dst
    assert dst.exists()
    assert _is_jpeg(dst), "extractor must write a real JPEG (FF D8 .. FF D9)"


def test_extract_last_frame_honors_offset_ms(synthetic_mp4: Path, tmp_path: Path) -> None:
    """``offset_ms`` seeds a frame BEFORE EOF; both calls still produce a JPEG.

    We assert the offset path is exercised without error and yields a valid
    JPEG — the precise pixel content is a Task-5 concern, not a Task-1 one.
    A non-zero offset must NOT raise and must still return ``dst``.
    """
    dst_eof = tmp_path / "eof.jpg"
    dst_offset = tmp_path / "offset.jpg"

    out_eof = extract_last_frame(synthetic_mp4, dst_eof, offset_ms=0)
    out_offset = extract_last_frame(synthetic_mp4, dst_offset, offset_ms=200)

    assert out_eof == dst_eof
    assert out_offset == dst_offset
    assert _is_jpeg(dst_eof)
    assert _is_jpeg(dst_offset)


def test_extract_last_frame_unicode_and_space_dst_path(synthetic_mp4: Path, tmp_path: Path) -> None:
    """Scenario #12: a Unicode + space destination path must work (Windows
    cp1252 / path-encoding traps)."""
    dst = tmp_path / "saída de vídeo 日本語.jpg"
    result = extract_last_frame(synthetic_mp4, dst)
    assert result == dst
    assert dst.exists()
    assert _is_jpeg(dst)


def test_extract_last_frame_raises_on_undecodable_input(tmp_path: Path) -> None:
    """A file that is not a valid mp4 must raise ``FrameExtractionError`` (not a
    bare PyAV/OSError) so callers get the typed exit code 20."""
    bogus = tmp_path / "not_a_video.mp4"
    bogus.write_bytes(b"this is definitely not an mp4 container")
    dst = tmp_path / "frame.jpg"
    with pytest.raises(FrameExtractionError):
        extract_last_frame(bogus, dst)


def test_extract_last_frame_raises_when_av_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the optional ``av`` package is not importable, the extractor must
    raise ``FrameExtractionError`` with an install hint — NOT a bare
    ``ModuleNotFoundError`` — so the CLI maps it to exit code 20.

    We simulate the missing extra by poisoning ``sys.modules['av']`` to None
    (which makes ``import av`` raise ImportError) and re-importing the module so
    a deferred (function-level) import re-evaluates.
    """
    # Poison the import: ``import av`` -> ImportError while this is set.
    monkeypatch.setitem(sys.modules, "av", None)

    # Re-import media so any module-level ``import av`` is re-evaluated under the
    # poisoned state. If media imports av lazily (inside the function), this is a
    # harmless no-op and the function-level guard still fires below.
    import gflow_cli.media as media_module

    media_module = importlib.reload(media_module)

    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42")  # plausible header; never decoded
    dst = tmp_path / "frame.jpg"

    with pytest.raises(FrameExtractionError) as excinfo:
        media_module.extract_last_frame(src, dst)

    # Remediation must point users at the optional extra.
    hint = excinfo.value.remediation_hint
    assert "chain" in hint.lower(), "FrameExtractionError must hint at gflow-cli[chain]"

    # Restore a clean module import for the rest of the session.
    monkeypatch.undo()
    importlib.reload(media_module)
