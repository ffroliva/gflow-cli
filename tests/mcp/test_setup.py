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
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)  # set on GitHub runners
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
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        expected = tmp_path / ".config" / "Code" / "User" / "mcp.json"
        assert setup_mod.config_path_for("vscode") == expected

    def test_linux_honours_xdg_config_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """VS Code (Electron) reads $XDG_CONFIG_HOME — writing ~/.config while
        it is set would be a silent no-op (council review)."""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        expected = tmp_path / "xdg" / "Code" / "User" / "mcp.json"
        assert setup_mod.config_path_for("vscode") == expected

    def test_windows_without_appdata_raises_configuration_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delenv("APPDATA", raising=False)
        with pytest.raises(ConfigurationError):
            setup_mod.config_path_for("claude-desktop")


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

    def test_existing_user_entry_is_never_rewritten(self) -> None:
        """docs/MCP.md's local-clone block uses key 'gflow-cli' with a custom
        `uv --directory` command (+ possibly env) — setup must not clobber it
        or add a duplicate (council review)."""
        custom = {"command": "uv", "args": ["--directory", "/x", "run", "gflow", "mcp", "run"]}
        existing = json.dumps({"mcpServers": {"gflow-cli": custom}})
        assert setup_mod.merge_server_entry(existing, target="claude-desktop") is None

    def test_existing_gflow_key_also_short_circuits(self) -> None:
        existing = json.dumps({"mcpServers": {"gflow": {"command": "custom", "env": {"A": "1"}}}})
        assert setup_mod.merge_server_entry(existing, target="claude-desktop") is None

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

    def test_second_run_is_a_noop_and_backup_stays_pristine(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Re-running setup must not rewrite the config NOR overwrite the
        one pristine backup of the user's original file (council review)."""
        target_file = tmp_path / "claude_desktop_config.json"
        original = json.dumps({"mcpServers": {"other": {"command": "x"}}})
        target_file.write_text(original, encoding="utf-8")
        monkeypatch.setattr(setup_mod, "config_path_for", lambda t: target_file)

        first = CliRunner().invoke(main, ["mcp", "setup"])
        assert first.exit_code == 0
        merged_once = target_file.read_text(encoding="utf-8")

        second = CliRunner().invoke(main, ["mcp", "setup"])
        assert second.exit_code == 0
        assert "Already configured" in second.output
        assert target_file.read_text(encoding="utf-8") == merged_once
        backup = target_file.with_name(target_file.name + ".gflow-backup")
        assert backup.read_text(encoding="utf-8") == original  # pristine v1

    def test_bom_config_is_accepted(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """PowerShell's UTF8 encoding writes a BOM — that is not corruption."""
        target_file = tmp_path / "claude_desktop_config.json"
        target_file.write_bytes(b'\xef\xbb\xbf{"mcpServers": {}}')
        monkeypatch.setattr(setup_mod, "config_path_for", lambda t: target_file)

        result = CliRunner().invoke(main, ["mcp", "setup"])

        assert result.exit_code == 0, result.output
        cfg = json.loads(target_file.read_text(encoding="utf-8"))
        assert cfg["mcpServers"]["gflow"]["command"] == "gflow"

    def test_non_utf8_config_exits_11_without_traceback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target_file = tmp_path / "claude_desktop_config.json"
        target_file.write_bytes(b'{"caf\xe9": 1}')  # latin-1, invalid UTF-8
        monkeypatch.setattr(setup_mod, "config_path_for", lambda t: target_file)

        result = CliRunner().invoke(main, ["mcp", "setup"])

        assert result.exit_code == 11
        assert "Traceback" not in result.output

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
