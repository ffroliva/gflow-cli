"""Unit tests for gflow_cli.media — PyAV last-frame extractor (Task 1, RED).

These tests are written against the Task-5 contract:

    extract_last_frame(src: Path, dst: Path, *, offset_ms: int = 0) -> Path

    - Decodes the LAST frame of ``src`` (an mp4), writes a JPEG to ``dst``,
      and returns ``dst``.
    - Raises ``FrameExtractionError`` when ``src`` is undecodable.
    - A missing ``gflow-cli[chain]`` extra is NOT a call-time failure (#813): both
      ``av`` and Pillow are module-level imports, so it fails at ``import
      gflow_cli.media`` and the CLI guard turns that into exit 20 up front.
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

from gflow_cli.errors import FrameExtractionError
from gflow_cli.media import extract_last_frame

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


def test_media_import_fails_when_av_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing ``av`` must fail at ``import gflow_cli.media``, not at call time.

    REPLACES ``test_extract_last_frame_raises_when_av_unavailable``, whose premise
    #813 invalidated. That test asserted ``extract_last_frame`` converted a missing
    ``av`` into ``FrameExtractionError`` at CALL time, and it passed — but the
    behaviour it blessed was the bug: ``_decode_frame`` runs only BETWEEN links, so
    a user without the extra paid for link 0 before being told the extra was
    missing, while a missing Pillow (module-level, same extra) never reached that
    guard at all and crashed with a generic exit 1. ``av`` now sits beside ``PIL``
    at module level, so both fail at one point and the single import guard in
    ``cli_video._run_chain`` maps either to exit 20 before anything is submitted —
    which is what ``tests/cli/test_cli_video_chain.py`` now pins.

    The old assertion is therefore not merely relocated: it asserted a call-time
    failure that must no longer be reachable.
    """
    import gflow_cli

    # `import av` -> ImportError while sys.modules["av"] is None. media must be
    # evicted from BOTH sys.modules and the package namespace, or the import is a
    # cache hit that never re-executes. monkeypatch restores both at teardown.
    monkeypatch.setitem(sys.modules, "av", None)
    monkeypatch.delitem(sys.modules, "gflow_cli.media", raising=False)
    monkeypatch.delattr(gflow_cli, "media", raising=False)

    with pytest.raises(ImportError):
        importlib.import_module("gflow_cli.media")
