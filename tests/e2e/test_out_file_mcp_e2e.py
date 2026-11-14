# SPDX-License-Identifier: MIT
"""Live proof that the MCP generate tools accept out_file and write to the output dir (#1158).

Calls ``gflow_generate_image`` in-process with ``out_file="<relative name>.png"``,
``wait=True``, and asserts the generated file exists under the configured output directory.

Cost: $0 on any account (Flow daily image quota, zero Veo credits).
Opt-in: ``-m e2e_image`` with ``GFLOW_CLI_E2E_PROFILE`` set.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.config import get_settings
from gflow_cli.mcp import tools

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_image]

_PROMPT = "a single red origami bird against a plain white background, minimalist"


async def test_mcp_generate_image_writes_to_out_file(
    e2e_profile_dir: Path,
) -> None:
    """gflow_generate_image with out_file writes the image to the configured output dir."""
    profile = os.environ.get("GFLOW_CLI_E2E_PROFILE", "").strip()
    if not profile:
        pytest.skip("GFLOW_CLI_E2E_PROFILE must name a logged-in profile")

    settings = get_settings()
    out_filename = "test_mcp_out_file_output.png"
    expected_path = (settings.output_dir / out_filename).resolve()
    if expected_path.exists():
        expected_path.unlink()

    result: dict[str, Any] = await tools.gflow_generate_image(
        prompt=_PROMPT,
        aspect="1:1",
        profile=profile,
        out_file=out_filename,
        wait=True,
    )

    assert result.get("status") == "completed", f"MCP image generation failed: {result}"
    assert expected_path.is_file(), f"Expected output file not found: {expected_path}"
    assert str(expected_path) in result.get("files", []), (
        f"Generated file path {expected_path} missing from result files: {result.get('files')}"
    )
