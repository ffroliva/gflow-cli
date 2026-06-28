from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.errors import ConfigurationError
from gflow_cli.tools.loader import load_builtin_tools, load_user_tools


def test_loads_creative_director_builtin() -> None:
    tools = load_builtin_tools()
    cd = tools["creative-director"]
    assert cd.title == "Creative Director"
    assert cd.category == "both"
    assert "GFLOW_CLI_GEMINI_API_KEY" in cd.requires_env
    assert "8k" in cd.config.banned_keywords
    # at least the banana image domains + the video set are present
    names = {d.name for d in cd.config.domains}
    assert {"cinema", "product", "portrait"} <= names
    assert {"cinematic", "documentary", "social"} <= names
    assert "Subject" in cd.config.system_template


def test_user_tools_empty_when_dir_absent(tmp_path: Path) -> None:
    assert load_user_tools(tmp_path / "nope") == {}


def test_user_tools_empty_when_dir_empty(tmp_path: Path) -> None:
    empty = tmp_path / "tools"
    empty.mkdir()
    assert load_user_tools(empty) == {}


def test_invalid_schema_raises_configuration_error(tmp_path: Path) -> None:
    from gflow_cli.tools.loader import _load_dir

    bad = tmp_path / "tools"
    bad.mkdir()
    (bad / "broken.toml").write_text('name = "x"\n', encoding="utf-8")  # missing required fields
    with pytest.raises(ConfigurationError):
        _load_dir(bad)


def test_malformed_toml_raises_configuration_error(tmp_path: Path) -> None:
    from gflow_cli.tools.loader import _load_dir

    bad = tmp_path / "tools"
    bad.mkdir()
    (bad / "broken.toml").write_text("this is = = not toml", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        _load_dir(bad)
