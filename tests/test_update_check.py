"""Tests for the once-a-day PyPI update notice (#479).

`tests/conftest.py::_isolate_settings` (autouse) redirects GFLOW_CLI_HOME to a
per-test tmp dir (and disables the update check suite-wide; tests here
re-enable it explicitly). The notice is best-effort by contract: every failure
path resolves to "no notice", never an exception, and the check must never
block the command — ``checked_at`` is stamped synchronously so the once-a-day
cap holds even when the daemon fetch thread dies with a fast command.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

from gflow_cli import __version__
from gflow_cli import update_check as uc
from gflow_cli.config import get_settings, reset_settings

if TYPE_CHECKING:
    from collections.abc import Callable


class _FakeThread:
    """Stands in for the module's Thread import — never starts a real thread."""

    spawned: list[dict[str, object]] = []

    def __init__(self, **kwargs: object) -> None:
        _FakeThread.spawned.append(kwargs)

    def start(self) -> None: ...


@pytest.fixture
def notify_enabled(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Eligible-notify baseline: index install, not CI, check enabled, and the
    module's Thread seam faked (patching `uc.Thread`, NOT threading.Thread —
    the real one is shared process-wide). Returns the spawn log."""
    monkeypatch.setattr(uc, "_installed_from_index", lambda: True)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("GFLOW_CLI_UPDATE_CHECK", "1")
    reset_settings()
    _FakeThread.spawned = []
    monkeypatch.setattr(uc, "Thread", _FakeThread)
    return _FakeThread.spawned


def _write_cache(latest: str | None, *, age_seconds: float = 0.0) -> Path:
    cache_path = get_settings().home / "update_check.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"checked_at": time.time() - age_seconds, "latest": latest}),
        encoding="utf-8",
    )
    return cache_path


class TestMaybeNotifyUpdate:
    def test_notice_when_cache_has_newer_version(
        self, notify_enabled: list[dict[str, object]]
    ) -> None:
        _write_cache("999.0.0")
        notice = uc.maybe_notify_update()
        assert notice is not None
        assert notice.latest == "999.0.0"
        assert notice.installed == __version__
        assert "999.0.0" in notice.text
        assert __version__ in notice.text
        assert "gflow update" in notice.text  # points at the self-update command

    def test_no_notice_when_cache_equal_or_older(
        self, notify_enabled: list[dict[str, object]]
    ) -> None:
        _write_cache(__version__)
        assert uc.maybe_notify_update() is None
        _write_cache("0.0.1")
        assert uc.maybe_notify_update() is None

    @pytest.mark.parametrize("ci_value", ["true", "1", "yes"])
    def test_skipped_in_ci(
        self,
        notify_enabled: list[dict[str, object]],
        monkeypatch: pytest.MonkeyPatch,
        ci_value: str,
    ) -> None:
        monkeypatch.setenv("CI", ci_value)
        _write_cache("999.0.0")
        assert uc.maybe_notify_update() is None

    @pytest.mark.parametrize("ci_value", ["", "0", "false", "False"])
    def test_ci_falsey_values_do_not_disable(
        self,
        notify_enabled: list[dict[str, object]],
        monkeypatch: pytest.MonkeyPatch,
        ci_value: str,
    ) -> None:
        """CI=false / CI=0 mark a NON-CI shell — the notice must survive."""
        monkeypatch.setenv("CI", ci_value)
        _write_cache("999.0.0")
        assert uc.maybe_notify_update() is not None

    def test_skipped_when_disabled(
        self, notify_enabled: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GFLOW_CLI_UPDATE_CHECK", "0")
        reset_settings()
        _write_cache("999.0.0")
        assert uc.maybe_notify_update() is None

    def test_skipped_for_non_index_install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("GFLOW_CLI_UPDATE_CHECK", "1")
        reset_settings()
        monkeypatch.setattr(uc, "_installed_from_index", lambda: False)
        _write_cache("999.0.0")
        assert uc.maybe_notify_update() is None

    def test_fresh_cache_spawns_no_refresh(self, notify_enabled: list[dict[str, object]]) -> None:
        _write_cache("999.0.0", age_seconds=60)
        assert uc.maybe_notify_update() is not None
        assert notify_enabled == []

    def test_stale_cache_stamps_synchronously_and_spawns_daemon_refresh(
        self, notify_enabled: list[dict[str, object]]
    ) -> None:
        """The cap must hold even if the fetch thread never runs: the stale
        path stamps checked_at BEFORE spawning, so an immediate second call
        spawns nothing — and the stale `latest` still yields a notice."""
        cache_path = _write_cache("999.0.0", age_seconds=uc._CHECK_INTERVAL_SECONDS + 60)
        assert uc.maybe_notify_update() is not None
        assert len(notify_enabled) == 1
        assert notify_enabled[0].get("daemon") is True
        stamped = json.loads(cache_path.read_text(encoding="utf-8"))
        assert stamped["checked_at"] == pytest.approx(time.time(), abs=30)
        assert stamped["latest"] == "999.0.0"  # preserved for the notice
        assert uc.maybe_notify_update() is not None
        assert len(notify_enabled) == 1  # no second spawn: cap enforced

    def test_future_checked_at_counts_as_stale(
        self, notify_enabled: list[dict[str, object]]
    ) -> None:
        """Clock skew / restored VM: a future stamp must not pin the cache
        'fresh' until the wall clock catches up."""
        _write_cache("999.0.0", age_seconds=-999_999)
        assert uc.maybe_notify_update() is not None
        assert len(notify_enabled) == 1

    def test_missing_or_corrupt_cache_is_no_notice(
        self, notify_enabled: list[dict[str, object]]
    ) -> None:
        assert uc.maybe_notify_update() is None  # no cache at all
        cache_path = get_settings().home / "update_check.json"
        cache_path.write_text("{not json", encoding="utf-8")
        assert uc.maybe_notify_update() is None

    def test_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Best-effort by contract: even a broken settings layer yields None."""

        def _boom() -> object:
            raise RuntimeError("settings exploded")

        monkeypatch.setattr(uc, "get_settings", _boom)
        assert uc.maybe_notify_update() is None


class TestRefreshCache:
    def test_refresh_writes_latest_and_checked_at(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cache_path = get_settings().home / "update_check.json"

        class _Resp:
            def raise_for_status(self) -> None: ...

            def json(self) -> dict[str, dict[str, str]]:
                return {"info": {"version": "1.2.3"}}

        monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
        uc._refresh_cache(cache_path)
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        assert data["latest"] == "1.2.3"
        assert data["checked_at"] == pytest.approx(time.time(), abs=30)

    def test_refresh_failure_writes_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """checked_at is the caller's job (stamped pre-spawn); a failed fetch
        must not clobber it or the previous latest."""
        cache_path = _write_cache("1.2.3", age_seconds=999)
        before = cache_path.read_text(encoding="utf-8")

        def _fail(*a: object, **k: object) -> object:
            raise OSError("offline")

        monkeypatch.setattr(httpx, "get", _fail)
        uc._refresh_cache(cache_path)
        assert cache_path.read_text(encoding="utf-8") == before

    def test_write_cache_is_atomic_replace(self, tmp_path: Path) -> None:
        """No .tmp leftovers, and the target is complete JSON after a write."""
        cache_path = tmp_path / "update_check.json"
        uc._write_cache(cache_path, {"checked_at": 1.0, "latest": "9.9.9"})
        assert json.loads(cache_path.read_text(encoding="utf-8"))["latest"] == "9.9.9"
        assert not cache_path.with_suffix(".json.tmp").exists()


class TestVersionCompare:
    @pytest.mark.parametrize(
        ("latest", "installed", "newer"),
        [
            ("0.56.0", "0.55.0", True),
            ("1.0.0", "0.99.9", True),
            ("0.55.0", "0.55.0", False),
            ("0.54.9", "0.55.0", False),
            # PEP 440 suffixes compare at base-version granularity — a
            # suffixed release must never silently kill the notice.
            ("0.56.0rc1", "0.55.0", True),
            ("0.55.1.post1", "0.55.0", True),
            ("0.56.0", "0.56.0.dev1", False),
            # Documented ceiling: same-base post-release is not "newer".
            ("0.55.0.post1", "0.55.0", False),
            ("not-a-version", "0.55.0", False),
            ("0.56.0", "not-a-version", False),
        ],
    )
    def test_is_newer(self, latest: str, installed: str, newer: bool) -> None:
        assert uc._is_newer(latest, installed) is newer


class TestInstalledFromIndex:
    def _with_direct_url(
        self, monkeypatch: pytest.MonkeyPatch, payload: str | None
    ) -> Callable[[], bool]:
        class _Dist:
            def read_text(self, name: str) -> str | None:
                assert name == "direct_url.json"
                return payload

        monkeypatch.setattr(uc, "distribution", lambda _name: _Dist())
        return uc._installed_from_index

    def test_no_direct_url_is_index_install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._with_direct_url(monkeypatch, None)() is True

    @pytest.mark.parametrize(
        "payload",
        [
            json.dumps({"url": "file:///dev/gflow-cli", "dir_info": {"editable": True}}),
            json.dumps({"url": "file:///dev/gflow-cli", "dir_info": {}}),
            # VCS / direct-URL installs: `pip install -U` would silently
            # replace a deliberate pin — never advise it.
            json.dumps({"url": "https://github.com/ffroliva/gflow-cli", "vcs_info": {}}),
        ],
    )
    def test_any_direct_url_install_is_not(
        self, monkeypatch: pytest.MonkeyPatch, payload: str
    ) -> None:
        assert self._with_direct_url(monkeypatch, payload)() is False

    def test_missing_distribution_is_not(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from importlib.metadata import PackageNotFoundError

        def _raise(_name: str) -> object:
            raise PackageNotFoundError("gflow-cli")

        monkeypatch.setattr(uc, "distribution", _raise)
        assert uc._installed_from_index() is False


# --- gflow update (self-update) ----------------------------------------------


@pytest.fixture
def index_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """An index-installed wheel whose venv root (sys.prefix) is `tmp_path`."""
    monkeypatch.setattr(uc, "_installed_from_index", lambda: True)
    monkeypatch.setattr(uc.sys, "prefix", str(tmp_path))
    return tmp_path


class TestInstaller:
    def test_uv_tool_receipt(self, index_install: Path) -> None:
        (index_install / "uv-receipt.toml").write_text("[tool]\n", encoding="utf-8")
        inst = uc.installer()
        assert inst is not None
        assert inst.name == "uv"
        assert inst.command == ("uv", "tool", "upgrade", "gflow-cli")

    def test_pipx_metadata(self, index_install: Path) -> None:
        (index_install / "pipx_metadata.json").write_text("{}", encoding="utf-8")
        inst = uc.installer()
        assert inst is not None
        assert inst.name == "pipx"
        assert inst.command == ("pipx", "upgrade", "gflow-cli")

    def test_plain_venv_uses_its_own_interpreter(self, index_install: Path) -> None:
        inst = uc.installer()
        assert inst is not None
        assert inst.name == "pip"
        # Never a bare `pip` from PATH — the venv that holds gflow-cli.
        assert inst.command == (
            uc.sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "gflow-cli",
        )

    def test_source_install_is_none(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(uc, "_installed_from_index", lambda: False)
        monkeypatch.setattr(uc.sys, "prefix", str(tmp_path))
        (tmp_path / "uv-receipt.toml").write_text("[tool]\n", encoding="utf-8")
        assert uc.installer() is None


@pytest.fixture
def uv_install(index_install: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """uv-tool install, `uv` on PATH, subprocess seam captured, Playwright stable."""
    (index_install / "uv-receipt.toml").write_text("[tool]\n", encoding="utf-8")
    monkeypatch.setattr(uc.shutil, "which", lambda name: f"/bin/{name}")
    calls: list[tuple[str, ...]] = []

    def _run(command: tuple[str, ...]) -> int:
        calls.append(command)
        return 0

    monkeypatch.setattr(uc, "_run_command", _run)
    # The venv after the manager ran: gflow-cli moved to 999.0.0, Playwright stable.
    versions = {"gflow-cli": "999.0.0", "playwright": "1.59.0"}
    monkeypatch.setattr(uc, "_version_in_venv", lambda dist: versions[dist])
    return calls


class TestRunUpdate:
    def test_source_install_refused_without_subprocess(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.errors import ConfigurationError

        monkeypatch.setattr(uc, "_installed_from_index", lambda: False)
        monkeypatch.setattr(uc, "_run_command", lambda cmd: pytest.fail("must not spawn"))
        with pytest.raises(ConfigurationError, match="not a PyPI install"):
            uc.run_update(check=False)

    def test_up_to_date_spawns_nothing(
        self, uv_install: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: __version__)
        report = uc.run_update(check=False)
        assert report.update_available is False
        assert report.upgraded is False
        assert uv_install == []

    def test_newer_runs_detected_command(
        self, uv_install: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        report = uc.run_update(check=False)
        assert report.update_available is True
        assert report.upgraded is True
        assert report.installer == "uv"
        assert report.latest == "999.0.0"  # what the venv reports after the run
        assert uv_install == [("uv", "tool", "upgrade", "gflow-cli")]
        assert report.notes == ()

    def test_check_never_spawns_and_refreshes_cache(
        self, uv_install: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        report = uc.run_update(check=True)
        assert report.update_available is True
        assert report.upgraded is False
        assert report.command == ("uv", "tool", "upgrade", "gflow-cli")
        assert uv_install == []
        cache_path = get_settings().home / "update_check.json"
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        assert cache["latest"] == "999.0.0"

    def test_pypi_unreachable_still_runs_manager(
        self, uv_install: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The manager is authoritative; a failed pre-check must not block it."""
        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: None)
        report = uc.run_update(check=False)
        assert report.update_available is None
        assert len(uv_install) == 1
        # The venv moved (fixture: 999.0.0), so the outcome is an upgrade even
        # though PyPI never answered — `latest` is what the venv reports now.
        assert report.upgraded is True
        assert report.latest == "999.0.0"

    def test_manager_failure_without_a_new_version_is_configuration_error(
        self, uv_install: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.errors import ConfigurationError

        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        monkeypatch.setattr(uc, "_run_command", lambda cmd: 3)
        monkeypatch.setattr(uc, "_version_in_venv", lambda dist: __version__)
        with pytest.raises(ConfigurationError, match="exited 3"):
            uc.run_update(check=False)

    def test_manager_exit_0_but_version_unchanged_is_configuration_error(
        self, uv_install: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pinned receipt makes `uv tool upgrade` a silent no-op — not success."""
        from gflow_cli.errors import ConfigurationError

        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        monkeypatch.setattr(uc, "_version_in_venv", lambda dist: __version__)
        with pytest.raises(ConfigurationError, match=f"still {__version__}"):
            uc.run_update(check=False)

    def test_manager_failure_after_install_is_upgraded_with_note(
        self, uv_install: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Measured on Windows: uv installs the wheel, then exits 1 copying the
        in-use `gflow.exe` launcher. The venv moved, so it IS an upgrade."""
        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        monkeypatch.setattr(uc, "_run_command", lambda cmd: 1)
        report = uc.run_update(check=False)
        assert report.upgraded is True
        assert report.latest == "999.0.0"
        assert len(report.notes) == 1
        assert "exited 1 AFTER installing 999.0.0" in report.notes[0]

    def test_pypi_unreachable_and_manager_found_nothing(
        self, uv_install: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: None)
        monkeypatch.setattr(uc, "_version_in_venv", lambda dist: __version__)
        report = uc.run_update(check=False)
        assert report.upgraded is False
        assert report.latest is None
        assert len(uv_install) == 1

    def test_missing_manager_binary(
        self, uv_install: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.errors import ConfigurationError

        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        monkeypatch.setattr(uc.shutil, "which", lambda name: None)
        with pytest.raises(ConfigurationError, match="uv tool upgrade gflow-cli"):
            uc.run_update(check=False)
        assert uv_install == []

    def test_playwright_bump_yields_hint(
        self, uv_install: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        playwright = iter(["1.59.0", "1.60.0"])

        def _version(dist: str) -> str:
            return "999.0.0" if dist == "gflow-cli" else next(playwright)

        monkeypatch.setattr(uc, "_version_in_venv", _version)
        report = uc.run_update(check=False)
        assert len(report.notes) == 1
        assert "playwright install chromium" in report.notes[0]


class TestFetchLatest:
    def test_returns_version_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Resp:
            def raise_for_status(self) -> None: ...

            def json(self) -> dict[str, dict[str, str]]:
                return {"info": {"version": "1.2.3"}}

        monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
        assert uc.fetch_latest(timeout=1.0) == "1.2.3"

    def test_failure_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _fail(*a: object, **k: object) -> object:
            raise OSError("offline")

        monkeypatch.setattr(httpx, "get", _fail)
        assert uc.fetch_latest(timeout=1.0) is None

    @pytest.mark.parametrize("bad", ["99.0[bold red]x", "0.99.0\x1b]8;;http://evil", "", "1 2"])
    def test_non_version_strings_are_rejected(
        self, monkeypatch: pytest.MonkeyPatch, bad: str
    ) -> None:
        """The string is cached, then rendered as Rich markup and put in a URL."""

        class _Resp:
            def raise_for_status(self) -> None: ...

            def json(self) -> dict[str, dict[str, str]]:
                return {"info": {"version": bad}}

        monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
        assert uc.fetch_latest(timeout=1.0) is None


class TestPipWithoutPipModule:
    def test_uv_venv_without_pip_is_refused_before_spawning(
        self, index_install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.errors import ConfigurationError

        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        monkeypatch.setattr(uc.importlib.util, "find_spec", lambda name: None)
        monkeypatch.setattr(uc, "_run_command", lambda cmd: pytest.fail("must not spawn"))
        with pytest.raises(ConfigurationError, match="no `pip` module") as info:
            uc.run_update(check=False)
        # The remediation must not recommend the very `python -m pip` that failed.
        assert "uv pip install --upgrade gflow-cli" in (info.value.remediation_hint or "")

    def test_probe_failure_after_run_is_reported_as_unverified(
        self, uv_install: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.errors import ConfigurationError

        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        monkeypatch.setattr(uc, "_version_in_venv", lambda dist: None)
        with pytest.raises(ConfigurationError, match="could not be re-read"):
            uc.run_update(check=False)
