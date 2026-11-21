from __future__ import annotations

import pytest

from gflow_cli.errors import ConfigurationError
from gflow_cli.mcp.audio_tools import gflow_narrate


@pytest.mark.asyncio
async def test_gflow_narrate_returns_local_audio_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from gflow_cli import config
    from gflow_cli.services.audio_narration import NarrationResult

    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(tmp_path))
    config.reset_settings()
    result = NarrationResult(tmp_path / "voice.mp3", "edge", "audio/mpeg")
    monkeypatch.setattr("gflow_cli.mcp.audio_tools.narrate", lambda **_: result)
    response = await gflow_narrate(text="hello", out_file="voice.mp3")
    assert response == {
        "status": "ok",
        "out_file": str(result.out_file),
        "provider": "edge",
        "media_type": "audio/mpeg",
    }
    config.reset_settings()


@pytest.mark.asyncio
async def test_mcp_narration_provider_errors_are_typed_and_redacted(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from gflow_cli import config

    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(tmp_path))
    config.reset_settings()
    monkeypatch.setattr(
        "gflow_cli.mcp.audio_tools.narrate",
        lambda **_: (_ for _ in ()).throw(
            ConfigurationError("provider key rejected", charged="no")
        ),
    )
    response = await gflow_narrate(text="private narration", out_file="voice.wav")
    assert response["status"] == "error"
    assert response["error"]["type"] == "https://gflow-cli.dev/errors/configuration"
    assert "private narration" not in str(response)
    config.reset_settings()


def test_gflow_narrate_is_registered_with_provider_disclosure(mcp_server) -> None:
    tool = mcp_server._tool_manager._tools["gflow_narrate"]
    properties = tool.parameters["properties"]
    assert {"text", "text_file", "out_file", "provider", "voice", "rate", "pitch"} <= set(
        properties
    )
    assert {"out_file"} <= set(tool.parameters["required"])
    assert "text is sent to the selected provider" in tool.description.lower()
    assert "flow credits" in tool.description.lower()
