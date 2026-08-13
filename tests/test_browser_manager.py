"""Tests for gflow_cli.browser_manager — Chrome discovery/channel helpers only.

The packaged CDP (Chrome DevTools Protocol) attach/spawn lifecycle
(``get_or_launch_browser`` / ``close_browser`` / lockfile helpers) and its
14 test classes were removed 2026-07-19 — see
``.superpowers/sdd/cdp-decision.md`` in the
``chore/production-readiness-hardening`` history for the evidence-based
removal decision. Only the Chrome-discovery/channel surface remains, both
in ``src/gflow_cli/browser_manager.py`` and here.

Run with:
    uv run python -m pytest tests/test_browser_manager.py -v \
        --cov=src/gflow_cli/browser_manager --cov-report=term-missing
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from gflow_cli.errors import ConfigurationError

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

    def test_resolved_chrome_binary_swallows_non_configuration_errors(self) -> None:
        """resolved_chrome_binary is exception-free by contract: a non-ConfigurationError
        from _find_chrome_binary (e.g. shutil.which raising AttributeError under an
        OS-detection mismatch — faking sys.platform='win32' on Linux) must resolve to
        None, never propagate. It feeds the _log_and_guard_launch diagnostic and must
        not abort a launch."""
        from gflow_cli.browser_manager import resolved_chrome_binary

        with patch(
            "gflow_cli.browser_manager._find_chrome_binary",
            side_effect=AttributeError("NeedCurrentDirectoryForExePath"),
        ):
            assert resolved_chrome_binary() is None


class TestPlaywrightChromeChannelAvailable:
    """Gate for Playwright's ``channel="chrome"`` — Chromium must NOT satisfy it.

    ``launch_persistent_context(channel="chrome")`` resolves to hardcoded
    Google-Chrome paths; a plain Chromium binary does not satisfy it. The gate
    therefore must ignore ``shutil.which`` (which would find Chromium) and probe
    only the exact Google-Chrome paths.
    """

    def test_chromium_only_host_returns_false(self) -> None:
        """Chromium on PATH but no Google Chrome at Playwright's paths → False."""
        from gflow_cli.browser_manager import _is_playwright_chrome_channel_available

        env_without = {k: v for k, v in os.environ.items() if k != "CHROME_BINARY"}
        with (
            patch.dict(os.environ, env_without, clear=True),
            patch("sys.platform", "linux"),
            # Chromium IS discoverable, but the gate must ignore which() entirely.
            patch("gflow_cli.browser_manager.shutil.which", return_value="/usr/bin/chromium"),
            patch.object(Path, "exists", return_value=False),
        ):
            assert _is_playwright_chrome_channel_available() is False

    def test_returns_true_when_google_chrome_present(self) -> None:
        """Google Chrome at Playwright's expected path → True."""
        from gflow_cli.browser_manager import _is_playwright_chrome_channel_available

        env_without = {k: v for k, v in os.environ.items() if k != "CHROME_BINARY"}
        with (
            patch.dict(os.environ, env_without, clear=True),
            patch("sys.platform", "linux"),
            patch.object(Path, "exists", return_value=True),
        ):
            assert _is_playwright_chrome_channel_available() is True

    def test_env_override_returns_true(self, tmp_path: Path) -> None:
        """CHROME_BINARY override is honoured for parity with _find_chrome_binary."""
        from gflow_cli.browser_manager import _is_playwright_chrome_channel_available

        with patch.dict(os.environ, {"CHROME_BINARY": str(tmp_path / "chrome")}):
            assert _is_playwright_chrome_channel_available() is True

    def test_win32_probes_program_files_chrome(self) -> None:
        """On win32, the Program Files Google-Chrome path is probed → True when present."""
        from gflow_cli.browser_manager import _is_playwright_chrome_channel_available

        expected = "C:/Program Files/Google/Chrome/Application/chrome.exe"
        env_without = {
            k: v for k, v in os.environ.items() if k not in ("CHROME_BINARY", "LOCALAPPDATA")
        }
        env_without["LOCALAPPDATA"] = str(Path(tempfile.gettempdir()) / "nonexistent_la")

        def path_exists_mock(self: Path) -> bool:
            return str(self).replace("\\", "/") == expected

        with (
            patch.dict(os.environ, env_without, clear=True),
            patch("sys.platform", "win32"),
            patch.object(Path, "exists", path_exists_mock),
        ):
            assert _is_playwright_chrome_channel_available() is True

    def test_darwin_probes_app_bundle(self) -> None:
        """On darwin, the /Applications Google Chrome.app path is probed."""
        from gflow_cli.browser_manager import _is_playwright_chrome_channel_available

        env_without = {k: v for k, v in os.environ.items() if k != "CHROME_BINARY"}
        with (
            patch.dict(os.environ, env_without, clear=True),
            patch("sys.platform", "darwin"),
            patch.object(Path, "exists", return_value=True),
        ):
            assert _is_playwright_chrome_channel_available() is True


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
# #477: profile-engine (Chromium version) downgrade guard
# ---------------------------------------------------------------------------


class TestProfileEngineDowngradeGuard:
    """Refuse to open a persisted profile with an older bundled Chromium than
    last wrote it (#477). Chromium's downgrade cleanup can leave the newer
    store — session cookies included — unreadable, surfacing as a mystery
    post-upgrade logout. Best-effort: any unknown version resolves to allow."""

    def test_profile_last_version_reads_file(self, tmp_path: Path) -> None:
        from gflow_cli.browser_manager import profile_last_version

        (tmp_path / "Last Version").write_text("142.0.7444.52\n", encoding="utf-8")
        assert profile_last_version(tmp_path) == "142.0.7444.52"

    def test_profile_last_version_absent_or_blank_is_none(self, tmp_path: Path) -> None:
        from gflow_cli.browser_manager import profile_last_version

        assert profile_last_version(tmp_path) is None
        (tmp_path / "Last Version").write_text("  \n", encoding="utf-8")
        assert profile_last_version(tmp_path) is None

    def test_installed_chromium_version_reads_playwright_registry(self) -> None:
        """The pinned Playwright must yield a dotted Chromium version from its
        driver browsers.json — pins the registry contract the guard reads."""
        from gflow_cli.browser_manager import installed_chromium_version

        version = installed_chromium_version()
        assert version is not None
        parts = version.split(".")
        assert len(parts) >= 2
        assert all(part.isdigit() for part in parts)

    def test_guard_raises_on_downgrade_naming_versions_and_remedy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import gflow_cli.browser_manager as bm
        from gflow_cli.errors import ProfileEngineDowngradeError

        (tmp_path / "Last Version").write_text("999.0.0.0", encoding="utf-8")
        monkeypatch.setattr(bm, "installed_chromium_version", lambda: "149.0.7827.55")
        with pytest.raises(ProfileEngineDowngradeError) as excinfo:
            bm.ensure_profile_engine_compatible(tmp_path, channel=None)
        message = str(excinfo.value)
        assert "999.0.0.0" in message
        assert "149.0.7827.55" in message
        assert "gflow auth login" in message

    def test_guard_error_is_configuration_error_exit_11(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No new exit code: like ProfileLockedError, the guard inherits
        ConfigurationError's 11 via the EXIT_CODE_MAP isinstance walk."""
        import gflow_cli.browser_manager as bm
        from gflow_cli.errors import ProfileEngineDowngradeError
        from gflow_cli.json_output import exit_code_for

        (tmp_path / "Last Version").write_text("999.0.0.0", encoding="utf-8")
        monkeypatch.setattr(bm, "installed_chromium_version", lambda: "149.0.7827.55")
        with pytest.raises(ProfileEngineDowngradeError) as excinfo:
            bm.ensure_profile_engine_compatible(tmp_path, channel=None)
        assert isinstance(excinfo.value, ConfigurationError)
        assert exit_code_for(excinfo.value) == 11

    def test_guard_allows_equal_and_newer_engine(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import gflow_cli.browser_manager as bm

        (tmp_path / "Last Version").write_text("149.0.7827.55", encoding="utf-8")
        monkeypatch.setattr(bm, "installed_chromium_version", lambda: "149.0.7827.55")
        bm.ensure_profile_engine_compatible(tmp_path, channel=None)  # equal: no raise
        monkeypatch.setattr(bm, "installed_chromium_version", lambda: "150.0.1.1")
        bm.ensure_profile_engine_compatible(tmp_path, channel=None)  # newer: no raise

    def test_guard_skipped_for_chrome_channel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """channel='chrome' means real Google Chrome opens the profile — it
        manages its own version lifecycle; the bundled-engine compare is moot."""
        import gflow_cli.browser_manager as bm

        (tmp_path / "Last Version").write_text("999.0.0.0", encoding="utf-8")
        monkeypatch.setattr(bm, "installed_chromium_version", lambda: "149.0.7827.55")
        bm.ensure_profile_engine_compatible(tmp_path, channel="chrome")  # no raise

    def test_guard_allows_unknown_or_unparseable_versions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import gflow_cli.browser_manager as bm

        # No Last Version file at all -> allow.
        monkeypatch.setattr(bm, "installed_chromium_version", lambda: "149.0.7827.55")
        bm.ensure_profile_engine_compatible(tmp_path, channel=None)
        # Unparseable profile version -> allow.
        (tmp_path / "Last Version").write_text("not-a-version", encoding="utf-8")
        bm.ensure_profile_engine_compatible(tmp_path, channel=None)
        # Engine version unknown -> allow.
        (tmp_path / "Last Version").write_text("999.0.0.0", encoding="utf-8")
        monkeypatch.setattr(bm, "installed_chromium_version", lambda: None)
        bm.ensure_profile_engine_compatible(tmp_path, channel=None)
