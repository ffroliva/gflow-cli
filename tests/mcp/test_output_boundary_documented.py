"""GHSA-hqfj-4jww-76f2: a tool that writes where the caller says must tell agents the limit.

Agents read the tool ``description`` from ``tools/list``; the docstring ``Args:`` block
never reaches them (see test_wait_parameter_documented.py). The release doc-review found
the generate tools refusing an ``output`` their listed description never mentioned.
"""

from __future__ import annotations

from typing import Any

_PATH_PARAMS = ("output", "out_file", "out_dir")


def test_every_tool_taking_a_write_path_names_the_output_dir_limit(mcp_server: Any) -> None:
    tools = mcp_server._tool_manager._tools
    writers = {
        name: tool
        for name, tool in tools.items()
        if set(_PATH_PARAMS) & set(tool.parameters.get("properties", {}))
    }
    assert {"gflow_generate_image", "gflow_generate_video", "gflow_download_media"} <= set(writers)
    for name, tool in writers.items():
        assert "inside the configured output directory" in (tool.description or ""), name
