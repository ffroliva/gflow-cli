"""Windows ACL hardening for profile dirs (issue #472).

POSIX mode bits are a no-op on Windows, so a profile created under a
world-readable ``GFLOW_CLI_HOME`` inherits that visibility. ``auth login``
hardens the dir before the browser runs; the client's launch path runs a
marker-gated sweep so pre-#472 profiles get hardened on first open.
Non-Windows platforms are a documented no-op (mode bits already cover them).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from gflow_cli import auth as auth_mod
from gflow_cli import winsec
from gflow_cli.errors import SecurityError

# Raw OEM-codepage bytes: a non-ASCII username must never poison the parse —
# the SID cell is pure ASCII (0x81 is undefined in cp1252 and invalid UTF-8).
_FAKE_WHOAMI_BYTES = (
    b'BENUTZERINFORMATIONEN\r\n\r\n"desk\x81top\\m\x81ller","S-1-5-21-111-222-333-1001"\r\n'
)


def _fake_run_factory(calls: list[list[str]], *, icacls_fails: bool = False) -> Any:
    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        if argv[0].endswith("whoami.exe"):
            return subprocess.CompletedProcess(argv, 0, stdout=_FAKE_WHOAMI_BYTES, stderr=b"")
        if icacls_fails:
            raise subprocess.CalledProcessError(5, argv, stderr=b"Access is denied.")
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    return fake_run


def test_noop_off_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no subprocess may run off Windows")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert winsec.restrict_dir_to_current_user(tmp_path) is False
    assert winsec.ensure_profile_hardened(tmp_path) is False


def test_windows_two_step_icacls_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(calls))
    assert winsec.restrict_dir_to_current_user(tmp_path) is True

    whoami = next(c for c in calls if c[0].endswith("whoami.exe"))
    harden, reset = (c for c in calls if c[0].endswith("icacls.exe"))
    # Security controls use absolute System32 paths — never a PATH lookup.
    assert "System32" in whoami[0] and "System32" in harden[0]
    # Step 1: strip inheritance on the dir, grant ONLY the current user by SID
    # (parsed from raw OEM bytes — locale/codepage-safe).
    assert str(tmp_path) in harden
    assert "/inheritance:r" in harden
    assert "/grant:r" in harden
    assert "*S-1-5-21-111-222-333-1001:(OI)(CI)F" in harden
    assert "/t" not in harden  # verified empirically: /t here breaks file access
    # Step 2: reset children recursively so they inherit the single ACE.
    assert str(tmp_path / "*") in reset
    assert "/reset" in reset
    assert "/t" in reset


def test_icacls_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Hardening is best-effort — a broken icacls must never fail the caller."""
    monkeypatch.setattr(sys, "platform", "win32")
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(calls, icacls_fails=True))
    assert winsec.restrict_dir_to_current_user(tmp_path) is False


def test_ensure_is_marker_gated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(calls))
    assert winsec.ensure_profile_hardened(tmp_path) is True
    assert (tmp_path / winsec.ACL_MARKER).exists()
    n = len(calls)
    # Second call: one stat, zero subprocesses.
    assert winsec.ensure_profile_hardened(tmp_path) is False
    assert len(calls) == n


def test_ensure_writes_no_marker_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed hardening must stay retryable — no marker."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", _fake_run_factory([], icacls_fails=True))
    assert winsec.ensure_profile_hardened(tmp_path) is False
    assert not (tmp_path / winsec.ACL_MARKER).exists()


@pytest.mark.skipif(sys.platform != "win32", reason="real icacls only exists on Windows")
def test_real_icacls_round_trip(tmp_path: Path) -> None:
    """On a real Windows host: hardening succeeds and the dir stays usable."""
    target = tmp_path / "profile_acl"
    target.mkdir()
    (target / "Cookies").write_bytes(b"x")
    assert winsec.restrict_dir_to_current_user(target) is True
    # Still fully usable by the current user afterwards — the naive /t form
    # failed exactly this read (PermissionError), which is why two steps.
    (target / "after.txt").write_text("ok", encoding="utf-8")
    assert (target / "Cookies").read_bytes() == b"x"


@pytest.mark.asyncio
async def test_login_hardens_profile_dir_before_strategy_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    def fake_ensure(path: Path) -> bool:
        order.append(f"harden:{path.name}")
        return True

    class FakeStrategy:
        async def login(self, pdir: Path, *, headless: bool = False) -> None:
            order.append("strategy")

    class FakeFactory:
        def create(self, browser: str) -> FakeStrategy:
            return FakeStrategy()

    monkeypatch.setattr(auth_mod, "ensure_profile_hardened", fake_ensure)
    monkeypatch.setattr(auth_mod, "AuthStrategyFactory", FakeFactory)
    await auth_mod.login("aclprof")
    assert order == ["harden:profile_aclprof", "strategy"]


@pytest.mark.asyncio
async def test_login_rejects_traversal_profile_name_before_any_mkdir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A traversal name must fail BEFORE mkdir or any ACL rewrite — the
    strategies' own boundary checks run too late to guard those."""
    from gflow_cli.config import get_settings

    touched: list[str] = []
    monkeypatch.setattr(
        auth_mod, "ensure_profile_hardened", lambda p: touched.append(str(p)) or True
    )
    with pytest.raises(SecurityError):
        await auth_mod.login("x/../../evil")
    assert touched == []
    assert not (get_settings().home.parent / "evil").exists()
