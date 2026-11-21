"""The #1387 settings spike puts the account's defaults back even when its own read fails."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

# Loaded by path: scripts/ is not an importable package (see test_analyze_agent_ui_capture.py).
_MOD_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "dev" / "spike_agent_settings_new_build.py"
)
_spec = importlib.util.spec_from_file_location("spike_agent_settings_new_build", _MOD_PATH)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules["spike_agent_settings_new_build"] = _mod
_spec.loader.exec_module(_mod)


class _Snapshot:
    def __init__(self) -> None:
        self.section = "video"


class _Composer:
    def __init__(self, *, restore_fails: bool = False) -> None:
        self.restored: list[_Snapshot] = []
        self._restore_fails = restore_fails

    async def apply_defaults(self, page: Any, **values: Any) -> _Snapshot:
        return _Snapshot()

    async def restore_defaults(self, page: Any, snapshot: _Snapshot) -> None:
        self.restored.append(snapshot)
        if self._restore_fails:
            raise RuntimeError("restore broke")


def _wire(monkeypatch: pytest.MonkeyPatch, composer: _Composer) -> None:
    async def state(page: Any) -> dict[str, Any]:
        return {}

    async def rows(page: Any) -> dict[str, str]:
        raise RuntimeError("row unreadable")

    monkeypatch.setattr(_mod.aoc, "AgentOnlyComposer", lambda: composer)
    monkeypatch.setattr(_mod, "_state", state)
    monkeypatch.setattr(_mod, "_rows", rows)


@pytest.mark.asyncio
async def test_a_failed_row_read_still_restores(monkeypatch: pytest.MonkeyPatch) -> None:
    composer = _Composer()
    _wire(monkeypatch, composer)
    out = await _mod._apply_restore(None, "video")
    assert len(composer.restored) == 1
    assert out["raised"] == "RuntimeError: row unreadable"
    assert out["restored"] is True


@pytest.mark.asyncio
async def test_a_failed_restore_is_kept_apart_from_the_read_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, _Composer(restore_fails=True))
    out = await _mod._apply_restore(None, "video")
    assert out["raised"] == "RuntimeError: row unreadable"
    assert out["restore_raised"] == "RuntimeError: restore broke"
    assert "restored" not in out
