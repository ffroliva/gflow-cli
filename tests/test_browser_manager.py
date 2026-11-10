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
from collections.abc import Iterator
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

    @pytest.mark.sharpit(allow_swallowed=True)  # the swallow is the contract under test
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
        from gflow_cli.browser_manager import is_playwright_chrome_channel_available

        env_without = {k: v for k, v in os.environ.items() if k != "CHROME_BINARY"}
        with (
            patch.dict(os.environ, env_without, clear=True),
            patch("sys.platform", "linux"),
            # Chromium IS discoverable, but the gate must ignore which() entirely.
            patch("gflow_cli.browser_manager.shutil.which", return_value="/usr/bin/chromium"),
            patch.object(Path, "exists", return_value=False),
        ):
            assert is_playwright_chrome_channel_available() is False

    def test_returns_true_when_google_chrome_present(self) -> None:
        """Google Chrome at Playwright's expected path → True."""
        from gflow_cli.browser_manager import is_playwright_chrome_channel_available

        env_without = {k: v for k, v in os.environ.items() if k != "CHROME_BINARY"}
        with (
            patch.dict(os.environ, env_without, clear=True),
            patch("sys.platform", "linux"),
            patch.object(Path, "exists", return_value=True),
        ):
            assert is_playwright_chrome_channel_available() is True

    def test_env_override_alone_returns_false(self, tmp_path: Path) -> None:
        """CHROME_BINARY set but no Google Chrome at Playwright's paths → False.

        Playwright's ``channel="chrome"`` ignores ``CHROME_BINARY`` (only
        ``executable_path=`` honours a custom binary), so treating the env var as
        proof of a resolvable channel passed the gate and then failed at launch.
        """
        from gflow_cli.browser_manager import is_playwright_chrome_channel_available

        with (
            patch.dict(os.environ, {"CHROME_BINARY": str(tmp_path / "chrome")}),
            patch("sys.platform", "linux"),
            patch.object(Path, "exists", return_value=False),
        ):
            assert is_playwright_chrome_channel_available() is False

    def test_env_override_does_not_mask_a_real_chrome(self, tmp_path: Path) -> None:
        """CHROME_BINARY is ignored, not inverted: real Chrome present still → True."""
        from gflow_cli.browser_manager import is_playwright_chrome_channel_available

        with (
            patch.dict(os.environ, {"CHROME_BINARY": str(tmp_path / "chrome")}),
            patch("sys.platform", "linux"),
            patch.object(Path, "exists", return_value=True),
        ):
            assert is_playwright_chrome_channel_available() is True

    def test_win32_probes_program_files_chrome(self) -> None:
        """On win32, the Program Files Google-Chrome path is probed → True when present."""
        from gflow_cli.browser_manager import is_playwright_chrome_channel_available

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
            assert is_playwright_chrome_channel_available() is True

    def test_darwin_probes_app_bundle(self) -> None:
        """On darwin, the /Applications Google Chrome.app path is probed."""
        from gflow_cli.browser_manager import is_playwright_chrome_channel_available

        env_without = {k: v for k, v in os.environ.items() if k != "CHROME_BINARY"}
        with (
            patch.dict(os.environ, env_without, clear=True),
            patch("sys.platform", "darwin"),
            patch.object(Path, "exists", return_value=True),
        ):
            assert is_playwright_chrome_channel_available() is True

    def test_public_name_is_what_channel_for_profile_gates_on(self, tmp_path: Path) -> None:
        """The predicate is public API, and ``channel_for_profile`` routes through it.

        ``factory.py`` needs to gate strategy selection on the same predicate, so it
        must be importable without reaching for a private name.
        """
        import gflow_cli.browser_manager as bm

        assert callable(bm.is_playwright_chrome_channel_available)
        (tmp_path / ".gflow_browser_strategy").write_text("chrome", encoding="utf-8")

        with patch.object(bm, "is_playwright_chrome_channel_available", return_value=True):
            assert bm.channel_for_profile(tmp_path) == "chrome"
        with patch.object(bm, "is_playwright_chrome_channel_available", return_value=False):
            assert bm.channel_for_profile(tmp_path) is None


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
        # Remedy comes from the class default (not the raise-site message —
        # naming it in both printed the remedy twice in the human output).
        assert "gflow auth login" in type(excinfo.value)._default_remediation  # noqa: SLF001

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

    def test_profile_last_version_invalid_utf8_is_none(self, tmp_path: Path) -> None:
        """Best-effort contract: a corrupt (non-UTF-8) Last Version resolves to
        None/allow instead of escaping as UnicodeDecodeError (code-review F4)."""
        from gflow_cli.browser_manager import profile_last_version

        (tmp_path / "Last Version").write_bytes(b"\xff\xfe\x99garbage")
        assert profile_last_version(tmp_path) is None

    def test_guard_allows_same_major_build_rollback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The destructive downgrade cleanup is milestone-keyed; a same-major
        build/patch rollback (e.g. pinning Playwright back one patch release)
        must NOT brick generation (code-review F9/F10)."""
        import gflow_cli.browser_manager as bm

        (tmp_path / "Last Version").write_text("149.0.9999.99", encoding="utf-8")
        monkeypatch.setattr(bm, "installed_chromium_version", lambda: "149.0.7827.55")
        bm.ensure_profile_engine_compatible(tmp_path, channel=None)  # no raise

    def test_installed_chromium_version_resolves_active_engine(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Under GFLOW_CLI_BROWSER_ENGINE=patchright the guard must read
        patchright's registry — the Chromium that will actually open the
        profile — not playwright's (code-review F2)."""
        import json as _json
        import sys as _sys
        import types

        import gflow_cli.browser_manager as bm
        from gflow_cli.config import BrowserEngine

        fake_pkg_dir = tmp_path / "patchright"
        (fake_pkg_dir / "driver" / "package").mkdir(parents=True)
        (fake_pkg_dir / "driver" / "package" / "browsers.json").write_text(
            _json.dumps({"browsers": [{"name": "chromium", "browserVersion": "131.0.0.1"}]}),
            encoding="utf-8",
        )
        fake_module = types.ModuleType("patchright")
        fake_module.__file__ = str(fake_pkg_dir / "__init__.py")
        monkeypatch.setitem(_sys.modules, "patchright", fake_module)
        monkeypatch.setattr("gflow_cli.api._engine.active_engine", lambda: BrowserEngine.PATCHRIGHT)
        assert bm.installed_chromium_version() == "131.0.0.1"


class TestLaunchSitesGuarded:
    def test_every_bundled_launch_site_calls_engine_guard(self) -> None:
        """Static sweep (code-review F6): every module in src/ that calls
        launch_persistent_context must also reference
        ensure_profile_engine_compatible, or sit on the explicit allowlist.
        Pins future launch sites to the #477 guard without coupling the guard
        into ProfileLease (lease = mutual exclusion, guard = engine compat)."""
        import gflow_cli

        src = Path(gflow_cli.__file__).parent
        allowlist = {
            # Login rewrites the profile and re-mints the session — it IS the
            # documented recovery path for a downgrade refusal.
            "auth/internal_chromium.py",
            "auth/real_chrome.py",
            # Chrome-marker-gated fallback: only ever launches channel="chrome".
            "auth/cookies.py",
        }
        offenders = []
        for py in src.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            if "launch_persistent_context" not in text:
                continue
            rel = py.relative_to(src).as_posix()
            if rel in allowlist:
                continue
            if "ensure_profile_engine_compatible" not in text:
                offenders.append(rel)
        assert not offenders, (
            f"launch_persistent_context call sites missing the #477 engine guard: {offenders}"
        )


# ---------------------------------------------------------------------------
# One merged --disable-features switch (#1140)
# ---------------------------------------------------------------------------

_BUNDLE_SHAPE = """
    disabledFeatures = [
      // See https://github.com/microsoft/playwright/issues/14047
      "AvoidUnnecessaryBeforeUnloadCheckSync",
      "HttpsUpgrades",
      // a comment with "quotes" in it
      "Translate"
    ].filter(Boolean);
    chromiumSwitches = (options) => [
      "--disable-features=" + disabledFeatures.join(","),
    ];
"""


class TestDisableFeaturesArgs:
    """Chrome honours only the LAST --disable-features switch, and Playwright passes
    its own list. A second switch would silently re-enable Playwright's (measured in
    #1140: http upgraded to https), so ours must be ONE switch carrying both lists."""

    @pytest.fixture(autouse=True)
    def _fresh_cache(self) -> Iterator[None]:
        import gflow_cli.browser_manager as bm

        bm._DISABLE_FEATURES_CACHE.clear()  # noqa: SLF001
        yield
        bm._DISABLE_FEATURES_CACHE.clear()  # noqa: SLF001

    def _with_bundle(self, monkeypatch: pytest.MonkeyPatch, path: Path) -> tuple[str, ...]:
        import gflow_cli.browser_manager as bm

        monkeypatch.setattr(bm, "_engine_bundle", lambda: path)
        return bm.disable_features_args()

    def test_one_switch_keeps_playwrights_features_and_adds_ours(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bundle = tmp_path / "coreBundle.js"
        bundle.write_text(_BUNDLE_SHAPE, encoding="utf-8")
        assert self._with_bundle(monkeypatch, bundle) == (
            "--disable-features=AvoidUnnecessaryBeforeUnloadCheckSync,HttpsUpgrades,"
            "Translate,CloudSyncedFolderChecker",
        )

    @pytest.mark.parametrize(
        "text",
        [
            "",  # list gone
            _BUNDLE_SHAPE.replace('"--disable-features=" + ', ""),  # list no longer used so
            _BUNDLE_SHAPE.replace('"Translate"', 'isMac ? "Translate" : ""'),  # not plain
            _BUNDLE_SHAPE.replace('"HttpsUpgrades",', "")
            .replace('"AvoidUnnecessaryBeforeUnloadCheckSync",', "")
            .replace('"Translate"', ""),  # empty
        ],
    )
    def test_unreadable_list_leaves_the_switch_out(
        self, text: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bundle = tmp_path / "coreBundle.js"
        bundle.write_text(text, encoding="utf-8")
        assert self._with_bundle(monkeypatch, bundle) == ()

    def test_missing_bundle_leaves_the_switch_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert self._with_bundle(monkeypatch, tmp_path / "absent.js") == ()

    def test_non_utf8_bundle_leaves_the_switch_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A truncated or corrupt bundle must degrade, not crash every launch."""
        bundle = tmp_path / "coreBundle.js"
        bundle.write_bytes(_BUNDLE_SHAPE.encode("utf-8")[:40] + b"\xff\xfe\x80")
        assert self._with_bundle(monkeypatch, bundle) == ()

    def test_a_failed_read_is_not_cached(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A transient lock on the first read must not cost a long-lived MCP server
        the slow close for the rest of its life."""
        bundle = tmp_path / "coreBundle.js"
        assert self._with_bundle(monkeypatch, bundle) == ()
        bundle.write_text(_BUNDLE_SHAPE, encoding="utf-8")
        assert self._with_bundle(monkeypatch, bundle) != ()

    @pytest.mark.parametrize(
        ("engine", "features", "expected"),
        [
            ("playwright", '"MediaRouter", "Translate"', "MediaRouter,Translate"),
            ("patchright", '"Translate"', "Translate"),
        ],
    )
    def test_the_active_engines_own_list_is_merged(
        self,
        engine: str,
        features: str,
        expected: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Patchright drops MediaRouter for stealth; merging Playwright's list over it
        would put MediaRouter back on the reCAPTCHA-scored path."""
        import sys
        import types

        import gflow_cli.api._engine as engine_mod
        import gflow_cli.browser_manager as bm
        from gflow_cli.config import BrowserEngine

        old_list = _BUNDLE_SHAPE[_BUNDLE_SHAPE.index("[") + 1 : _BUNDLE_SHAPE.index("].filter")]
        for name, listed in (("playwright", '"Bogus"'), ("patchright", '"Bogus"')):
            lib = tmp_path / name / "driver" / "package" / "lib"
            lib.mkdir(parents=True)
            text = _BUNDLE_SHAPE.replace(old_list, features if name == engine else listed)
            (lib / "coreBundle.js").write_text(text, encoding="utf-8")
            module = types.ModuleType(name)
            module.__file__ = str(tmp_path / name / "__init__.py")
            monkeypatch.setitem(sys.modules, name, module)
        monkeypatch.setattr(engine_mod, "active_engine", lambda: BrowserEngine(engine))
        assert bm.disable_features_args() == (
            f"--disable-features={expected},CloudSyncedFolderChecker",
        )

    def test_installed_playwright_list_still_parses(self) -> None:
        """Pins the parse to the installed Playwright: an upgrade that moves or
        reshapes the list fails here instead of silently dropping the fix."""
        from gflow_cli.browser_manager import disable_features_args

        (switch,) = disable_features_args()
        features = switch.removeprefix("--disable-features=").split(",")
        assert {"HttpsUpgrades", "Translate", "MediaRouter"} <= set(features)
        assert features[-1] == "CloudSyncedFolderChecker"

    def test_every_launch_call_carries_the_merged_switch(self) -> None:
        """Headless real Chrome hangs on close too (measured 31.3 s, #1140 probe), so
        EVERY ``launch_persistent_context`` call, not merely every file, must pass
        ``*disable_features_args()`` in ``args=`` or spread ``**login_launch_kwargs(...)``.
        ``FlowApiClient`` launches ``**kwargs`` from ``_persistent_context_kwargs``,
        which the kwargs test below checks by behaviour."""
        import ast

        import gflow_cli

        def called(node: ast.expr, name: str) -> bool:
            return isinstance(node, ast.Call) and getattr(node.func, "id", "") == name

        def carries_switch(call: ast.Call) -> bool:
            for kw in call.keywords:
                if kw.arg == "args" and isinstance(kw.value, ast.List):
                    return any(
                        isinstance(e, ast.Starred) and called(e.value, "disable_features_args")
                        for e in kw.value.elts
                    )
                if kw.arg is None and called(kw.value, "login_launch_kwargs"):
                    return True
            return False

        src = Path(gflow_cli.__file__).parent
        offenders: list[str] = []
        for py in src.rglob("*.py"):
            rel = py.relative_to(src).as_posix()
            for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
                if not (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "attr", "") == "launch_persistent_context"
                ):
                    continue
                if rel == "api/client.py" and any(k.arg is None for k in node.keywords):
                    continue  # **kwargs from _persistent_context_kwargs (tested below)
                if not carries_switch(node):
                    offenders.append(f"{rel}:{node.lineno}")
        assert not offenders, f"Chrome launches without the #1140 switch: {offenders}"

    def test_generation_and_login_kwargs_carry_one_switch(self, tmp_path: Path) -> None:
        from gflow_cli.api.client import FlowApiClient
        from gflow_cli.auth.internal_chromium import login_launch_kwargs
        from gflow_cli.browser_manager import disable_features_args
        from gflow_cli.config import Settings

        (switch,) = disable_features_args()
        generation = FlowApiClient(profile_dir=tmp_path, settings=Settings())
        for args in (
            generation._persistent_context_kwargs()["args"],  # noqa: SLF001
            login_launch_kwargs(tmp_path, headless=False)["args"],
        ):
            assert [a for a in args if a.startswith("--disable-features")] == [switch]

    def test_passive_login_subprocess_disables_the_checker(self) -> None:
        """The bare-subprocess login has no Playwright list to keep, so a lone switch
        is the whole fix there, and it must be the only one."""
        from gflow_cli.auth.real_chrome import _build_chrome_args

        for headless in (False, True):
            args = _build_chrome_args("chrome.exe", Path("prof"), headless=headless)
            assert [a for a in args if a.startswith("--disable-features")] == [
                "--disable-features=CloudSyncedFolderChecker"
            ]
