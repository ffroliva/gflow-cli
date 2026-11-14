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
    e2e_profile_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gflow_generate_image with out_file writes the image there (extension follows the bytes)."""
    profile = os.environ.get("GFLOW_CLI_E2E_PROFILE", "").strip()
    if not profile:
        pytest.skip("GFLOW_CLI_E2E_PROFILE must name a logged-in profile")
    out_dir = tmp_path / "out"
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(out_dir))  # never the user's real folder
    get_settings.cache_clear()

    result: dict[str, Any] = await tools.gflow_generate_image(
        prompt=_PROMPT, aspect="1:1", profile=profile, out_file="mcp_out_file.png", wait=True
    )

    assert result.get("status") == "completed", f"MCP image generation failed: {result}"
    files = [Path(f) for f in result.get("files", [])]
    assert len(files) == 1, result
    assert files[0].parent == out_dir.resolve(), files[0]
    assert files[0].stem == "mcp_out_file", files[0]
    assert files[0].is_file(), files[0]
