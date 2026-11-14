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
    assert target.exists(), f"Expected output file not found at {target}"
    assert _is_image(target), f"Output file {target} is neither PNG nor JPEG"
    assert _OUT_FILE_DEPRECATION_WARNING not in proc.stderr


def test_e2e_out_file_deprecated_flag(e2e_env: dict[str, str], tmp_path: Path) -> None:
    """gflow image t2i with -o writes the image and emits the deprecation warning once."""
    target = tmp_path / "b.png"
    proc = _run_gflow(
        ["image", "t2i", "a blue sphere on a white table", "-o", str(target)],
        e2e_env,
    )
    assert proc.returncode == 0, f"gflow image t2i failed: {proc.stderr}"
    assert target.exists(), f"Expected output file not found at {target}"
    assert _is_image(target), f"Output file {target} is neither PNG nor JPEG"
    assert proc.stderr.count(_OUT_FILE_DEPRECATION_WARNING) == 1
