# SPDX-License-Identifier: MIT
"""Truth guards for MCP resources (#495).

The agent guide is our own instruction sheet — an agent following it must
never be told to pass a parameter that does not exist on the registered
tools. #495 caught the guide naming ``image_path`` while the real video
parameters are ``initial_frame`` / ``reference_images``.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_mcp_guide_names_real_video_params() -> None:
    from gflow_cli.mcp.resources import mcp_guide

    text = await mcp_guide()
    assert "image_path" not in text, "#495: image_path is not a real parameter"
    assert "initial_frame" in text
    assert "reference_images" in text
