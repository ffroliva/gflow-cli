"""GFLOW_CLI_BROWSER_WINDOW_POSITION places the headed generation window (#923).

Off-screen by default so a generation run does not cover the user's desktop; any
``X,Y`` places it somewhere visible; empty restores Chrome's own placement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from gflow_cli.api.client import FlowApiClient
from gflow_cli.config import Settings

if TYPE_CHECKING:
    from pathlib import Path


def _position_args(tmp_path: Path, settings: Settings) -> list[str]:
    kwargs = FlowApiClient(profile_dir=tmp_path, settings=settings)._persistent_context_kwargs()  # noqa: SLF001
    return [a for a in kwargs["args"] if a.startswith("--window-position")]


def test_default_is_off_screen(tmp_path: Path) -> None:
    assert _position_args(tmp_path, Settings()) == ["--window-position=-30000,-30000"]


def test_env_places_window_where_asked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_BROWSER_WINDOW_POSITION", "100,100")
    assert _position_args(tmp_path, Settings()) == ["--window-position=100,100"]


def test_empty_restores_chrome_placement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_BROWSER_WINDOW_POSITION", "")
    assert _position_args(tmp_path, Settings()) == []


@pytest.mark.parametrize("bad", ["abc", "100", "1,2,3", "10 ,20", "--x=1,2"])
def test_malformed_position_is_refused(bad: str) -> None:
    with pytest.raises(ValidationError):
        Settings(browser_window_position=bad)
