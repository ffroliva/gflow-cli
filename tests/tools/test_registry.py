from __future__ import annotations

from pathlib import Path

import pytest
import structlog

from gflow_cli.config import get_settings
from gflow_cli.errors import ConfigurationError
from gflow_cli.tools.registry import get_tool, iter_tools, reset_registry, tool_names


def setup_function() -> None:
    reset_registry()


def _write_tool(tools_dir: Path, slug: str, *, title: str = "Custom") -> None:
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / f"{slug}.toml").write_text(
        f'name = "{slug}"\n'
        f'title = "{title}"\n'
        'description = "A user-authored tool."\n'
        'category = "both"\n'
        'version = "1"\n\n'
        "[config]\n"
        'system_template = "Rewrite the prompt vividly."\n',
        encoding="utf-8",
    )


def test_creative_director_registered() -> None:
    assert "creative-director" in tool_names()
    assert get_tool("creative-director").title == "Creative Director"
    assert [t.name for t in iter_tools()] == sorted(tool_names())


def test_unknown_tool_raises_with_valid_names() -> None:
    with pytest.raises(ConfigurationError) as exc:
        get_tool("nope")
    assert "creative-director" in str(exc.value)


class TestMyToolsLoader:
    def test_absent_user_dir_loads_only_builtins(self) -> None:
        # No <home>/tools dir exists in a fresh isolated home → only builtins.
        assert tool_names() == ("creative-director", "reverse-engineer", "storyboard")

    def test_user_tool_is_registered_alongside_builtins(self) -> None:
        _write_tool(get_settings().user_tools_dir(), "my-custom")
        reset_registry()

        assert "my-custom" in tool_names()
        assert get_tool("my-custom").title == "Custom"
        # Builtins are not lost when user tools are present.
        assert "creative-director" in tool_names()

    def test_user_tool_overrides_builtin_with_warning(
        self,
        install_log_capture: structlog.testing.LogCapture,
    ) -> None:
        # A user TOML with a builtin's name shadows it (user customization wins),
        # and the shadow is logged so it is never silent.
        _write_tool(get_settings().user_tools_dir(), "creative-director", title="My Override")
        reset_registry()

        assert get_tool("creative-director").title == "My Override"
        events = {e["event"] for e in install_log_capture.entries}
        assert "tool_user_override" in events

    def test_malformed_user_tool_fails_loud(self) -> None:
        tools_dir = get_settings().user_tools_dir()
        tools_dir.mkdir(parents=True, exist_ok=True)
        (tools_dir / "broken.toml").write_text("this is = not valid = toml", encoding="utf-8")
        reset_registry()

        with pytest.raises(ConfigurationError):
            tool_names()
