"""Live E2E for --out-file and -o/--output deprecation on image t2i (#1158).

Opt in with:
    GFLOW_CLI_E2E_PROFILE=<profile-name> uv run pytest -m e2e_image -v \\
        tests/e2e/test_out_file_cli_e2e.py
"""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from gflow_cli._cli_helpers import _OUT_FILE_DEPRECATION_WARNING

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_image]

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


def _is_image(path: Path) -> bool:
    data = path.read_bytes()
    return data.startswith(_PNG_MAGIC) or data.startswith(_JPEG_MAGIC)


def _saved(target: Path) -> Path:
    """The file written for *target*: gflow corrects the extension from the bytes (PNG/JPEG)."""
    found = sorted(target.parent.glob(f"{target.stem}.*"))
    assert len(found) == 1, f"expected one file for {target}, found {found}"
    assert _is_image(found[0]), f"{found[0]} is neither PNG nor JPEG"
    return found[0]


def _run_gflow(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "gflow_cli", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_e2e_out_file_new_flag(e2e_env: dict[str, str], tmp_path: Path) -> None:
    """gflow image t2i with --out-file writes the image and emits no deprecation warning."""
    target = tmp_path / "a.png"
    proc = _run_gflow(
        ["image", "t2i", "a red cube on a white table", "--out-file", str(target)],
        e2e_env,
    )
    assert proc.returncode == 0, f"gflow image t2i failed: {proc.stderr}"
    _saved(target)
    assert _OUT_FILE_DEPRECATION_WARNING not in proc.stderr


def test_e2e_out_file_deprecated_flag(e2e_env: dict[str, str], tmp_path: Path) -> None:
    """gflow image t2i with -o writes the image and emits the deprecation warning once."""
    target = tmp_path / "b.png"
    proc = _run_gflow(
        ["image", "t2i", "a blue sphere on a white table", "-o", str(target)],
        e2e_env,
    )
    assert proc.returncode == 0, f"gflow image t2i failed: {proc.stderr}"
    _saved(target)
    assert proc.stderr.count(_OUT_FILE_DEPRECATION_WARNING) == 1


def test_e2e_out_file_is_refused_under_cloud_storage(
    e2e_env: dict[str, str], tmp_path: Path
) -> None:
    """S12 (#1008): with an s3:// storage URI, video t2v --out-file exits 11 before submit ($0)."""
    env = {**e2e_env, "GFLOW_CLI_STORAGE_URI": "s3://gflow-e2e-refusal/gflow/"}
    proc = _run_gflow(
        ["video", "t2v", "a red kite over a beach", "--out-file", str(tmp_path / "c.mp4")],
        env,
    )
    assert proc.returncode == 11, f"expected exit 11, got {proc.returncode}: {proc.stderr}"
    assert "--out-file needs the clip" in proc.stdout + proc.stderr
    assert "Nothing was submitted" in proc.stdout + proc.stderr
    assert not (tmp_path / "c.mp4").exists()
