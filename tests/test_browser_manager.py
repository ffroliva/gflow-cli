"""Tests for gflow_cli.browser_manager — TDD-first, all mocked (no real Chrome).

Run with:
    uv run python -m pytest tests/test_browser_manager.py -v \
        --cov=src/gflow_cli/browser_manager --cov-report=term-missing
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.errors import AuthMissingError, ConfigurationError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile_dir(tmp_path: Path, name: str = "test") -> Path:
    d = tmp_path / f"profile_{name}"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# is_browser_running
# ---------------------------------------------------------------------------


class TestIsBrowserRunning:
    def test_returns_false_when_no_chrome(self, tmp_path: Path) -> None:
        """ECONNREFUSED (or any network error) → False."""
        import httpx

        from gflow_cli.browser_manager import is_browser_running

        with patch(
            "gflow_cli.browser_manager.httpx.get",
            side_effect=httpx.ConnectError("refused"),
        ):
            assert is_browser_running(port=9222) is False

    def test_returns_true_on_valid_json_version(self) -> None:
        """200 with Chrome JSON → True."""
        from gflow_cli.browser_manager import is_browser_running

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "Browser": "Chrome/124.0.0.0",
            "webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/abc",
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("gflow_cli.browser_manager.httpx.get", return_value=mock_resp):
            assert is_browser_running(port=9222) is True

    def test_returns_false_on_timeout(self) -> None:
        """Timeout (even with retry) ultimately → False, never raises."""
        import httpx

        from gflow_cli.browser_manager import is_browser_running

        with patch(
            "gflow_cli.browser_manager.httpx.get",
            side_effect=httpx.TimeoutException("timeout"),
        ):
            assert is_browser_running(port=9222) is False

    def test_returns_false_on_invalid_json(self) -> None:
        """Response that doesn't parse as expected dict → False."""
        from gflow_cli.browser_manager import is_browser_running

        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("not json")
        mock_resp.raise_for_status = MagicMock()

        with patch("gflow_cli.browser_manager.httpx.get", return_value=mock_resp):
            assert is_browser_running(port=9222) is False

    def test_returns_false_on_http_error(self) -> None:
        """Non-2xx response → False."""
        import httpx

        from gflow_cli.browser_manager import is_browser_running

        with patch(
            "gflow_cli.browser_manager.httpx.get",
            side_effect=httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock()),
        ):
            assert is_browser_running(port=9222) is False


# ---------------------------------------------------------------------------
# Health check: 3s timeout + 1 retry
# ---------------------------------------------------------------------------


class TestHealthCheck3sTimeoutAndRetry:
    def test_health_check_3s_timeout_and_one_retry(self) -> None:
        """First attempt times out; second attempt succeeds → True."""
        import httpx

        from gflow_cli.browser_manager import is_browser_running

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"Browser": "Chrome/124.0.0.0"}
        mock_resp.raise_for_status = MagicMock()

        call_count = 0

        def side_effect(*args: object, **kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.TimeoutException("timeout on first try")
            return mock_resp

        with patch("gflow_cli.browser_manager.httpx.get", side_effect=side_effect):
            result = is_browser_running(port=9222)

        assert result is True
        assert call_count == 2

    def test_health_check_uses_3s_timeout(self) -> None:
        """httpx.get must be called with timeout=3."""
        import httpx

        from gflow_cli.browser_manager import is_browser_running

        captured_kwargs: list[dict] = []

        def capture(*args: object, **kwargs: object) -> object:
            captured_kwargs.append(dict(kwargs))
            raise httpx.ConnectError("refuse")

        with patch("gflow_cli.browser_manager.httpx.get", side_effect=capture):
            is_browser_running(port=9222)

        assert any(kw.get("timeout") == 3 for kw in captured_kwargs)


# ---------------------------------------------------------------------------
# Chrome binary detection
# ---------------------------------------------------------------------------


class TestChromeBinaryDetection:
    def test_chrome_binary_env_var_override(self, tmp_path: Path) -> None:
        """CHROME_BINARY env var is used before any path probing."""
        from gflow_cli.browser_manager import _find_chrome_binary

        custom = str(tmp_path / "custom_chrome")
        Path(custom).touch()

        with patch.dict(os.environ, {"CHROME_BINARY": custom}):
            result = _find_chrome_binary()

        assert result == custom

    def test_chrome_not_found_raises_configuration_error(self) -> None:
        """All detection paths fail → ConfigurationError with install hint."""
        from gflow_cli.browser_manager import _find_chrome_binary

        env_without = {
            k: v for k, v in os.environ.items() if k not in ("CHROME_BINARY", "LOCALAPPDATA")
        }
        # Ensure LOCALAPPDATA is a valid (but nonexistent) path so Path() doesn't crash
        env_without["LOCALAPPDATA"] = str(Path(tempfile.gettempdir()) / "nonexistent_chrome_test")

        with (
            patch.dict(os.environ, env_without, clear=True),
            patch("gflow_cli.browser_manager.shutil.which", return_value=None),
            patch.object(Path, "exists", return_value=False),
        ):
            with pytest.raises(ConfigurationError) as exc_info:
                _find_chrome_binary()

        assert "chrome" in str(exc_info.value).lower() or "Chrome" in str(exc_info.value)
        assert "https://www.google.com/chrome/" in str(exc_info.value)

    def test_chrome_binary_detection_falls_through_to_which(self, tmp_path: Path) -> None:
        """When CHROME_BINARY not set, shutil.which is tried."""
        from gflow_cli.browser_manager import _find_chrome_binary

        env_without = {k: v for k, v in os.environ.items() if k != "CHROME_BINARY"}
        with (
            patch.dict(os.environ, env_without, clear=True),
            patch("gflow_cli.browser_manager.shutil.which", return_value="/usr/bin/google-chrome"),
        ):
            result = _find_chrome_binary()

        assert result == "/usr/bin/google-chrome"

    def test_chrome_binary_detection_falls_through_platform_paths_on_windows(
        self, tmp_path: Path
    ) -> None:
        """On win32, platform-standard paths are probed when which() returns None."""
        from gflow_cli.browser_manager import _find_chrome_binary

        expected_path = "C:/Program Files/Google/Chrome/Application/chrome.exe"
        env_without = {k: v for k, v in os.environ.items() if k != "CHROME_BINARY"}

        def path_exists_mock(self: Path) -> bool:
            return str(self).replace("\\", "/") == expected_path.replace("\\", "/")

        with (
            patch.dict(os.environ, env_without, clear=True),
            patch("sys.platform", "win32"),
            patch("gflow_cli.browser_manager.shutil.which", return_value=None),
            patch.object(Path, "exists", path_exists_mock),
        ):
            result = _find_chrome_binary()

        assert "chrome.exe" in result.lower() or "Chrome" in result


# ---------------------------------------------------------------------------
# Port-range auto-increment
# ---------------------------------------------------------------------------


class TestPortRange:
    def test_port_range_increments_when_busy(self, tmp_path: Path) -> None:
        """Port 9222 returns non-gflow Chrome → probe 9223."""
        from gflow_cli.browser_manager import _find_available_cdp_port

        profile_dir = _make_profile_dir(tmp_path)

        # Port 9222: returns Chrome version info but no matching lockfile → not ours
        # Port 9223: ECONNREFUSED → free
        import httpx

        call_ports: list[int] = []

        def mock_get(url: str, **kwargs: object) -> object:
            port = int(url.split(":")[2].split("/")[0])
            call_ports.append(port)
            if port == 9222:
                mock_resp = MagicMock()
                mock_resp.json.return_value = {"Browser": "Chrome/124.0.0.0"}
                mock_resp.raise_for_status = MagicMock()
                return mock_resp
            raise httpx.ConnectError("refused")

        with patch("gflow_cli.browser_manager.httpx.get", side_effect=mock_get):
            chosen = _find_available_cdp_port(profile_dir, start_port=9222)

        assert chosen == 9223

    def test_all_ports_taken_raises_configuration_error(self, tmp_path: Path) -> None:
        """All 8 ports (9222-9229) respond with non-gflow Chrome → ConfigurationError."""
        from gflow_cli.browser_manager import _find_available_cdp_port

        profile_dir = _make_profile_dir(tmp_path)

        def mock_get(url: str, **kwargs: object) -> object:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"Browser": "Chrome/124.0.0.0"}
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        with patch("gflow_cli.browser_manager.httpx.get", side_effect=mock_get):
            with pytest.raises(ConfigurationError) as exc_info:
                _find_available_cdp_port(profile_dir, start_port=9222)

        assert "9222" in str(exc_info.value) or "ports" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Process detachment flags
# ---------------------------------------------------------------------------


class TestProcessDetachmentFlags:
    def test_spawn_uses_detached_flags_on_windows(self, tmp_path: Path) -> None:
        """On win32, Popen is called with DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP."""
        import subprocess

        from gflow_cli.browser_manager import _spawn_chrome

        profile_dir = _make_profile_dir(tmp_path)
        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with (
            patch("sys.platform", "win32"),
            patch(
                "gflow_cli.browser_manager.subprocess.Popen", return_value=mock_proc
            ) as mock_popen,
        ):
            _spawn_chrome("/custom/chrome.exe", profile_dir, port=9222)

        call_kwargs = mock_popen.call_args[1]
        expected_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        assert call_kwargs.get("creationflags") == expected_flags
        # start_new_session should NOT be set on Windows
        assert call_kwargs.get("start_new_session") is not True

    def test_spawn_uses_start_new_session_on_posix(self, tmp_path: Path) -> None:
        """On linux, Popen is called with start_new_session=True."""
        from gflow_cli.browser_manager import _spawn_chrome

        profile_dir = _make_profile_dir(tmp_path)
        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with (
            patch("sys.platform", "linux"),
            patch(
                "gflow_cli.browser_manager.subprocess.Popen", return_value=mock_proc
            ) as mock_popen,
        ):
            _spawn_chrome("/usr/bin/google-chrome", profile_dir, port=9222)

        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs.get("start_new_session") is True
        # creationflags should NOT be set on POSIX
        assert "creationflags" not in call_kwargs or call_kwargs.get("creationflags") == 0

    def test_spawn_includes_required_chrome_args(self, tmp_path: Path) -> None:
        """Chrome must be spawned with --remote-debugging-port and --user-data-dir."""
        from gflow_cli.browser_manager import _spawn_chrome

        profile_dir = _make_profile_dir(tmp_path)
        mock_proc = MagicMock()
        mock_proc.pid = 99

        with (
            patch("sys.platform", "linux"),
            patch(
                "gflow_cli.browser_manager.subprocess.Popen", return_value=mock_proc
            ) as mock_popen,
        ):
            _spawn_chrome("/usr/bin/google-chrome", profile_dir, port=9222)

        cmd = mock_popen.call_args[0][0]
        cmd_str = " ".join(str(c) for c in cmd)
        assert "--remote-debugging-port=9222" in cmd_str
        assert "--user-data-dir=" in cmd_str


# ---------------------------------------------------------------------------
# Atomic lockfile
# ---------------------------------------------------------------------------


class TestAtomicLockfile:
    @pytest.mark.asyncio
    async def test_atomic_lockfile_prevents_double_spawn(self, tmp_path: Path) -> None:
        """Two concurrent get_or_launch_browser calls → only ONE Popen invocation.

        The asyncio.Lock inside get_or_launch_browser serializes the two calls.
        The second call finds the lockfile written by the first and attaches
        instead of spawning again.
        """
        profile_dir = _make_profile_dir(tmp_path)

        from gflow_cli import browser_manager
        from gflow_cli.browser_manager import get_or_launch_browser

        popen_calls: list[int] = []
        fake_page = AsyncMock()
        fake_page.url = "https://labs.google/fx/tools/flow"
        fake_page.locator = MagicMock(
            return_value=MagicMock(first=MagicMock(is_visible=MagicMock(return_value=False)))
        )
        fake_context = AsyncMock()
        fake_context.new_page = AsyncMock(return_value=fake_page)

        mock_proc = MagicMock()
        mock_proc.pid = 42

        import httpx

        spawned_pid: list[int] = []

        def mock_health(url: str, **kwargs: object) -> object:
            # Chrome appears alive after first spawn has written the lockfile
            if spawned_pid:
                resp = MagicMock()
                resp.json.return_value = {"Browser": "Chrome/124.0.0.0"}
                resp.raise_for_status = MagicMock()
                return resp
            raise httpx.ConnectError("refused")

        def mock_popen(cmd: list, **kwargs: object) -> object:
            popen_calls.append(1)
            spawned_pid.append(42)
            return mock_proc

        async def mock_connect(endpoint: str) -> object:
            return fake_context

        # Reset the module-level spawn lock so this test gets a fresh one
        browser_manager._spawn_lock = asyncio.Lock()

        with (
            patch("gflow_cli.browser_manager.httpx.get", side_effect=mock_health),
            patch("gflow_cli.browser_manager.subprocess.Popen", side_effect=mock_popen),
            patch("gflow_cli.browser_manager._connect_cdp", side_effect=mock_connect),
            patch("gflow_cli.browser_manager._find_chrome_binary", return_value="/usr/bin/chrome"),
            patch("gflow_cli.browser_manager._is_logged_in_to_flow", return_value=True),
            patch(
                "gflow_cli.browser_manager._pid_alive",
                side_effect=lambda pid: pid in spawned_pid,
            ),
            patch("sys.platform", "linux"),
        ):
            await asyncio.gather(
                get_or_launch_browser(profile_dir, port=9222),
                get_or_launch_browser(profile_dir, port=9222),
            )

        assert len(popen_calls) == 1, f"Expected 1 spawn, got {len(popen_calls)}"

    def test_stale_lockfile_is_cleaned_up(self, tmp_path: Path) -> None:
        """Lock points to dead PID → lock is removed and spawn proceeds."""
        profile_dir = _make_profile_dir(tmp_path)
        lock_path = profile_dir / ".gflow-cdp.lock"

        # Write a lock with a dead PID
        dead_pid = 999999999
        lock_path.write_text(json.dumps({"pid": dead_pid, "port": 9222, "profile_name": "test"}))

        from gflow_cli.browser_manager import get_or_launch_browser

        mock_proc = MagicMock()
        mock_proc.pid = 42

        fake_page = AsyncMock()
        fake_page.url = "https://labs.google/fx/tools/flow"
        fake_page.locator = MagicMock(
            return_value=MagicMock(first=MagicMock(is_visible=MagicMock(return_value=False)))
        )
        fake_context = AsyncMock()
        fake_context.new_page = AsyncMock(return_value=fake_page)

        import httpx

        async def mock_connect(endpoint: str) -> object:
            return fake_context

        with (
            patch(
                "gflow_cli.browser_manager.httpx.get",
                side_effect=httpx.ConnectError("refused"),
            ),
            patch(
                "gflow_cli.browser_manager.subprocess.Popen", return_value=mock_proc
            ) as mock_popen,
            patch("gflow_cli.browser_manager._connect_cdp", side_effect=mock_connect),
            patch("gflow_cli.browser_manager._find_chrome_binary", return_value="/usr/bin/chrome"),
            patch("gflow_cli.browser_manager._pid_alive", return_value=False),
            patch("gflow_cli.browser_manager._is_logged_in_to_flow", return_value=True),
            patch("sys.platform", "linux"),
        ):
            asyncio.get_event_loop().run_until_complete(
                get_or_launch_browser(profile_dir, port=9222)
            )

        assert mock_popen.called, "Spawn should have been called after stale lock cleanup"

    def test_lockfile_written_with_pid_and_port(self, tmp_path: Path) -> None:
        """After spawning, lockfile exists and contains pid + port."""
        profile_dir = _make_profile_dir(tmp_path)
        from gflow_cli.browser_manager import get_or_launch_browser

        mock_proc = MagicMock()
        mock_proc.pid = 777

        fake_page = AsyncMock()
        fake_page.url = "https://labs.google/fx/tools/flow"
        fake_page.locator = MagicMock(
            return_value=MagicMock(first=MagicMock(is_visible=MagicMock(return_value=False)))
        )
        fake_context = AsyncMock()
        fake_context.new_page = AsyncMock(return_value=fake_page)

        import httpx

        async def mock_connect(endpoint: str) -> object:
            return fake_context

        with (
            patch("gflow_cli.browser_manager.httpx.get", side_effect=httpx.ConnectError("refused")),
            patch("gflow_cli.browser_manager.subprocess.Popen", return_value=mock_proc),
            patch("gflow_cli.browser_manager._connect_cdp", side_effect=mock_connect),
            patch("gflow_cli.browser_manager._find_chrome_binary", return_value="/usr/bin/chrome"),
            patch("gflow_cli.browser_manager._is_logged_in_to_flow", return_value=True),
            patch("sys.platform", "linux"),
        ):
            asyncio.get_event_loop().run_until_complete(
                get_or_launch_browser(profile_dir, port=9222)
            )

        lock_path = profile_dir / ".gflow-cdp.lock"
        assert lock_path.exists()
        data = json.loads(lock_path.read_text())
        assert data["pid"] == 777
        assert data["port"] == 9222


# ---------------------------------------------------------------------------
# Chrome SingletonLock precheck
# ---------------------------------------------------------------------------


class TestSingletonLockPrecheck:
    def test_singleton_lock_with_live_pid_raises_configuration_error(self, tmp_path: Path) -> None:
        """SingletonLock/lockfile file present + PID alive → ConfigurationError."""
        from gflow_cli.browser_manager import _check_chrome_singleton_lock

        profile_dir = _make_profile_dir(tmp_path)

        # Create a SingletonLock referencing current process PID (definitely alive)
        current_pid = os.getpid()
        lock_file = profile_dir / "SingletonLock"
        lock_file.write_text(f"{current_pid}")  # POSIX format

        with (
            patch("sys.platform", "linux"),
            patch("gflow_cli.browser_manager._pid_alive", return_value=True),
        ):
            with pytest.raises(ConfigurationError) as exc_info:
                _check_chrome_singleton_lock(profile_dir)

        assert "in use" in str(exc_info.value).lower() or "Profile" in str(exc_info.value)
        # Should mention PID or how to resolve
        assert (
            str(current_pid) in str(exc_info.value)
            or "close" in str(exc_info.value).lower()
            or "cdp" in str(exc_info.value).lower()
        )

    def test_singleton_lock_with_dead_pid_does_not_raise(self, tmp_path: Path) -> None:
        """SingletonLock present but PID dead → no error (stale lock, Chrome not running)."""
        from gflow_cli.browser_manager import _check_chrome_singleton_lock

        profile_dir = _make_profile_dir(tmp_path)
        lock_file = profile_dir / "SingletonLock"
        lock_file.write_text("999999999")

        with (
            patch("sys.platform", "linux"),
            patch("gflow_cli.browser_manager._pid_alive", return_value=False),
        ):
            # Should not raise
            _check_chrome_singleton_lock(profile_dir)

    def test_no_singleton_lock_does_not_raise(self, tmp_path: Path) -> None:
        """No lock file → no error."""
        from gflow_cli.browser_manager import _check_chrome_singleton_lock

        profile_dir = _make_profile_dir(tmp_path)
        _check_chrome_singleton_lock(profile_dir)


# ---------------------------------------------------------------------------
# get_or_launch_browser — attach vs spawn
# ---------------------------------------------------------------------------


class TestGetOrLaunchBrowser:
    @pytest.mark.asyncio
    async def test_get_or_launch_attaches_when_chrome_alive(self, tmp_path: Path) -> None:
        """Chrome alive (health check passes) → no Popen called."""
        from gflow_cli.browser_manager import get_or_launch_browser

        profile_dir = _make_profile_dir(tmp_path)

        # Write an existing lockfile with current PID so we skip double-spawn
        lock_path = profile_dir / ".gflow-cdp.lock"
        lock_path.write_text(json.dumps({"pid": os.getpid(), "port": 9222, "profile_name": "test"}))

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"Browser": "Chrome/124.0.0.0"}
        mock_resp.raise_for_status = MagicMock()

        fake_page = AsyncMock()
        fake_page.url = "https://labs.google/fx/tools/flow"
        fake_page.locator = MagicMock(
            return_value=MagicMock(first=MagicMock(is_visible=MagicMock(return_value=False)))
        )
        fake_context = AsyncMock()
        fake_context.new_page = AsyncMock(return_value=fake_page)

        async def mock_connect(endpoint: str) -> object:
            return fake_context

        with (
            patch("gflow_cli.browser_manager.httpx.get", return_value=mock_resp),
            patch("gflow_cli.browser_manager.subprocess.Popen") as mock_popen,
            patch("gflow_cli.browser_manager._connect_cdp", side_effect=mock_connect),
            patch("gflow_cli.browser_manager._pid_alive", return_value=True),
            patch("gflow_cli.browser_manager._is_logged_in_to_flow", return_value=True),
        ):
            result = await get_or_launch_browser(profile_dir, port=9222)

        mock_popen.assert_not_called()
        assert result is fake_context

    @pytest.mark.asyncio
    async def test_get_or_launch_spawns_when_chrome_dead(self, tmp_path: Path) -> None:
        """Chrome dead (ECONNREFUSED) → Popen called with binary + flags."""
        import httpx

        from gflow_cli.browser_manager import get_or_launch_browser

        profile_dir = _make_profile_dir(tmp_path)

        mock_proc = MagicMock()
        mock_proc.pid = 555

        fake_page = AsyncMock()
        fake_page.url = "https://labs.google/fx/tools/flow"
        fake_page.locator = MagicMock(
            return_value=MagicMock(first=MagicMock(is_visible=MagicMock(return_value=False)))
        )
        fake_context = AsyncMock()
        fake_context.new_page = AsyncMock(return_value=fake_page)

        async def mock_connect(endpoint: str) -> object:
            return fake_context

        with (
            patch(
                "gflow_cli.browser_manager.httpx.get",
                side_effect=httpx.ConnectError("refused"),
            ),
            patch(
                "gflow_cli.browser_manager.subprocess.Popen", return_value=mock_proc
            ) as mock_popen,
            patch("gflow_cli.browser_manager._connect_cdp", side_effect=mock_connect),
            patch(
                "gflow_cli.browser_manager._find_chrome_binary",
                return_value="/usr/bin/google-chrome",
            ),
            patch("gflow_cli.browser_manager._is_logged_in_to_flow", return_value=True),
            patch("sys.platform", "linux"),
        ):
            result = await get_or_launch_browser(profile_dir, port=9222)

        mock_popen.assert_called_once()
        assert result is fake_context

    @pytest.mark.asyncio
    async def test_get_or_launch_returns_existing_pid_on_second_call(self, tmp_path: Path) -> None:
        """Call get_or_launch_browser twice; second call attaches (no new spawn)."""
        from gflow_cli.browser_manager import get_or_launch_browser

        profile_dir = _make_profile_dir(tmp_path)

        spawn_count = 0
        mock_proc = MagicMock()
        mock_proc.pid = 42

        fake_page = AsyncMock()
        fake_page.url = "https://labs.google/fx/tools/flow"
        fake_page.locator = MagicMock(
            return_value=MagicMock(first=MagicMock(is_visible=MagicMock(return_value=False)))
        )
        fake_context = AsyncMock()
        fake_context.new_page = AsyncMock(return_value=fake_page)

        import httpx

        health_call_n = 0

        def dynamic_health(url: str, **kwargs: object) -> object:
            nonlocal health_call_n
            health_call_n += 1
            # First call: dead. After spawn writes lock, second call should attach.
            if spawn_count == 0 and health_call_n <= 2:
                raise httpx.ConnectError("refused")
            resp = MagicMock()
            resp.json.return_value = {"Browser": "Chrome/124.0.0.0"}
            resp.raise_for_status = MagicMock()
            return resp

        def mock_spawn_popen(cmd: list, **kwargs: object) -> object:
            nonlocal spawn_count
            spawn_count += 1
            return mock_proc

        async def mock_connect(endpoint: str) -> object:
            return fake_context

        with (
            patch("gflow_cli.browser_manager.httpx.get", side_effect=dynamic_health),
            patch("gflow_cli.browser_manager.subprocess.Popen", side_effect=mock_spawn_popen),
            patch("gflow_cli.browser_manager._connect_cdp", side_effect=mock_connect),
            patch("gflow_cli.browser_manager._find_chrome_binary", return_value="/usr/bin/chrome"),
            patch("gflow_cli.browser_manager._is_logged_in_to_flow", return_value=True),
            patch("gflow_cli.browser_manager._pid_alive", side_effect=lambda pid: pid == 42),
            patch("sys.platform", "linux"),
        ):
            await get_or_launch_browser(profile_dir, port=9222)
            await get_or_launch_browser(profile_dir, port=9222)

        assert spawn_count == 1, f"Expected 1 spawn total across 2 calls, got {spawn_count}"


# ---------------------------------------------------------------------------
# Pre-spawn logged-in check
# ---------------------------------------------------------------------------


class TestPreSpawnLoggedInCheck:
    @pytest.mark.asyncio
    async def test_pre_spawn_logged_in_check_passes_for_authenticated(self, tmp_path: Path) -> None:
        """Page on Flow URL (not accounts.google.com) + no Sign-in CTA → no error."""
        from gflow_cli.browser_manager import get_or_launch_browser

        profile_dir = _make_profile_dir(tmp_path)
        lock_path = profile_dir / ".gflow-cdp.lock"
        lock_path.write_text(json.dumps({"pid": os.getpid(), "port": 9222, "profile_name": "test"}))

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"Browser": "Chrome/124.0.0.0"}
        mock_resp.raise_for_status = MagicMock()

        # Page looks authenticated
        fake_page = AsyncMock()
        fake_page.url = "https://labs.google/fx/tools/flow"  # not accounts.google.com
        fake_page.locator = MagicMock(
            return_value=MagicMock(first=MagicMock(is_visible=MagicMock(return_value=False)))
        )
        fake_context = AsyncMock()
        fake_context.new_page = AsyncMock(return_value=fake_page)

        async def mock_connect(endpoint: str) -> object:
            return fake_context

        with (
            patch("gflow_cli.browser_manager.httpx.get", return_value=mock_resp),
            patch("gflow_cli.browser_manager._connect_cdp", side_effect=mock_connect),
            patch("gflow_cli.browser_manager._pid_alive", return_value=True),
            patch("gflow_cli.browser_manager._is_logged_in_to_flow", return_value=True),
        ):
            result = await get_or_launch_browser(profile_dir, port=9222)

        assert result is fake_context

    @pytest.mark.asyncio
    async def test_pre_spawn_logged_in_check_raises_auth_missing(self, tmp_path: Path) -> None:
        """Page redirects to accounts.google.com → AuthMissingError."""
        from gflow_cli.browser_manager import get_or_launch_browser

        profile_dir = _make_profile_dir(tmp_path)
        lock_path = profile_dir / ".gflow-cdp.lock"
        lock_path.write_text(json.dumps({"pid": os.getpid(), "port": 9222, "profile_name": "test"}))

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"Browser": "Chrome/124.0.0.0"}
        mock_resp.raise_for_status = MagicMock()

        fake_page = AsyncMock()
        fake_page.url = "https://accounts.google.com/signin/..."
        fake_page.locator = MagicMock(
            return_value=MagicMock(first=MagicMock(is_visible=MagicMock(return_value=True)))
        )
        fake_context = AsyncMock()
        fake_context.new_page = AsyncMock(return_value=fake_page)

        async def mock_connect(endpoint: str) -> object:
            return fake_context

        with (
            patch("gflow_cli.browser_manager.httpx.get", return_value=mock_resp),
            patch("gflow_cli.browser_manager._connect_cdp", side_effect=mock_connect),
            patch("gflow_cli.browser_manager._pid_alive", return_value=True),
            patch("gflow_cli.browser_manager._is_logged_in_to_flow", return_value=False),
        ):
            with pytest.raises(AuthMissingError) as exc_info:
                await get_or_launch_browser(profile_dir, port=9222)

        assert "gflow auth login" in str(exc_info.value)

    def test_is_logged_in_to_flow_returns_false_for_signin_redirect(self) -> None:
        """_is_logged_in_to_flow returns False when page is on accounts.google.com."""
        from gflow_cli.browser_manager import _is_logged_in_to_flow

        fake_page = MagicMock()
        fake_page.url = "https://accounts.google.com/signin/v2/identifier"
        fake_page.locator = MagicMock(
            return_value=MagicMock(first=MagicMock(is_visible=MagicMock(return_value=True)))
        )

        result = _is_logged_in_to_flow(fake_page)
        assert result is False

    def test_is_logged_in_to_flow_returns_true_for_flow_url(self) -> None:
        """_is_logged_in_to_flow returns True when page is on Flow and no sign-in CTA."""
        from gflow_cli.browser_manager import _is_logged_in_to_flow

        fake_page = MagicMock()
        fake_page.url = "https://labs.google/fx/tools/flow"
        fake_page.locator = MagicMock(
            return_value=MagicMock(first=MagicMock(is_visible=MagicMock(return_value=False)))
        )

        result = _is_logged_in_to_flow(fake_page)
        assert result is True


# ---------------------------------------------------------------------------
# close_browser
# ---------------------------------------------------------------------------


class TestCloseBrowser:
    @pytest.mark.asyncio
    async def test_close_browser_removes_lockfile(self, tmp_path: Path) -> None:
        """close_browser removes the lockfile for the given port."""
        from gflow_cli.browser_manager import close_browser

        profile_dir = _make_profile_dir(tmp_path)
        lock_path = profile_dir / ".gflow-cdp.lock"
        lock_path.write_text(json.dumps({"pid": os.getpid(), "port": 9222, "profile_name": "test"}))

        await close_browser(profile_dir, port=9222)

        assert not lock_path.exists()

    @pytest.mark.asyncio
    async def test_close_browser_no_error_when_no_lockfile(self, tmp_path: Path) -> None:
        """close_browser is a no-op when no lockfile exists."""
        from gflow_cli.browser_manager import close_browser

        profile_dir = _make_profile_dir(tmp_path)
        # Should not raise
        await close_browser(profile_dir, port=9222)

    @pytest.mark.asyncio
    async def test_close_browser_different_port_removes_lock_anyway(self, tmp_path: Path) -> None:
        """close_browser on a different port still removes whatever lock exists (else branch)."""
        from gflow_cli.browser_manager import close_browser

        profile_dir = _make_profile_dir(tmp_path)
        lock_path = profile_dir / ".gflow-cdp.lock"
        lock_path.write_text(json.dumps({"pid": os.getpid(), "port": 9223, "profile_name": "test"}))

        await close_browser(profile_dir, port=9222)

        assert not lock_path.exists()


# ---------------------------------------------------------------------------
# _pid_alive — platform-specific branches
# ---------------------------------------------------------------------------


class TestPidAlive:
    def test_pid_alive_returns_true_for_current_pid_on_posix(self) -> None:
        """Current process PID → alive on POSIX."""
        from gflow_cli.browser_manager import _pid_alive

        with patch("sys.platform", "linux"):
            result = _pid_alive(os.getpid())

        assert result is True

    def test_pid_alive_returns_false_for_nonexistent_pid_on_posix(self) -> None:
        """Very large PID that doesn't exist → False on POSIX."""
        from gflow_cli.browser_manager import _pid_alive

        with patch("sys.platform", "linux"):
            result = _pid_alive(999999999)

        assert result is False

    def test_pid_alive_permission_error_means_alive_on_posix(self) -> None:
        """PermissionError from os.kill → process exists, return True."""
        from gflow_cli.browser_manager import _pid_alive

        with (
            patch("sys.platform", "linux"),
            patch("gflow_cli.browser_manager.os.kill", side_effect=PermissionError()),
        ):
            result = _pid_alive(1234)

        assert result is True

    def test_pid_alive_other_exception_returns_false_on_posix(self) -> None:
        """Unexpected exception from os.kill → False (never raises)."""
        from gflow_cli.browser_manager import _pid_alive

        with (
            patch("sys.platform", "linux"),
            patch("gflow_cli.browser_manager.os.kill", side_effect=OSError("unexpected")),
        ):
            result = _pid_alive(1234)

        assert result is False

    def test_pid_alive_on_windows_found(self) -> None:
        """tasklist output contains PID → True."""
        from gflow_cli.browser_manager import _pid_alive

        mock_result = MagicMock()
        mock_result.stdout = '"chrome.exe","1234","Console","1","50,000 K"'

        with (
            patch("sys.platform", "win32"),
            patch("gflow_cli.browser_manager.subprocess.run", return_value=mock_result),
        ):
            result = _pid_alive(1234)

        assert result is True

    def test_pid_alive_on_windows_not_found(self) -> None:
        """tasklist output doesn't contain PID → False."""
        from gflow_cli.browser_manager import _pid_alive

        mock_result = MagicMock()
        mock_result.stdout = "INFO: No tasks are running which match the specified criteria."

        with (
            patch("sys.platform", "win32"),
            patch("gflow_cli.browser_manager.subprocess.run", return_value=mock_result),
        ):
            result = _pid_alive(1234)

        assert result is False

    def test_pid_alive_on_windows_exception_returns_false(self) -> None:
        """Exception from subprocess.run → False."""
        from gflow_cli.browser_manager import _pid_alive

        with (
            patch("sys.platform", "win32"),
            patch("gflow_cli.browser_manager.subprocess.run", side_effect=OSError("fail")),
        ):
            result = _pid_alive(1234)

        assert result is False


# ---------------------------------------------------------------------------
# Chrome binary — macOS and Linux platform paths
# ---------------------------------------------------------------------------


class TestChromeBinaryPlatformPaths:
    def test_chrome_binary_macos_path(self, tmp_path: Path) -> None:
        """On darwin, /Applications/Google Chrome.app path is tried."""
        from gflow_cli.browser_manager import _find_chrome_binary

        env_without = {k: v for k, v in os.environ.items() if k != "CHROME_BINARY"}

        def path_exists_mock(self: Path) -> bool:
            # Match any path containing the macOS Chrome bundle directory
            return "Google Chrome.app" in str(self)

        with (
            patch.dict(os.environ, env_without, clear=True),
            patch("sys.platform", "darwin"),
            patch("gflow_cli.browser_manager.shutil.which", return_value=None),
            patch.object(Path, "exists", path_exists_mock),
        ):
            result = _find_chrome_binary()

        assert "Google Chrome" in result

    def test_chrome_binary_linux_path(self, tmp_path: Path) -> None:
        """On linux, /usr/bin/google-chrome path is tried."""
        from gflow_cli.browser_manager import _find_chrome_binary

        env_without = {k: v for k, v in os.environ.items() if k != "CHROME_BINARY"}

        def path_exists_mock(self: Path) -> bool:
            # Match the first linux path candidate
            return "google-chrome" in str(self)

        with (
            patch.dict(os.environ, env_without, clear=True),
            patch("sys.platform", "linux"),
            patch("gflow_cli.browser_manager.shutil.which", return_value=None),
            patch.object(Path, "exists", path_exists_mock),
        ):
            result = _find_chrome_binary()

        assert "google-chrome" in result


# ---------------------------------------------------------------------------
# Lockfile helpers — edge cases
# ---------------------------------------------------------------------------


class TestLockfileHelpers:
    def test_read_lock_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        """_read_lock returns None when file absent."""
        from gflow_cli.browser_manager import _read_lock

        result = _read_lock(tmp_path / "nonexistent.lock")
        assert result is None

    def test_read_lock_returns_none_for_invalid_json(self, tmp_path: Path) -> None:
        """_read_lock returns None when content is not valid JSON."""
        from gflow_cli.browser_manager import _read_lock

        p = tmp_path / "bad.lock"
        p.write_text("not-json!!!")
        result = _read_lock(p)
        assert result is None

    def test_remove_lock_no_error_when_missing(self, tmp_path: Path) -> None:
        """_remove_lock is a no-op if file doesn't exist."""
        from gflow_cli.browser_manager import _remove_lock

        _remove_lock(tmp_path / "ghost.lock")  # should not raise

    def test_write_lock_raises_file_exists_on_duplicate(self, tmp_path: Path) -> None:
        """_write_lock raises FileExistsError on second call (atomic O_EXCL)."""
        from gflow_cli.browser_manager import _write_lock

        lock = tmp_path / "test.lock"
        _write_lock(lock, pid=1, port=9222, profile_name="x")

        with pytest.raises(FileExistsError):
            _write_lock(lock, pid=2, port=9223, profile_name="y")


# ---------------------------------------------------------------------------
# get_or_launch_browser — attach path without existing lockfile
# ---------------------------------------------------------------------------


class TestGetOrLaunchBrowserNoLockAttach:
    @pytest.mark.asyncio
    async def test_attaches_when_chrome_running_but_no_lockfile(self, tmp_path: Path) -> None:
        """Chrome is already running but no lockfile exists → attach without spawn."""

        from gflow_cli.browser_manager import get_or_launch_browser

        profile_dir = _make_profile_dir(tmp_path)
        # No lockfile — but Chrome is alive

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"Browser": "Chrome/124.0.0.0"}
        mock_resp.raise_for_status = MagicMock()

        fake_page = AsyncMock()
        fake_page.url = "https://labs.google/fx/tools/flow"
        fake_page.locator = MagicMock(
            return_value=MagicMock(first=MagicMock(is_visible=MagicMock(return_value=False)))
        )
        fake_context = AsyncMock()
        fake_context.new_page = AsyncMock(return_value=fake_page)

        async def mock_connect(endpoint: str) -> object:
            return fake_context

        with (
            patch("gflow_cli.browser_manager.httpx.get", return_value=mock_resp),
            patch("gflow_cli.browser_manager.subprocess.Popen") as mock_popen,
            patch("gflow_cli.browser_manager._connect_cdp", side_effect=mock_connect),
            patch("gflow_cli.browser_manager._is_logged_in_to_flow", return_value=True),
        ):
            result = await get_or_launch_browser(profile_dir, port=9222)

        mock_popen.assert_not_called()
        assert result is fake_context

    @pytest.mark.asyncio
    async def test_raises_auth_missing_when_no_lockfile_not_logged_in(self, tmp_path: Path) -> None:
        """Chrome alive, no lockfile, but not logged in → AuthMissingError."""
        from gflow_cli.browser_manager import get_or_launch_browser

        profile_dir = _make_profile_dir(tmp_path)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"Browser": "Chrome/124.0.0.0"}
        mock_resp.raise_for_status = MagicMock()

        fake_page = AsyncMock()
        fake_page.url = "https://accounts.google.com/signin"
        fake_page.locator = MagicMock(
            return_value=MagicMock(first=MagicMock(is_visible=MagicMock(return_value=True)))
        )
        fake_context = AsyncMock()
        fake_context.new_page = AsyncMock(return_value=fake_page)

        async def mock_connect(endpoint: str) -> object:
            return fake_context

        with (
            patch("gflow_cli.browser_manager.httpx.get", return_value=mock_resp),
            patch("gflow_cli.browser_manager._connect_cdp", side_effect=mock_connect),
            patch("gflow_cli.browser_manager._is_logged_in_to_flow", return_value=False),
        ):
            with pytest.raises(AuthMissingError):
                await get_or_launch_browser(profile_dir, port=9222)
