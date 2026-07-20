"""RealChromeStrategy — cancellation safety + chrome-arg hygiene (Task D4).

Companion to ``tests/auth/strategies/test_strategies.py`` (the success/marker/
lease-ordering suite). This file focuses on D4's two additions:

* the duplicate Flow-URL positional is gone (Chrome opens ONE Flow tab), and
* a cancellation while waiting for the user to close Chrome terminates + reaps
  the child and releases the profile lease (nothing orphaned).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.auth.real_chrome import (
    GEMINI_URL,
    RealChromeStrategy,
    _await_chrome_close,
    _build_chrome_args,
)

# ---------------------------------------------------------------------------
# _build_chrome_args — Flow URL appears exactly once (D4: dup positional gone)
# ---------------------------------------------------------------------------


def test_build_chrome_args_opens_flow_once() -> None:
    args = _build_chrome_args(r"C:\fake\chrome.exe", Path("prof"), headless=False)
    assert args.count(GEMINI_URL) == 1, "Flow URL must be passed exactly once"
    assert args[-1] == GEMINI_URL, "the Flow URL should be the trailing positional"


def test_build_chrome_args_headless_opens_flow_once() -> None:
    args = _build_chrome_args(r"C:\fake\chrome.exe", Path("prof"), headless=True)
    assert args.count(GEMINI_URL) == 1
    assert "--headless=new" in args


# ---------------------------------------------------------------------------
# _await_chrome_close — cancellation terminates + reaps the child
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_await_chrome_close_cancellation_terminates_and_reaps() -> None:
    """A cancel while waiting for Chrome to close must terminate + reap the
    child (so it can't be orphaned holding the profile lock) and re-raise."""
    entered = asyncio.Event()
    closed = asyncio.Event()

    proc = MagicMock(name="proc")

    async def _wait() -> int:
        entered.set()
        await closed.wait()  # released by terminate() below
        return 0

    proc.wait = _wait
    proc.terminate = MagicMock(side_effect=lambda: closed.set())
    proc.kill = MagicMock()

    task = asyncio.create_task(_await_chrome_close(proc, timeout_seconds=600))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    proc.terminate.assert_called_once()
    proc.kill.assert_not_called()  # exited within the reap grace after terminate


# ---------------------------------------------------------------------------
# login — cancellation releases the profile lease (chrome reaped first)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_cancellation_releases_lease_and_reaps_chrome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling login while passive-capture Chrome runs must terminate the
    child and release the profile lease via the enclosing ``async with
    ProfileLease`` (chrome dead BEFORE the profile is freed)."""
    from gflow_cli.profile_lease import ProfileLease

    events: list[str] = []

    def acq(self: ProfileLease) -> ProfileLease:
        events.append("acquire")
        return self

    def rel(self: ProfileLease) -> None:
        events.append("release")

    monkeypatch.setattr(ProfileLease, "acquire", acq)
    monkeypatch.setattr(ProfileLease, "release", rel)

    strategy = RealChromeStrategy()
    gflow_home = tmp_path / "gflow_home"
    profile_dir = gflow_home / "profile_default"
    gflow_home.mkdir()

    entered = asyncio.Event()
    closed = asyncio.Event()
    proc = MagicMock(name="proc")

    async def _wait() -> int:
        entered.set()
        await closed.wait()
        return 0

    proc.wait = _wait
    proc.terminate = MagicMock(side_effect=lambda: closed.set())
    proc.kill = MagicMock()

    with (
        patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
        patch(
            "gflow_cli.auth.real_chrome.find_chrome_executable",
            return_value=r"C:\fake\chrome.exe",
        ),
        patch(
            "gflow_cli.auth.real_chrome.asyncio.create_subprocess_exec",
            AsyncMock(return_value=proc),
        ),
        # verify_flow_profile must never run — the cancel lands before it.
        patch(
            "gflow_cli.auth.real_chrome.verify_flow_profile",
            AsyncMock(side_effect=AssertionError("verification must not run after cancel")),
        ),
    ):
        mock_settings.return_value.home = gflow_home
        task = asyncio.create_task(strategy.login(profile_dir, headless=False))
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    proc.terminate.assert_called_once()
    # Lease acquired around Chrome, then released on the cancellation path.
    assert events == ["acquire", "release"]
