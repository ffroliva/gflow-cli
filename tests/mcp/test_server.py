from __future__ import annotations

import inspect

from gflow_cli.cli import main
from gflow_cli.mcp.server import mcp


def test_mcp_cli_option_symmetry():
    # Retrieve FastMCP tools
    tools = {t.name: t for t in mcp._tool_manager.list_tools()}

    # 1. Compare gflow_generate_image tool vs gflow image t2i Click command
    assert "gflow_generate_image" in tools
    image_tool = tools["gflow_generate_image"]
    main.commands["image"].commands["t2i"]

    mcp_sig = inspect.signature(image_tool.fn)
    mcp_params = mcp_sig.parameters

    # Check key parameter mappings
    assert "prompt" in mcp_params
    assert "model" in mcp_params
    assert "aspect" in mcp_params
    assert "count" in mcp_params
    assert "profile" in mcp_params
    assert "project_id" in mcp_params
    assert "reference_entities" in mcp_params
    assert "reference_entity_names" in mcp_params

    # Verify defaults
    assert mcp_params["model"].default == "nano-pro"
    assert mcp_params["aspect"].default == "1:1"
    assert mcp_params["count"].default == 1
    assert mcp_params["profile"].default == "default"
    assert mcp_params["project_id"].default is None

    # 2. Compare gflow_generate_video tool vs gflow video commands
    assert "gflow_generate_video" in tools
    video_tool = tools["gflow_generate_video"]
    mcp_vid_sig = inspect.signature(video_tool.fn)
    mcp_vid_params = mcp_vid_sig.parameters

    assert "prompt" in mcp_vid_params
    assert "mode" in mcp_vid_params
    assert "aspect" in mcp_vid_params
    assert "tier" in mcp_vid_params
    assert "model" in mcp_vid_params
    assert "duration" in mcp_vid_params
    assert "count" in mcp_vid_params
    assert "seed" in mcp_vid_params
    assert "start_image" in mcp_vid_params
    assert "end_image" in mcp_vid_params
    assert "reference_images" in mcp_vid_params
    assert "reference_entities" in mcp_vid_params
    assert "reference_entity_names" in mcp_vid_params
    assert "reference_audio" in mcp_vid_params
    assert "profile" in mcp_vid_params
