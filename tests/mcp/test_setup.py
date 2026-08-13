"""`gflow mcp setup` — MCP client-config generator (issue #475).

docs/MCP.md has promised this command since the stub shipped; it now writes
(or non-destructively merges) the gflow server entry into the target client's
config file, with a backup of any pre-existing file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from gflow_cli.cli import main
from gflow_cli.errors import ConfigurationError
from gflow_cli.mcp import setup as setup_mod


class TestConfigPathFor:
    def test_claude_desktop_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", str(Path("C:/u/AppData/Roaming")))
        expected = Path("C:/u/AppData/Roaming") / "Claude" / "claude_desktop_config.json"
        assert setup_mod.config_path_for("claude-desktop") == expected

    def test_claude_desktop_macos(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        expected = (
            tmp_path / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        )
        assert setup_mod.config_path_for("claude-desktop") == expected

    def test_claude_desktop_linux(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        expected = tmp_path / ".config" / "Claude" / "claude_desktop_config.json"
        assert setup_mod.config_path_for("claude-desktop") == expected

    def test_cursor_is_home_dotfile_everywhere(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        assert setup_mod.config_path_for("cursor") == tmp_path / ".cursor" / "mcp.json"

    def test_vscode_linux(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        expected = tmp_path / ".config" / "Code" / "User" / "mcp.json"
        assert setup_mod.config_path_for("vscode") == expected


class TestMergeServerEntry:
    def test_creates_config_from_nothing(self) -> None:
        cfg = json.loads(setup_mod.merge_server_entry(None, target="claude-desktop"))
        assert cfg["mcpServers"]["gflow"] == {"command": "gflow", "args": ["mcp", "run"]}

    def test_preserves_unrelated_keys_and_servers(self) -> None:
        existing = json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "x"}}})
        cfg = json.loads(setup_mod.merge_server_entry(existing, target="claude-desktop"))
        assert cfg["theme"] == "dark"
        assert cfg["mcpServers"]["other"] == {"command": "x"}
        assert cfg["mcpServers"]["gflow"]["command"] == "gflow"

    def test_updates_manual_gflow_cli_key_in_place_without_duplicate(self) -> None:
        """docs/MCP.md's manual blocks use the key 'gflow-cli' — setup must
        converge that entry, not add a second server."""
        existing = json.dumps({"mcpServers": {"gflow-cli": {"command": "stale"}}})
        cfg = json.loads(setup_mod.merge_server_entry(existing, target="claude-desktop"))
        assert cfg["mcpServers"]["gflow-cli"] == {"command": "gflow", "args": ["mcp", "run"]}
        assert "gflow" not in cfg["mcpServers"]

    def test_vscode_uses_servers_key_and_stdio_type(self) -> None:
        cfg = json.loads(setup_mod.merge_server_entry(None, target="vscode"))
        assert cfg["servers"]["gflow"] == {
            "type": "stdio",
            "command": "gflow",
            "args": ["mcp", "run"],
        }

    def test_invalid_json_raises_configuration_error(self) -> None:
        with pytest.raises(ConfigurationError):
            setup_mod.merge_server_entry("{definitely not json", target="claude-desktop")

    def test_non_object_root_raises_configuration_error(self) -> None:
        with pytest.raises(ConfigurationError):
            setup_mod.merge_server_entry(json.dumps([1, 2]), target="claude-desktop")


class TestApplyAndCli:
    def test_merges_existing_file_and_backs_it_up(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target_file = tmp_path / "claude_desktop_config.json"
        target_file.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), "utf-8")
        monkeypatch.setattr(setup_mod, "config_path_for", lambda t: target_file)

        result = CliRunner().invoke(main, ["mcp", "setup"])

        assert result.exit_code == 0, result.output
        cfg = json.loads(target_file.read_text(encoding="utf-8"))
        assert cfg["mcpServers"]["gflow"]["args"] == ["mcp", "run"]
        assert cfg["mcpServers"]["other"] == {"command": "x"}
        backup = target_file.with_name(target_file.name + ".gflow-backup")
        assert json.loads(backup.read_text(encoding="utf-8"))["mcpServers"] == {
            "other": {"command": "x"}
        }
        # Rich soft-wraps long paths in a narrow console — compare unwrapped.
        assert str(target_file) in result.output.replace("\n", "")

    def test_creates_file_and_parents_when_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target_file = tmp_path / "deep" / "nested" / "mcp.json"
        monkeypatch.setattr(setup_mod, "config_path_for", lambda t: target_file)

        result = CliRunner().invoke(main, ["mcp", "setup", "--target", "cursor"])

        assert result.exit_code == 0, result.output
        assert json.loads(target_file.read_text(encoding="utf-8"))["mcpServers"]["gflow"]
        assert not target_file.with_name(target_file.name + ".gflow-backup").exists()

    def test_corrupt_config_exits_11_and_leaves_file_untouched(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target_file = tmp_path / "claude_desktop_config.json"
        target_file.write_text("{broken", encoding="utf-8")
        monkeypatch.setattr(setup_mod, "config_path_for", lambda t: target_file)

        result = CliRunner().invoke(main, ["mcp", "setup"])

        assert result.exit_code == 11
        assert target_file.read_text(encoding="utf-8") == "{broken"
        assert "Traceback" not in result.output
