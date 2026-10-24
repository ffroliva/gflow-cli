"""`--output` on another filesystem (#639 café run, 2026-10-17): the clip was downloaded
under the working directory and `--output` named a tmpfs path; `Path.replace` cannot cross
filesystems (EXDEV), so a finished, billed clip ended as exit 1 "Unexpected error"."""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.cli_video import _relocate_video_output


@dataclass(frozen=True)
class _Result:
    local_path: Path | None


def test_output_on_another_filesystem_is_moved_not_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = tmp_path / "dl" / "clip.mp4"
    clip.parent.mkdir()
    clip.write_bytes(b"mp4")
    target = tmp_path / "out" / "beat.mp4"
    real_rename = os.rename

    def cross_device(src: Any, dst: Any) -> None:
        if Path(src) == clip:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", cross_device)
    monkeypatch.setattr(os, "replace", cross_device)

    result = _relocate_video_output(_Result(clip), target)

    assert result.local_path == target
    assert target.read_bytes() == b"mp4"
    assert not clip.exists()
