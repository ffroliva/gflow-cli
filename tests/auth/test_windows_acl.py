"""Windows ACL hardening for profile dirs (issue #472).

POSIX mode bits are a no-op on Windows, so a profile created under a
world-readable ``GFLOW_CLI_HOME`` inherits that visibility. ``auth login``
now applies a real restrict-to-current-user DACL via ``icacls``. Non-Windows
platforms are a documented no-op (mode bits already cover them).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from gflow_cli import auth as auth_mod

_FAKE_WHOAMI_CSV = '"Benutzerinformationen"\r\n"desktop\\dev user","S-1-5-21-111-222-333-1001"\r\n'


def test_noop_off_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no subprocess may run off Windows")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert auth_mod.restrict_dir_to_current_user(tmp_path) is False


def test_windows_invokes_icacls_with_current_user_sid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        stdout = _FAKE_WHOAMI_CSV if argv[0] == "whoami" else ""
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert auth_mod.restrict_dir_to_current_user(tmp_path) is True
    harden, reset = (c for c in calls if c[0] == "icacls")
    # Step 1: strip inheritance on the dir, grant ONLY the current user by SID
    # (locale-safe), with dir+file inheritance flags.
    assert str(tmp_path) in harden
    assert "/inheritance:r" in harden
    assert "/grant:r" in harden
    assert "*S-1-5-21-111-222-333-1001:(OI)(CI)F" in harden
    assert "/t" not in harden  # verified: /t here breaks file access
    # Step 2: reset children recursively so they inherit the single ACE.
    assert str(tmp_path / "*") in reset
    assert "/reset" in reset
    assert "/t" in reset


def test_icacls_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Hardening is best-effort — a broken icacls must never fail login."""
    monkeypatch.setattr(sys, "platform", "win32")

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if argv[0] == "whoami":
            return subprocess.CompletedProcess(argv, 0, stdout=_FAKE_WHOAMI_CSV, stderr="")
        raise subprocess.CalledProcessError(5, argv)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert auth_mod.restrict_dir_to_current_user(tmp_path) is False


@pytest.mark.skipif(sys.platform != "win32", reason="real icacls only exists on Windows")
def test_real_icacls_round_trip(tmp_path: Path) -> None:
    """On a real Windows host: hardening succeeds and the dir stays usable."""
    target = tmp_path / "profile_acl"
    target.mkdir()
    (target / "Cookies").write_bytes(b"x")
    assert auth_mod.restrict_dir_to_current_user(target) is True
    # Still fully usable by the current user afterwards.
    (target / "after.txt").write_text("ok", encoding="utf-8")
    assert (target / "Cookies").read_bytes() == b"x"


@pytest.mark.asyncio
async def test_login_hardens_profile_dir_before_strategy_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    order: list[str] = []

    def fake_restrict(path: Path) -> bool:
        order.append(f"restrict:{path.name}")
        return True

    class FakeStrategy:
        async def login(self, pdir: Path, *, headless: bool = False) -> None:
            order.append("strategy")

    class FakeFactory:
        def create(self, browser: str) -> FakeStrategy:
            return FakeStrategy()

    monkeypatch.setattr(auth_mod, "restrict_dir_to_current_user", fake_restrict)
    monkeypatch.setattr(auth_mod, "AuthStrategyFactory", FakeFactory)
    await auth_mod.login("aclprof")
    assert order == ["restrict:profile_aclprof", "strategy"]
