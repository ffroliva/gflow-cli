"""An MCP server waits out profile contention instead of failing it (#862, #864).

Two separate defects, both measured before this file existed:

* The wait default was applied inside the server entry point, but ``gflow``'s root
  command had already loaded and cached settings, so ``gflow mcp run`` kept the CLI's
  fail-fast ``0``.
* Tool calls run inside the server's own process, where lease contention fails fast
  by design (waiting on yourself would deadlock). Two calls on one profile therefore
  have to be serialized in the server; no wait setting can do it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from gflow_cli.config import get_settings, reset_settings
from gflow_cli.data.store import DataStore

ENV = "GFLOW_CLI_LEASE_WAIT_SECONDS"


@pytest.fixture
def unset_wait(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv(ENV, "0")  # registers the original state for undo
    monkeypatch.delenv(ENV)
    reset_settings()
    yield
    reset_settings()


def test_wait_default_reaches_settings_the_cli_already_cached(unset_wait: None) -> None:
    from gflow_cli.mcp.server import _apply_mcp_lease_wait_default

    assert get_settings().lease_wait_seconds == 0  # what `gflow`'s root command caches
    _apply_mcp_lease_wait_default()

    assert get_settings().lease_wait_seconds == 180


def test_an_explicit_value_is_never_overridden(
    unset_wait: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.mcp.server import _apply_mcp_lease_wait_default

    monkeypatch.setenv(ENV, "0")
    reset_settings()
    _apply_mcp_lease_wait_default()

    assert get_settings().lease_wait_seconds == 0


@pytest.mark.parametrize("entry", ["run_stdio", "run_http", "run_sse"])
def test_every_entry_point_applies_the_default_first(entry: str) -> None:
    import inspect

    from gflow_cli.mcp import server

    body = inspect.getsource(getattr(server, entry))
    first_call = body.split('"""')[-1].strip().splitlines()
    assert any("_apply_mcp_lease_wait_default()" in line for line in first_call[:4]), entry


class _Peak:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0

    async def hold(self, *_: Any, **__: Any) -> dict[str, Any]:
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.05)
        self.active -= 1
        return {"status": "ok"}


async def test_calls_on_one_profile_run_one_at_a_time() -> None:
    from gflow_cli.mcp import tools

    peak = _Peak()
    with (
        patch("gflow_cli.mcp.tools._resolve_and_validate_profile", return_value="p"),
        patch("gflow_cli.mcp.tools.inspect_credit_profile", new=peak.hold),
    ):
        await asyncio.gather(tools.gflow_get_credits(), tools.gflow_get_credits())

    assert peak.peak == 1


async def test_calls_on_different_profiles_still_run_together() -> None:
    from gflow_cli.mcp import tools

    peak = _Peak()
    names = iter(["a", "b"])
    with (
        patch(
            "gflow_cli.mcp.tools._resolve_and_validate_profile", side_effect=lambda _: next(names)
        ),
        patch("gflow_cli.mcp.tools.inspect_credit_profile", new=peak.hold),
    ):
        await asyncio.gather(tools.gflow_get_credits(), tools.gflow_get_credits())

    assert peak.peak == 2


async def test_generations_on_one_profile_run_one_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.mcp import tools

    async def always_allow() -> bool:
        return True

    # The token bucket is module-global: spending two tokens here starves every later
    # generation test in the session (seen as `rate_limited` in test_tools_wired).
    monkeypatch.setattr(tools._rate_limiter, "acquire", always_allow)
    peak = _Peak()

    class _Worker:
        def __init__(self, **_: Any) -> None:
            self.repo = MagicMock(claim_task=lambda *_a, **_k: object())

        async def process_task(self, _task: object) -> None:
            await peak.hold()

        def close(self) -> None:
            pass

    db = DataStore.open(tmp_path / "gflow.db")
    db.close()
    settings = MagicMock(
        resolved_db_path=lambda: tmp_path / "gflow.db",
        profile_subdir=lambda _: tmp_path / "profile_p",
        timeout_seconds=30,
    )
    with (
        patch("gflow_cli.mcp.tools._resolve_and_validate_profile", return_value="p"),
        patch("gflow_cli.mcp.tools.FlowWorker", _Worker),
        patch("gflow_cli.mcp.tools.get_settings", return_value=settings),
    ):
        await asyncio.gather(
            tools.gflow_generate_image(prompt="a"), tools.gflow_generate_image(prompt="b")
        )

    assert peak.peak == 1


def test_a_dotenv_value_is_the_operators_choice_too(
    unset_wait: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gflow_cli.mcp.server import _apply_mcp_lease_wait_default

    (tmp_path / ".env").write_text(f"{ENV}=5\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    reset_settings()
    _apply_mcp_lease_wait_default()

    assert get_settings().lease_wait_seconds == 5
