"""Platform-specific rename and edge error branch tests for video assembly."""

from __future__ import annotations

import ctypes
import errno
import os
import re
import sys
import time
from pathlib import Path

import av
import numpy as np
import pytest

from gflow_cli.errors import VideoAssemblyError
from gflow_cli.services import video_assembly
from gflow_cli.services.video_assembly import assemble_videos


def _make_dummy_clip(
    path: Path,
    *,
    size: tuple[int, int] = (64, 48),
    rate: int = 10,
    frames: int = 2,
    audio: bool = True,
) -> None:
    with av.open(str(path), "w", format="mp4") as container:
        stream = container.add_stream("mpeg4", rate=rate)
        stream.width, stream.height = size
        stream.pix_fmt = "yuv420p"
        sound = container.add_stream("aac", rate=44100) if audio else None
        if sound is not None:
            sound.layout = "stereo"
            sound.format = "fltp"
        for index in range(frames):
            pixels = np.zeros((size[1], size[0], 3), dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts = index
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
        if sound is not None:
            samples = np.zeros((2, 1024), dtype=np.float32)
            sample = av.AudioFrame.from_ndarray(samples, format="fltp", layout="stereo")
            sample.sample_rate = 44100
            sample.pts = 0
            for packet in sound.encode(sample):
                container.mux(packet)
            for packet in sound.encode():
                container.mux(packet)


def test_darwin_no_replace_rename_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[bytes, bytes, int]] = []

    class FakeRenamex:
        argtypes: list[object] | None = None
        restype: object = None

        def __call__(self, src: bytes, dst: bytes, flags: ctypes.c_uint) -> int:
            flags_val = flags.value if hasattr(flags, "value") else int(flags)
            calls.append((src, dst, flags_val))
            return 0

    class FakeLibc:
        renamex_np = FakeRenamex()

    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: FakeLibc())
    src = tmp_path / "temp.mp4"
    dst = tmp_path / "dest.mp4"
    video_assembly._darwin_no_replace_rename(src, dst)

    assert len(calls) == 1
    assert calls[0][0] == os.fsencode(src)
    assert calls[0][1] == os.fsencode(dst)
    assert calls[0][2] == 4  # RENAME_EXCL


def test_darwin_no_replace_rename_exists_raises_file_exists_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeRenamex:
        argtypes: list[object] | None = None
        restype: object = None

        def __call__(self, _src: bytes, _dst: bytes, _flags: ctypes.c_uint) -> int:
            return -1

    class FakeLibc:
        renamex_np = FakeRenamex()

    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: FakeLibc())
    monkeypatch.setattr(ctypes, "get_errno", lambda: errno.EEXIST)
    src = tmp_path / "temp.mp4"
    dst = tmp_path / "dest.mp4"

    with pytest.raises(FileExistsError) as exc_info:
        video_assembly._darwin_no_replace_rename(src, dst)
    assert exc_info.value.errno == errno.EEXIST


def test_darwin_no_replace_rename_missing_symbol_raises_enosys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeLibcWithoutRenamex:
        pass

    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: FakeLibcWithoutRenamex())
    src = tmp_path / "temp.mp4"
    dst = tmp_path / "dest.mp4"

    with pytest.raises(OSError) as exc_info:
        video_assembly._darwin_no_replace_rename(src, dst)
    assert exc_info.value.errno == errno.ENOSYS
    assert "renamex_np not found in libc" in str(exc_info.value)


def test_darwin_no_replace_rename_cdll_error_raises_enosys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cdll_fail(*args: object, **kwargs: object) -> None:
        raise OSError("Failed to load libc")

    monkeypatch.setattr(ctypes, "CDLL", cdll_fail)
    src = tmp_path / "temp.mp4"
    dst = tmp_path / "dest.mp4"

    with pytest.raises(OSError) as exc_info:
        video_assembly._darwin_no_replace_rename(src, dst)
    assert exc_info.value.errno == errno.ENOSYS
    assert "Could not load libc" in str(exc_info.value)


def test_linux_no_replace_rename_cdll_error_raises_enosys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cdll_fail(*args: object, **kwargs: object) -> None:
        raise OSError("Failed to load libc")

    monkeypatch.setattr(ctypes, "CDLL", cdll_fail)
    src = tmp_path / "temp.mp4"
    dst = tmp_path / "dest.mp4"

    with pytest.raises(OSError) as exc_info:
        video_assembly._linux_no_replace_rename(src, dst)
    assert exc_info.value.errno == errno.ENOSYS
    assert "Could not load libc" in str(exc_info.value)


def test_linux_no_replace_rename_missing_symbol_raises_enosys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeLibcWithoutRenameat2:
        pass

    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: FakeLibcWithoutRenameat2())
    src = tmp_path / "temp.mp4"
    dst = tmp_path / "dest.mp4"

    with pytest.raises(OSError) as exc_info:
        video_assembly._linux_no_replace_rename(src, dst)
    assert exc_info.value.errno == errno.ENOSYS
    assert "renameat2 not found in libc" in str(exc_info.value)


def test_linux_no_replace_rename_success_and_exists_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[int, bytes, int, bytes, int]] = []

    class FakeRenameat2:
        argtypes: list[object] | None = None
        restype: object = None

        def __call__(
            self,
            dfd1: ctypes.c_int,
            src: bytes,
            dfd2: ctypes.c_int,
            dst: bytes,
            flags: ctypes.c_uint,
        ) -> int:
            dfd1_val = dfd1.value if hasattr(dfd1, "value") else int(dfd1)
            dfd2_val = dfd2.value if hasattr(dfd2, "value") else int(dfd2)
            flags_val = flags.value if hasattr(flags, "value") else int(flags)
            calls.append((dfd1_val, src, dfd2_val, dst, flags_val))
            return 0

    class FakeLibc:
        renameat2 = FakeRenameat2()

    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: FakeLibc())
    src = tmp_path / "temp.mp4"
    dst = tmp_path / "dest.mp4"
    video_assembly._linux_no_replace_rename(src, dst)

    assert len(calls) == 1
    assert calls[0][1] == os.fsencode(src)
    assert calls[0][3] == os.fsencode(dst)
    assert calls[0][4] == 1  # RENAME_NOREPLACE

    class FakeFailingRenameat2:
        argtypes: list[object] | None = None
        restype: object = None

        def __call__(self, *args: object, **kwargs: object) -> int:
            return -1

    FakeLibc.renameat2 = FakeFailingRenameat2()
    monkeypatch.setattr(ctypes, "get_errno", lambda: errno.EEXIST)
    with pytest.raises(FileExistsError) as exc_info:
        video_assembly._linux_no_replace_rename(src, dst)
    assert exc_info.value.errno == errno.EEXIST


def test_platform_no_replace_rename_dispatch_win32(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[tuple[Path, Path]] = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(video_assembly.os, "rename", lambda s, d: called.append((Path(s), Path(d))))
    src, dst = tmp_path / "src.mp4", tmp_path / "dst.mp4"
    video_assembly._platform_no_replace_rename(src, dst)
    assert called == [(src, dst)]


def test_platform_no_replace_rename_dispatch_linux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[tuple[Path, Path]] = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        video_assembly, "_linux_no_replace_rename", lambda s, d: called.append((s, d))
    )
    src, dst = tmp_path / "src.mp4", tmp_path / "dst.mp4"
    video_assembly._platform_no_replace_rename(src, dst)
    assert called == [(src, dst)]


def test_platform_no_replace_rename_dispatch_darwin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[tuple[Path, Path]] = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        video_assembly, "_darwin_no_replace_rename", lambda s, d: called.append((s, d))
    )
    src, dst = tmp_path / "src.mp4", tmp_path / "dst.mp4"
    video_assembly._platform_no_replace_rename(src, dst)
    assert called == [(src, dst)]


def test_platform_no_replace_rename_dispatch_unsupported_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "sunos5")
    src, dst = tmp_path / "src.mp4", tmp_path / "dst.mp4"
    with pytest.raises(OSError) as exc_info:
        video_assembly._platform_no_replace_rename(src, dst)
    assert exc_info.value.errno == errno.ENOSYS
    assert "No platform atomic no-replace rename for sunos5" in str(exc_info.value)


def test_probe_directory_mtime_ignores_unlink_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_unlink = os.unlink
    unlink_failed: list[str] = []

    def failing_unlink(path: str | bytes | os.PathLike[str] | os.PathLike[bytes]) -> None:
        unlink_failed.append(str(path))
        raise OSError(errno.EPERM, "mock probe unlink failure")

    monkeypatch.setattr(video_assembly.os, "unlink", failing_unlink)
    mtime = video_assembly._probe_directory_mtime(tmp_path)
    assert isinstance(mtime, float)
    assert len(unlink_failed) == 1
    original_unlink(unlink_failed[0])


def test_matches_pre_rename_reservation_permission_error_raises_assembly_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "target.mp4"
    output.touch()
    st = os.stat(output)

    def lstat_eperm(
        _path: int | str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args: object,
        **kwargs: object,
    ) -> os.stat_result:
        raise PermissionError("Access denied")

    monkeypatch.setattr(video_assembly.os, "lstat", lstat_eperm)
    with pytest.raises(VideoAssemblyError) as exc_info:
        video_assembly._matches_pre_rename_reservation(output, st)
    assert exc_info.value.charged == "no"
    assert f"Could not remove stale output reservation: {output}" in exc_info.value.detail
    assert "Access denied" in exc_info.value.detail


def test_rename_dance_clear_returns_when_pre_rename_reservation_not_matched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "target.mp4"
    output.touch()
    st = os.stat(output)

    def lstat_fnf(
        _path: int | str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args: object,
        **kwargs: object,
    ) -> os.stat_result:
        raise FileNotFoundError("gone before lstat")

    monkeypatch.setattr(video_assembly.os, "lstat", lstat_fnf)

    def fail_rename(*args: object) -> None:
        raise AssertionError("platform rename should not be called when reservation check fails")

    monkeypatch.setattr(video_assembly, "_platform_no_replace_rename", fail_rename)
    video_assembly._rename_dance_clear(output, st)
    assert output.exists()


def test_rename_dance_clear_returns_if_rename_aside_raises_file_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "stale.mp4"
    output.touch()
    past = time.time() - 120.0
    os.utime(output, (past, past))
    st = os.stat(output)

    def rename_fnf(_src: Path, _dst: Path) -> None:
        raise FileNotFoundError("concurrent deletion")

    monkeypatch.setattr(video_assembly, "_platform_no_replace_rename", rename_fnf)
    video_assembly._rename_dance_clear(output, st)
    assert output.exists()


def test_rename_dance_clear_handles_stale_candidate_file_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "stale.mp4"
    output.touch()
    past = time.time() - 120.0
    os.utime(output, (past, past))
    st = os.stat(output)

    monkeypatch.setattr(video_assembly, "_platform_no_replace_rename", lambda s, d: None)

    original_lstat = os.lstat

    def lstat_candidate_fnf(
        path: int | str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args: object,
        **kwargs: object,
    ) -> os.stat_result:
        if ".gflow-stale-" in str(path):
            raise FileNotFoundError("candidate vanished")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(video_assembly.os, "lstat", lstat_candidate_fnf)
    video_assembly._rename_dance_clear(output, st)


def test_rename_dance_clear_raises_video_assembly_error_on_stale_candidate_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "stale.mp4"
    output.touch()
    past = time.time() - 120.0
    os.utime(output, (past, past))
    st = os.stat(output)

    monkeypatch.setattr(video_assembly, "_platform_no_replace_rename", lambda s, d: None)

    original_lstat = os.lstat

    def lstat_candidate_eio(
        path: int | str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args: object,
        **kwargs: object,
    ) -> os.stat_result:
        if ".gflow-stale-" in str(path):
            raise OSError(errno.EIO, "stale candidate read error")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(video_assembly.os, "lstat", lstat_candidate_eio)
    with pytest.raises(VideoAssemblyError) as exc_info:
        video_assembly._rename_dance_clear(output, st)
    assert exc_info.value.charged == "no"
    assert f"Could not remove stale output reservation: {output}" in exc_info.value.detail
    assert "stale candidate read error" in exc_info.value.detail


def test_reserved_replace_publish_os_open_file_exists_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp_output = tmp_path / "temp.mp4"
    temp_output.write_bytes(b"data")
    output = tmp_path / "output.mp4"

    def open_exists(*args: object, **kwargs: object) -> int:
        raise FileExistsError("File exists")

    monkeypatch.setattr(video_assembly.os, "open", open_exists)
    expected_msg = f"Output already exists: {re.escape(str(output))}"
    with pytest.raises(VideoAssemblyError, match=expected_msg):
        video_assembly._reserved_replace_publish(temp_output, output)


def test_reserved_replace_publish_identity_recheck_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp_output = tmp_path / "temp.mp4"
    temp_output.write_bytes(b"data")
    output = tmp_path / "output.mp4"

    original_stat = os.stat

    def failing_stat(
        path: int | str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args: object,
        **kwargs: object,
    ) -> os.stat_result:
        if str(path) == str(output):
            raise OSError(errno.ENOENT, "target vanished during identity check")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(video_assembly.os, "stat", failing_stat)
    expected_msg = f"Output already exists: {re.escape(str(output))}"
    with pytest.raises(VideoAssemblyError, match=expected_msg):
        video_assembly._reserved_replace_publish(temp_output, output)


def test_reserved_replace_publish_cleanup_unlink_os_error_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp_output = tmp_path / "temp.mp4"
    temp_output.write_bytes(b"data")
    output = tmp_path / "output.mp4"

    def failing_replace(_src: Path | str, _dst: Path | str) -> None:
        raise RuntimeError("replace simulated failure")

    real_unlink = Path.unlink

    def failing_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if str(self) == str(output):
            raise OSError(errno.EACCES, "cannot unlink reservation")
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(video_assembly.os, "replace", failing_replace)
    monkeypatch.setattr(Path, "unlink", failing_unlink)

    with pytest.raises(RuntimeError, match="replace simulated failure"):
        video_assembly._reserved_replace_publish(temp_output, output)


def test_assemble_videos_ignores_audio_spool_unlink_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = tmp_path / "clip.mp4"
    output = tmp_path / "joined.mp4"
    _make_dummy_clip(clip, size=(64, 48), rate=10, frames=2, audio=True)

    spool_file = tmp_path / "fake_spool.wav"
    spool_file.write_bytes(b"spool audio data")

    def make_audio_with_spool(*args: object, **kwargs: object) -> list[Path]:
        return [spool_file]

    real_unlink = Path.unlink
    unlink_called: list[Path] = []

    def failing_spool_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self == spool_file:
            unlink_called.append(self)
            raise OSError(errno.EACCES, "cannot delete spool file")
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(video_assembly, "_make_audio", make_audio_with_spool)
    monkeypatch.setattr(Path, "unlink", failing_spool_unlink)

    result = assemble_videos([clip], output)
    assert result.output_file == output
    assert len(unlink_called) == 1
    real_unlink(spool_file)
