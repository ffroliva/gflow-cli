"""Tests for the once-a-day PyPI update notice (#479).

`tests/conftest.py::_isolate_settings` (autouse) redirects GFLOW_CLI_HOME to a
per-test tmp dir (and disables the update check suite-wide; tests here
re-enable it explicitly). The notice is best-effort by contract: every failure
path resolves to "no notice", never an exception, and the check must never
block the command — ``checked_at`` is stamped synchronously so the once-a-day
cap holds even when the daemon fetch thread dies with a fast command.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
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
    """An index-installed wheel whose venv root (sys.prefix) is `tmp_path`.

    The #1298 launcher guard is stubbed: unstubbed, a pip `run_update` test on Windows
    renamed this checkout's own `.venv/Scripts/*.exe` (review of #1298). The guard has
    its own tests below, on files under `tmp_path`."""
    monkeypatch.setattr(uc, "_installed_from_index", lambda: True)
    monkeypatch.setattr(uc.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(uc, "_launchers_set_aside", lambda inst: contextlib.nullcontext())
    monkeypatch.setattr(uc, "_sweep_set_aside", lambda: None)
    return tmp_path


_REAL_INSTALLED_EXTRAS = uc._installed_extras


@pytest.fixture(autouse=True)
def _no_installed_extras(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic: the venv running the suite may hold an extra (CI's engine job installs one)."""
    monkeypatch.setattr(uc, "_installed_extras", lambda: ())


_REQUIRES = [
    "httpx>=0.27",
    'pytest>=8.0.0; extra == "dev"',
    'patchright==1.64.4; extra == "patchright"',
    "av>=12; extra == 'chain'",
    'pillow>=13.0.0; extra == "chain"',
    'universal_pathlib>=0.2.5; extra == "gcs"',
    'gcsfs>=2024.2.0; extra == "gcs"',
]


@pytest.fixture
def installed_dists(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """`distribution()` answers for gflow-cli (with `_REQUIRES`) and the names in the set."""
    present = {"gflow-cli"}

    class _Dist:
        requires = _REQUIRES

    def _dist(name: str) -> _Dist:
        if name not in present:
            raise uc.PackageNotFoundError(name)
        return _Dist()

    monkeypatch.setattr(uc, "distribution", _dist)
    return present


class TestInstalledExtras:
    """#1322: an upgrade must request the extras that are installed, or their pins never move."""

    def test_an_extra_counts_when_every_requirement_is_installed(
        self, installed_dists: set[str]
    ) -> None:
        installed_dists |= {"patchright", "av", "pillow", "universal_pathlib", "pytest"}
        # gcs lacks gcsfs, so a shared dependency alone does not claim it; dev never counts.
        assert _REAL_INSTALLED_EXTRAS() == ("chain", "patchright")

    def test_no_extras_installed(self, installed_dists: set[str]) -> None:
        assert _REAL_INSTALLED_EXTRAS() == ()

    def test_the_shown_command_quotes_an_extras_spec(self) -> None:
        cmd = ("py", "-m", "pip", "install", "--upgrade", "gflow-cli[patchright]")
        assert uc.shown_command(cmd).endswith('--upgrade "gflow-cli[patchright]"')

    def test_pip_upgrade_and_repair_carry_the_extras(
        self, index_install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(uc, "_installed_extras", lambda: ("chain", "patchright"))
        inst = uc.installer()
        assert inst is not None
        assert inst.command[-1] == "gflow-cli[chain,patchright]"
        assert "gflow-cli[chain,patchright]==0.101.2" in uc._reinstall_hint(inst, "0.101.2")

    def test_the_pip_repair_keeps_the_extras_after_pip_removed_them(
        self, index_install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed pip run can leave the extras uninstalled; the hint must not drop them."""
        monkeypatch.setattr(uc, "_installed_extras", lambda: ("patchright",))
        inst = uc.installer()
        assert inst is not None
        monkeypatch.setattr(uc, "_installed_extras", lambda: ())  # metadata gone after the run
        assert '"gflow-cli[patchright]==0.101.2"' in uc._reinstall_hint(inst, "0.101.2")


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


def _done(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    """What `_run_command` returns: a finished manager run."""
    return subprocess.CompletedProcess(["fake"], returncode, stdout, stderr)


def _exits(
    returncode: int, stdout: str = "", stderr: str = ""
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """A `_run_command` fake with the real signature, finishing as given."""

    def _run(command: tuple[str, ...], *, capture_output: bool) -> subprocess.CompletedProcess[str]:
        return _done(returncode, stdout, stderr)

    return _run


class _FakeProc:
    """Stands in for `subprocess.Popen` in `_run_command`'s captured branch.

    Records the Popen kwargs and every `communicate` timeout. With
    ``interrupt_with`` set, the first `communicate` raises KeyboardInterrupt and
    the drain after `kill()` returns (or raises) that value."""

    kwargs: dict[str, object] = {}
    timeouts: list[float | None] = []
    interrupt_with: tuple[str, str] | BaseException | None = None
    # The pipes `__exit__` would close; a real close blocks while a grandchild holds them.
    pipes_at_exit: tuple[object, object] = (None, None)

    def __init__(self, args: object, **kwargs: object) -> None:
        type(self).kwargs = kwargs
        type(self).timeouts = []
        self.args = args
        self.returncode = 0
        self.stdout: object = object()
        self.stderr: object = object()

    def __enter__(self) -> _FakeProc:
        return self

    def __exit__(self, *a: object) -> None:
        type(self).interrupt_with = None
        type(self).pipes_at_exit = (self.stdout, self.stderr)

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self.timeouts.append(timeout)
        drain = type(self).interrupt_with
        if drain is None:
            return ("", "")
        if len(self.timeouts) == 1:
            raise KeyboardInterrupt
        if isinstance(drain, BaseException):
            raise drain
        return drain

    def kill(self) -> None: ...


@pytest.fixture
def uv_install(index_install: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """uv-tool install, `uv` on PATH, subprocess seam captured, Playwright stable."""
    (index_install / "uv-receipt.toml").write_text("[tool]\n", encoding="utf-8")
    monkeypatch.setattr(uc.shutil, "which", lambda name: f"/bin/{name}")
    calls: list[tuple[str, ...]] = []

    def _run(command: tuple[str, ...], *, capture_output: bool) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return _done(0)

    monkeypatch.setattr(uc, "_run_command", _run)
    # The venv after the manager ran: gflow-cli moved to 999.0.0, Playwright stable.
    versions = {"gflow-cli": "999.0.0", "playwright": "1.59.0"}
    monkeypatch.setattr(uc, "_version_in_venv", lambda dist: versions[dist])
    # Healthy by default, and stubbed so no test spawns the real probe interpreter.
    monkeypatch.setattr(uc, "_import_failure", lambda: None)
    return calls


class TestRunUpdate:
    def test_source_install_refused_without_subprocess(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.errors import ConfigurationError

        monkeypatch.setattr(uc, "_installed_from_index", lambda: False)
        monkeypatch.setattr(
            uc, "_run_command", lambda cmd, *, capture_output: pytest.fail("must not spawn")
        )
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

    def test_type_error_after_install_propagates_and_runs_installer_once(
        self, uv_install: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bug after the manager ran must surface, never re-run the whole update."""
        from click.testing import CliRunner

        from gflow_cli.cli import main

        def _boom() -> str | None:
            raise TypeError("bug after the install")

        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        monkeypatch.setattr(uc, "_import_failure", _boom)
        res = CliRunner().invoke(main, ["update"])
        assert res.exit_code != 0
        assert len(uv_install) == 1, f"installer ran {len(uv_install)} times"

    def test_type_error_from_the_manager_run_is_not_retried(
        self, uv_install: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[bool] = []

        def _run(command: tuple[str, ...], *, capture_output: bool = True) -> object:
            calls.append(capture_output)
            raise TypeError("bug inside the run")

        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        monkeypatch.setattr(uc, "_run_command", _run)
        with pytest.raises(TypeError, match="bug inside the run"):
            uc.run_update(check=False)
        assert len(calls) == 1, f"manager ran {len(calls)} times"

    @pytest.mark.parametrize(
        ("argv", "captured"),
        [
            (["update"], True),
            (["update", "-v"], False),  # streams: the user sees the manager live
            (["update", "--json", "-v"], True),  # stdout must stay one JSON document
            (["-v", "update"], True),  # root -v is debug logging, not installer streaming
        ],
    )
    def test_capture_decision(
        self,
        uv_install: list[tuple[str, ...]],
        monkeypatch: pytest.MonkeyPatch,
        argv: list[str],
        captured: bool,
    ) -> None:
        from click.testing import CliRunner

        from gflow_cli.cli import main

        seen: list[bool] = []

        def _run(
            command: tuple[str, ...], *, capture_output: bool
        ) -> subprocess.CompletedProcess[str]:
            seen.append(capture_output)
            return _done(0)

        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        monkeypatch.setattr(uc, "_run_command", _run)
        res = CliRunner().invoke(main, argv)
        assert res.exit_code == 0, res.output
        assert seen == [captured], f"{argv}: capture_output={seen}"
        if "--json" in argv:
            json.loads(res.stdout)

    def test_manager_failure_without_a_new_version_is_configuration_error(
        self, uv_install: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.errors import ConfigurationError

        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        monkeypatch.setattr(uc, "_run_command", _exits(3))
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

    def test_broken_environment_is_reported_even_when_the_version_moved(
        self, uv_install: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#848: metadata says 999.0.0, imports say otherwise. The version moved,
        so every existing branch would have called this a successful upgrade."""
        from gflow_cli.errors import ConfigurationError

        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        monkeypatch.setattr(
            uc,
            "_import_failure",
            lambda: "AttributeError: module 'greenlet' has no attribute 'greenlet'",
        )
        with pytest.raises(ConfigurationError, match="unusable") as excinfo:
            uc.run_update(check=False)
        assert "greenlet" in str(excinfo.value)
        # The repair command, not the `upgrade` that cannot fix a half-replaced venv.
        assert "--force --reinstall" in (excinfo.value.remediation_hint or "")

    def test_broken_environment_outranks_the_unchanged_version_message(
        self, uv_install: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The #848 shape exactly: manager failed, version unchanged, venv dead.
        "still <version>" plus "run the upgrade yourself" was the wrong advice."""
        from gflow_cli.errors import ConfigurationError

        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        monkeypatch.setattr(uc, "_run_command", _exits(2))
        monkeypatch.setattr(uc, "_version_in_venv", lambda dist: __version__)
        monkeypatch.setattr(uc, "_import_failure", lambda: "ImportError: DLL load failed")
        with pytest.raises(ConfigurationError, match="unusable"):
            uc.run_update(check=False)

    def test_manager_failure_after_install_is_upgraded_with_note(
        self, uv_install: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Measured on Windows: uv installs the wheel, then exits 1 copying the
        in-use `gflow.exe` launcher. The venv moved, so it IS an upgrade."""
        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        monkeypatch.setattr(uc, "_run_command", _exits(1))
        report = uc.run_update(check=False)
        assert report.upgraded is True
        assert report.latest == "999.0.0"
        assert len(report.notes) == 1
        assert "exited 1 AFTER installing 999.0.0" in report.notes[0]

    def test_failure_after_install_shows_output_and_an_honest_note(
        self,
        uv_install: list[tuple[str, ...]],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The next `gflow update` reports up to date and never runs the manager,
        so the note must not promise a refresh; and the launcher is a Windows cause."""
        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        monkeypatch.setattr(
            uc, "_run_command", _exits(1, stderr="error: Failed to upgrade gflow-cli")
        )
        report = uc.run_update(check=False)
        assert report.upgraded is True
        assert "error: Failed to upgrade gflow-cli" in capsys.readouterr().err
        (note,) = report.notes
        assert "On Windows" in note, note
        assert "next update" not in note, note

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

    def test_no_updating_line_when_the_manager_cannot_start(
        self, uv_install: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.errors import ConfigurationError

        status: list[str] = []
        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        monkeypatch.setattr(uc.shutil, "which", lambda name: None)
        with pytest.raises(ConfigurationError, match="not on PATH"):
            uc.run_update(check=False, status_callback=status.append)
        assert status == [], f"announced a run that cannot start: {status}"

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

    def test_installer_output_captured_by_default(
        self,
        uv_install: list[tuple[str, ...]],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import subprocess

        fake_process = subprocess.CompletedProcess(
            args=["uv", "tool", "upgrade", "gflow-cli"],
            returncode=0,
            stdout="Resolved 27 packages in 12ms\nInstalled 1 package",
            stderr="",
        )
        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        monkeypatch.setattr(uc, "_run_command", lambda cmd, *, capture_output: fake_process)
        report = uc.run_update(check=False)
        assert report.upgraded is True
        captured = capsys.readouterr()
        assert "Resolved 27 packages" not in captured.err
        assert "Resolved 27 packages" not in captured.out

    def test_installer_output_shown_on_failure(
        self,
        uv_install: list[tuple[str, ...]],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import subprocess

        from gflow_cli.errors import ConfigurationError

        fake_process = subprocess.CompletedProcess(
            args=["uv", "tool", "upgrade", "gflow-cli"],
            returncode=2,
            stdout="",
            stderr="error: could not resolve dependencies",
        )
        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        monkeypatch.setattr(uc, "_run_command", lambda cmd, *, capture_output: fake_process)
        monkeypatch.setattr(uc, "_version_in_venv", lambda dist: __version__)
        with pytest.raises(ConfigurationError):
            uc.run_update(check=False)
        captured = capsys.readouterr()
        assert captured.err.count("error: could not resolve dependencies") == 1

    def test_status_callback_invoked(
        self, uv_install: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        status_messages: list[str] = []
        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        report = uc.run_update(check=False, status_callback=status_messages.append)
        assert report.upgraded is True
        assert len(status_messages) == 1
        assert f"Updating gflow-cli {__version__} -> 999.0.0 via uv..." == status_messages[0]

    @pytest.mark.parametrize(
        ("installer_name", "receipt_file", "noisy_output"),
        [
            ("uv", "uv-receipt.toml", "Resolved 27 packages\nDownloaded 1 package"),
            ("pipx", "pipx_metadata.json", "upgrading gflow-cli from 0.1.0 to 999.0.0"),
            (
                "pip",
                None,
                "Collecting gflow-cli\nRequirement already satisfied\nSuccessfully installed",
            ),
        ],
    )
    def test_all_installers_capture_output_cleanly(
        self,
        index_install: Path,
        monkeypatch: pytest.MonkeyPatch,
        installer_name: str,
        receipt_file: str | None,
        noisy_output: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import subprocess

        if receipt_file:
            (index_install / receipt_file).write_text("{}", encoding="utf-8")
        monkeypatch.setattr(uc.shutil, "which", lambda name: f"/bin/{name}")
        monkeypatch.setattr(uc.importlib.util, "find_spec", lambda name: object())
        fake_process = subprocess.CompletedProcess(
            args=["fake"],
            returncode=0,
            stdout=noisy_output,
            stderr="warning: some stderr notice",
        )
        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        monkeypatch.setattr(uc, "_run_command", lambda cmd, *, capture_output: fake_process)
        monkeypatch.setattr(uc, "_version_in_venv", lambda dist: "999.0.0")
        monkeypatch.setattr(uc, "_import_failure", lambda: None)

        report = uc.run_update(check=False)
        assert report.upgraded is True
        assert report.installer == installer_name
        assert report.latest == "999.0.0"

        captured = capsys.readouterr()
        assert noisy_output not in captured.out
        assert noisy_output not in captured.err

    @pytest.mark.parametrize(
        ("receipt_file", "noisy_stdout"),
        [
            ("uv-receipt.toml", "Resolved 27 packages"),
            ("pipx_metadata.json", "upgrading gflow-cli"),
            (None, "Collecting gflow-cli\nRequirement already satisfied"),
        ],
    )
    def test_json_stdout_clean_for_all_installers(
        self,
        index_install: Path,
        monkeypatch: pytest.MonkeyPatch,
        receipt_file: str | None,
        noisy_stdout: str,
    ) -> None:
        import subprocess

        from click.testing import CliRunner

        from gflow_cli.cli import main

        if receipt_file:
            (index_install / receipt_file).write_text("{}", encoding="utf-8")
        monkeypatch.setattr(uc.shutil, "which", lambda name: f"/bin/{name}")
        monkeypatch.setattr(uc.importlib.util, "find_spec", lambda name: object())
        fake_process = subprocess.CompletedProcess(
            args=["fake"],
            returncode=0,
            stdout=noisy_stdout,
            stderr="launcher notice",
        )
        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        monkeypatch.setattr(uc, "_run_command", lambda cmd, *, capture_output: fake_process)
        monkeypatch.setattr(uc, "_version_in_venv", lambda dist: "999.0.0")
        monkeypatch.setattr(uc, "_import_failure", lambda: None)

        res = CliRunner().invoke(main, ["update", "--json"])
        assert res.exit_code == 0, res.output
        doc = json.loads(res.stdout)
        assert doc["status"] == "ok"
        assert doc["upgraded"] is True
        assert doc["latest"] == "999.0.0"
        assert noisy_stdout not in res.stdout

    def test_interruption_preserves_captured_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(subprocess, "Popen", _FakeProc)
        _FakeProc.interrupt_with = ("", "partially downloaded bytes\n")
        with pytest.raises(KeyboardInterrupt):
            uc._run_command(("fake",), capture_output=True)
        captured = capsys.readouterr()
        assert "partially downloaded bytes" in captured.err

    def test_interrupt_drain_is_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A grandchild still holding the pipe must not block the Ctrl+C forever.

        Measured on Windows: after a timed-out drain, Popen.__exit__ closing the
        pipes waited for a 25 s grandchild; with the pipes detached it returned at 6 s."""
        monkeypatch.setattr(subprocess, "Popen", _FakeProc)
        _FakeProc.interrupt_with = subprocess.TimeoutExpired("fake", 5)
        with pytest.raises(KeyboardInterrupt):
            uc._run_command(("fake",), capture_output=True)
        assert _FakeProc.timeouts[-1] is not None, f"drain timeouts: {_FakeProc.timeouts}"
        assert _FakeProc.pipes_at_exit == (None, None), "pipes left for __exit__ to close"

    def test_capture_never_waits_on_a_hidden_prompt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With both pipes captured, a pip `User for host:` prompt would be invisible."""
        monkeypatch.setattr(subprocess, "Popen", _FakeProc)
        uc._run_command(("fake",), capture_output=True)
        assert _FakeProc.kwargs.get("stdin") is subprocess.DEVNULL, _FakeProc.kwargs

    def test_capture_asks_the_child_for_utf8(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """pip / pipx on Windows write the console code page unless told otherwise."""
        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        monkeypatch.setenv("GFLOW_TEST_SENTINEL", "kept")
        monkeypatch.setattr(subprocess, "Popen", _FakeProc)
        uc._run_command(("fake",), capture_output=True)
        env = _FakeProc.kwargs.get("env")
        assert isinstance(env, dict), f"no env passed: {_FakeProc.kwargs}"
        assert env.get("PYTHONIOENCODING") == "utf-8"
        assert env.get("GFLOW_TEST_SENTINEL") == "kept"  # a copy, not a replacement

    def test_run_command_decodes_utf8_with_replacement(self) -> None:
        cmd = (
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'valid utf8 \\xc3\\xa1 and invalid \\xff\\n')",
        )
        res = uc._run_command(cmd, capture_output=True)
        assert res.returncode == 0
        assert "valid utf8 á" in res.stdout
        assert "\ufffd" in res.stdout


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
        monkeypatch.setattr(
            uc, "_run_command", lambda cmd, *, capture_output: pytest.fail("must not spawn")
        )
        with pytest.raises(ConfigurationError, match="no `pip` module") as info:
            uc.run_update(check=False)
        # The remediation must not recommend the very `python -m pip` that failed.
        assert 'uv pip install --upgrade "gflow-cli"' in (info.value.remediation_hint or "")

    def test_probe_failure_after_run_is_reported_as_unverified(
        self, uv_install: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.errors import ConfigurationError

        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: "999.0.0")
        monkeypatch.setattr(uc, "_version_in_venv", lambda dist: None)
        with pytest.raises(ConfigurationError, match="could not be re-read"):
            uc.run_update(check=False)


class TestImportFailure:
    """The probe itself — a real subprocess, because a stubbed one proves nothing
    about whether `-I` can still see the venv it is asked about."""

    def test_healthy_environment_probes_clean(self) -> None:
        assert uc._import_failure() is None

    def test_broken_import_returns_the_last_stderr_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(uc, "_IMPORT_PROBE", "import gflow_cli_definitely_not_installed")
        reason = uc._import_failure()
        assert reason is not None
        assert "ModuleNotFoundError" in reason

    def test_unrunnable_probe_is_not_a_broken_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An environment we could not ask about is never reported as broken."""

        def _boom(*args: object, **kwargs: object) -> object:
            raise OSError("no fork for you")

        monkeypatch.setattr(uc.subprocess, "run", _boom)
        assert uc._import_failure() is None


class TestReinstallHint:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("uv", 'uv tool install "gflow-cli[patchright]==9.9.9" --force --reinstall'),
            ("pipx", 'pipx install --force "gflow-cli[patchright]==9.9.9"'),
        ],
    )
    def test_uv_and_pipx_repairs_carry_the_installed_extras(
        self, name: str, expected: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(uc, "_installed_extras", lambda: ("patchright",))
        inst = uc.Installer(name, ("x",), "gflow-cli[patchright]")
        monkeypatch.setattr(uc, "_installed_extras", lambda: ())  # a failed run dropped them
        assert expected in uc._reinstall_hint(inst, "9.9.9")

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("uv", 'uv tool install "gflow-cli==9.9.9" --force --reinstall'),
            ("pipx", 'pipx install --force "gflow-cli==9.9.9"'),
            ("pip", '-m pip install --force-reinstall "gflow-cli==9.9.9"'),
        ],
    )
    def test_repair_command_per_installer(self, name: str, expected: str) -> None:
        # A pip installer's argv ends in the package spec it upgrades (see installer()).
        hint = uc._reinstall_hint(uc.Installer(name, ("x", "gflow-cli")), "9.9.9")
        assert expected in hint
        assert "Close every running gflow process" in hint

    def test_unknown_version_falls_back_to_unpinned(self) -> None:
        hint = uc._reinstall_hint(uc.Installer("uv", ("x",)), None)
        assert '"gflow-cli" --force --reinstall' in hint


class _WinPermissionError(PermissionError):
    """A PermissionError that carries ``winerror`` on every platform.

    ``PermissionError(13, msg, path, 32)`` sets ``winerror`` only on Windows; on the
    Linux CI legs the guard saw no 32 and the in-use test failed there (#1304)."""

    def __init__(self, errno: int, msg: str, filename: str, *, winerror: int) -> None:
        super().__init__(errno, msg, filename)
        self._winerror = winerror

    @property
    def winerror(self) -> int:  # type: ignore[override]
        return self._winerror


class _FakeDistribution:
    """Python 3.12+ semantics: ``files`` drops entries missing on disk, RECORD keeps them.

    The guard must read RECORD: with ``files`` a launcher an interrupted update left
    aside simply vanished from the list (review of #1298)."""

    def __init__(self, paths: list[Path]) -> None:
        self._paths = {p.name: p for p in paths}

    @property
    def files(self) -> list[Path]:
        return [p for p in self._paths.values() if p.exists()]

    def read_text(self, name: str) -> str:
        assert name == "RECORD"
        return "".join(f"../../Scripts/{n},sha256=x,1\n" for n in self._paths)

    def locate_file(self, rel: object) -> Path:
        return self._paths[Path(str(rel)).name]


class TestLaunchersSetAside:
    def test_r1_launchers_moved_aside_before_manager_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """r1 every RECORD .exe launcher is moved aside BEFORE the manager runs:
        the fake _run_command asserts that the originals are absent."""
        gflow_exe = tmp_path / "gflow.exe"
        flow_exe = tmp_path / "flow.exe"
        gflow_exe.write_text("gflow", encoding="utf-8")
        flow_exe.write_text("flow", encoding="utf-8")

        fake_dist = _FakeDistribution([gflow_exe, flow_exe])
        monkeypatch.setattr(uc, "distribution", lambda name: fake_dist)
        monkeypatch.setattr(sys, "platform", "win32")

        inst = uc.Installer(
            "pip", (sys.executable, "-m", "pip", "install", "--upgrade", "gflow-cli")
        )
        with uc._launchers_set_aside(inst):
            assert not gflow_exe.exists()
            assert not flow_exe.exists()
            old_gflow = tmp_path / f"gflow.exe.gflow-update-{os.getpid()}.old"
            old_flow = tmp_path / f"flow.exe.gflow-update-{os.getpid()}.old"
            assert old_gflow.is_file()
            assert old_flow.is_file()

    @pytest.mark.parametrize("recreate", [True, False])
    def test_r2_recreated_launcher_kept_unrecreated_restored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recreate: bool
    ) -> None:
        """r2 after the manager returns (rc 0 and rc 1), a launcher pip recreated is kept
        and its .old is left for the sweep. A launcher pip did not recreate is restored."""
        gflow_exe = tmp_path / "gflow.exe"
        gflow_exe.write_text("original", encoding="utf-8")

        fake_dist = _FakeDistribution([gflow_exe])
        monkeypatch.setattr(uc, "distribution", lambda name: fake_dist)
        monkeypatch.setattr(sys, "platform", "win32")

        inst = uc.Installer(
            "pip", (sys.executable, "-m", "pip", "install", "--upgrade", "gflow-cli")
        )
        old_file = tmp_path / f"gflow.exe.gflow-update-{os.getpid()}.old"

        with uc._launchers_set_aside(inst):
            if recreate:
                gflow_exe.write_text("new_binary", encoding="utf-8")

        if recreate:
            assert gflow_exe.read_text(encoding="utf-8") == "new_binary"
            assert old_file.exists()  # left for the sweep
        else:
            assert gflow_exe.read_text(encoding="utf-8") == "original"  # restored
            assert not old_file.exists()

    def test_r2b_failed_restore_after_normal_yield_raises_configuration_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Finding 2a: os.replace failing during restore after a normal yield raises
        ConfigurationError (exit 11) naming the launcher path and its .old file, with
        an unpinned _reinstall_hint: pip may already have installed a newer release."""
        from gflow_cli.errors import EXIT_CODE_MAP, ConfigurationError

        gflow_exe = tmp_path / "gflow.exe"
        gflow_exe.write_text("original", encoding="utf-8")
        old_file = tmp_path / f"gflow.exe.gflow-update-{os.getpid()}.old"

        fake_dist = _FakeDistribution([gflow_exe])
        monkeypatch.setattr(uc, "distribution", lambda name: fake_dist)
        monkeypatch.setattr(sys, "platform", "win32")

        real_replace = os.replace

        def _fail_on_restore(src: str | Path, dst: str | Path) -> None:
            if str(dst) == str(gflow_exe):
                raise PermissionError(13, "Access denied", str(gflow_exe))
            real_replace(src, dst)

        monkeypatch.setattr(os, "replace", _fail_on_restore)

        inst = uc.Installer(
            "pip", (sys.executable, "-m", "pip", "install", "--upgrade", "gflow-cli")
        )
        with pytest.raises(ConfigurationError) as exc_info, uc._launchers_set_aside(inst):
            pass

        assert EXIT_CODE_MAP[type(exc_info.value)] == 11
        msg = str(exc_info.value)
        assert str(gflow_exe) in msg
        assert str(old_file) in msg
        assert exc_info.value.remediation_hint == uc._reinstall_hint(inst, None)

    def test_r4c_keyboard_interrupt_not_masked_when_restore_also_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Finding 2b: a KeyboardInterrupt inside the block still raises KeyboardInterrupt
        even when restore also fails."""
        gflow_exe = tmp_path / "gflow.exe"
        gflow_exe.write_text("original", encoding="utf-8")

        fake_dist = _FakeDistribution([gflow_exe])
        monkeypatch.setattr(uc, "distribution", lambda name: fake_dist)
        monkeypatch.setattr(sys, "platform", "win32")

        real_replace = os.replace

        def _fail_on_restore(src: str | Path, dst: str | Path) -> None:
            if str(dst) == str(gflow_exe):
                raise PermissionError(13, "Access denied", str(gflow_exe))
            real_replace(src, dst)

        monkeypatch.setattr(os, "replace", _fail_on_restore)

        inst = uc.Installer(
            "pip", (sys.executable, "-m", "pip", "install", "--upgrade", "gflow-cli")
        )
        with pytest.raises(KeyboardInterrupt), uc._launchers_set_aside(inst):
            raise KeyboardInterrupt

    def test_r3_permission_error_restores_and_raises_configuration_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """r3 os.replace raising PermissionError(13, ..., None, 32) on the 2nd launcher
        restores the 1st and raises ConfigurationError (exit 11). The manager is never called.
        The message names the path and does not suggest python -m."""
        from gflow_cli.errors import EXIT_CODE_MAP, ConfigurationError

        gflow1 = tmp_path / "gflow.exe"
        gflow2 = tmp_path / "flow.exe"
        gflow1.write_text("bin1", encoding="utf-8")
        gflow2.write_text("bin2", encoding="utf-8")

        fake_dist = _FakeDistribution([gflow1, gflow2])
        monkeypatch.setattr(uc, "distribution", lambda name: fake_dist)
        monkeypatch.setattr(sys, "platform", "win32")

        real_replace = os.replace

        def _fake_replace(src: str | Path, dst: str | Path) -> None:
            if str(src) == str(gflow2):
                raise _WinPermissionError(13, "Permission denied", str(gflow2), winerror=32)
            real_replace(src, dst)

        monkeypatch.setattr(os, "replace", _fake_replace)

        inst = uc.Installer(
            "pip", (sys.executable, "-m", "pip", "install", "--upgrade", "gflow-cli")
        )
        manager_called = False

        with pytest.raises(ConfigurationError) as exc_info:
            with uc._launchers_set_aside(inst):
                manager_called = True

        assert not manager_called
        assert EXIT_CODE_MAP[type(exc_info.value)] == 11
        msg = str(exc_info.value)
        assert f"`{gflow2}`" in msg
        assert "is in use by another program" in msg
        hint = exc_info.value.remediation_hint or ""
        assert "python -m" not in hint
        assert "close every program" in hint.lower()

        assert gflow1.read_text(encoding="utf-8") == "bin1"
        assert gflow2.read_text(encoding="utf-8") == "bin2"
        old_1 = tmp_path / f"gflow.exe.gflow-update-{os.getpid()}.old"
        assert not old_1.exists()

    def test_r4_keyboard_interrupt_restores_and_reraises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """r4 KeyboardInterrupt from the manager restores the moved launchers and re-raises."""
        gflow_exe = tmp_path / "gflow.exe"
        gflow_exe.write_text("original", encoding="utf-8")

        fake_dist = _FakeDistribution([gflow_exe])
        monkeypatch.setattr(uc, "distribution", lambda name: fake_dist)
        monkeypatch.setattr(sys, "platform", "win32")

        inst = uc.Installer(
            "pip", (sys.executable, "-m", "pip", "install", "--upgrade", "gflow-cli")
        )

        with pytest.raises(KeyboardInterrupt):
            with uc._launchers_set_aside(inst):
                raise KeyboardInterrupt

        assert gflow_exe.read_text(encoding="utf-8") == "original"
        old_file = tmp_path / f"gflow.exe.gflow-update-{os.getpid()}.old"
        assert not old_file.exists()

    def test_r5_exception_restores_and_reraises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """r5 any other exception from the manager does the same."""
        gflow_exe = tmp_path / "gflow.exe"
        gflow_exe.write_text("original", encoding="utf-8")

        fake_dist = _FakeDistribution([gflow_exe])
        monkeypatch.setattr(uc, "distribution", lambda name: fake_dist)
        monkeypatch.setattr(sys, "platform", "win32")

        inst = uc.Installer(
            "pip", (sys.executable, "-m", "pip", "install", "--upgrade", "gflow-cli")
        )

        with pytest.raises(RuntimeError, match="manager exploded"):
            with uc._launchers_set_aside(inst):
                raise RuntimeError("manager exploded")

        assert gflow_exe.read_text(encoding="utf-8") == "original"
        old_file = tmp_path / f"gflow.exe.gflow-update-{os.getpid()}.old"
        assert not old_file.exists()

    def test_r6_missing_record_launcher_refuses_exit_11_with_reinstall_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """r6 a RECORD launcher missing on disk refuses with exit 11, the hint is
        _reinstall_hint(inst, __version__), and the manager is not called."""
        from gflow_cli.errors import EXIT_CODE_MAP, ConfigurationError

        gflow_exe = tmp_path / "gflow.exe"
        fake_dist = _FakeDistribution([gflow_exe])
        monkeypatch.setattr(uc, "distribution", lambda name: fake_dist)
        monkeypatch.setattr(sys, "platform", "win32")

        inst = uc.Installer(
            "pip", (sys.executable, "-m", "pip", "install", "--upgrade", "gflow-cli")
        )
        manager_called = False

        with pytest.raises(ConfigurationError) as exc_info:
            with uc._launchers_set_aside(inst):
                manager_called = True

        assert not manager_called
        assert EXIT_CODE_MAP[type(exc_info.value)] == 11
        hint = exc_info.value.remediation_hint or ""
        expected_hint = uc._reinstall_hint(inst, __version__)
        assert hint == expected_hint

    def test_r7_execution_order_spied(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """r7 order: RECORD is read, THEN importlib.invalidate_caches and gc.collect,
        THEN the first os.replace. Spy on all three."""
        import gc
        import importlib

        gflow_exe = tmp_path / "gflow.exe"
        gflow_exe.write_text("original", encoding="utf-8")

        fake_dist = _FakeDistribution([gflow_exe])
        call_log: list[str] = []

        def _spy_distribution(name: str) -> _FakeDistribution:
            call_log.append("distribution")
            return fake_dist

        def _spy_invalidate_caches() -> None:
            call_log.append("invalidate_caches")

        def _spy_gc_collect() -> int:
            call_log.append("gc_collect")
            return 0

        real_replace = os.replace

        def _spy_replace(src: str | Path, dst: str | Path) -> None:
            call_log.append("replace")
            real_replace(src, dst)

        monkeypatch.setattr(uc, "distribution", _spy_distribution)
        monkeypatch.setattr(importlib, "invalidate_caches", _spy_invalidate_caches)
        monkeypatch.setattr(gc, "collect", _spy_gc_collect)
        monkeypatch.setattr(os, "replace", _spy_replace)
        monkeypatch.setattr(sys, "platform", "win32")

        inst = uc.Installer(
            "pip", (sys.executable, "-m", "pip", "install", "--upgrade", "gflow-cli")
        )

        with uc._launchers_set_aside(inst):
            pass

        assert call_log[:4] == ["distribution", "invalidate_caches", "gc_collect", "replace"]

    @pytest.mark.parametrize(
        ("inst_name", "platform"),
        [
            ("uv", "win32"),
            ("pipx", "win32"),
            ("pip", "linux"),
            ("pip", "darwin"),
        ],
    )
    def test_r8_unaffected_installers_do_not_rename(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inst_name: str, platform: str
    ) -> None:
        """r8 uv, pipx and non-win32 pip: no rename, the manager runs as before."""
        gflow_exe = tmp_path / "gflow.exe"
        gflow_exe.write_text("original", encoding="utf-8")

        fake_dist = _FakeDistribution([gflow_exe])
        monkeypatch.setattr(uc, "distribution", lambda name: fake_dist)
        monkeypatch.setattr(sys, "platform", platform)

        inst = uc.Installer(inst_name, ("dummy", "cmd"))
        manager_ran = False

        with uc._launchers_set_aside(inst):
            manager_ran = True
            assert gflow_exe.exists()
            assert list(tmp_path.glob("*.old")) == []

        assert manager_ran
        assert gflow_exe.exists()
        assert list(tmp_path.glob("*.old")) == []

    def test_r3b_access_denied_lets_pip_run_as_before(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Review of #1298: only WinError 32 means "in use". Access denied (an all-users
        Python, run without admin rights) also stops pip removing the launcher, so pip
        falls back as it did before the guard: no refusal, nothing left renamed."""
        first = tmp_path / "gflow.exe"
        second = tmp_path / "flow.exe"
        first.write_text("bin1", encoding="utf-8")
        second.write_text("bin2", encoding="utf-8")
        monkeypatch.setattr(uc, "distribution", lambda name: _FakeDistribution([first, second]))
        monkeypatch.setattr(sys, "platform", "win32")
        real_replace = os.replace

        def _fake_replace(src: str | Path, dst: str | Path) -> None:
            if str(src) == str(second):
                raise _WinPermissionError(13, "Access is denied", str(second), winerror=5)
            real_replace(src, dst)

        monkeypatch.setattr(os, "replace", _fake_replace)
        inst = uc.Installer("pip", (sys.executable, "-m", "pip", "install", "-U", "gflow-cli"))
        with uc._launchers_set_aside(inst):
            assert first.read_text(encoding="utf-8") == "bin1"  # put back before pip
            assert second.exists()
        assert list(tmp_path.glob("*.old")) == []

    def test_r4b_ctrl_c_during_the_renames_restores_what_moved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Review of #1298: a KeyboardInterrupt between two renames left the first
        launcher set aside, because only the pip phase was under the finally."""
        first = tmp_path / "flow.exe"
        second = tmp_path / "gflow.exe"
        first.write_text("bin1", encoding="utf-8")
        second.write_text("bin2", encoding="utf-8")
        monkeypatch.setattr(uc, "distribution", lambda name: _FakeDistribution([first, second]))
        monkeypatch.setattr(sys, "platform", "win32")
        real_replace = os.replace

        def _fake_replace(src: str | Path, dst: str | Path) -> None:
            if str(src) == str(second):
                raise KeyboardInterrupt
            real_replace(src, dst)

        monkeypatch.setattr(os, "replace", _fake_replace)
        inst = uc.Installer("pip", (sys.executable, "-m", "pip", "install", "-U", "gflow-cli"))
        with pytest.raises(KeyboardInterrupt), uc._launchers_set_aside(inst):
            pass
        assert first.read_text(encoding="utf-8") == "bin1"
        assert list(tmp_path.glob("*.old")) == []

    def test_r3c_vanished_launcher_mid_rename_restores_and_refuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Finding 3: the second launcher's os.replace raises FileNotFoundError; the first
        is restored, it refuses with ConfigurationError (exit 11), and the manager never runs."""
        from gflow_cli.errors import EXIT_CODE_MAP, ConfigurationError

        first = tmp_path / "gflow.exe"
        second = tmp_path / "flow.exe"
        first.write_text("bin1", encoding="utf-8")
        second.write_text("bin2", encoding="utf-8")
        monkeypatch.setattr(uc, "distribution", lambda name: _FakeDistribution([first, second]))
        monkeypatch.setattr(sys, "platform", "win32")

        real_replace = os.replace

        def _fake_replace(src: str | Path, dst: str | Path) -> None:
            if str(src) == str(second):
                raise FileNotFoundError(2, "No such file or directory", str(second))
            real_replace(src, dst)

        monkeypatch.setattr(os, "replace", _fake_replace)
        inst = uc.Installer("pip", (sys.executable, "-m", "pip", "install", "-U", "gflow-cli"))
        manager_called = False

        with pytest.raises(ConfigurationError) as exc_info:
            with uc._launchers_set_aside(inst):
                manager_called = True

        assert not manager_called
        assert EXIT_CODE_MAP[type(exc_info.value)] == 11
        assert (
            f"`{second}` disappeared during the update, so `gflow update` changed nothing"
            in str(exc_info.value)
        )
        assert exc_info.value.remediation_hint == (
            "Another `gflow update` may be running. Wait for it to finish, then run "
            "`gflow update` again."
        )
        assert first.read_text(encoding="utf-8") == "bin1"
        assert list(tmp_path.glob("*.old")) == []


class TestUserInstall:
    """`pip install --user` (#1298 e2e case C): upgraded with --user, probed with the
    user site visible, repaired with --user even after pip removed the metadata."""

    @pytest.fixture
    def user_site(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        import site

        site_dir = tmp_path / "user-site"
        site_dir.mkdir()
        monkeypatch.setattr(uc, "_installed_from_index", lambda: True)
        monkeypatch.setattr(uc.sys, "base_prefix", uc.sys.prefix)  # not a venv
        monkeypatch.setattr(site, "getusersitepackages", lambda: str(site_dir))

        class _Dist:
            def locate_file(self, rel: object) -> Path:
                return site_dir / str(rel)

        monkeypatch.setattr(uc, "distribution", lambda name: _Dist())
        return site_dir

    def test_a_user_site_install_upgrades_with_user(self, user_site: Path) -> None:
        inst = uc.installer()
        assert inst is not None
        assert inst.command[-2:] == ("--user", "gflow-cli")

    def test_a_venv_install_never_gets_user(
        self, user_site: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(uc.sys, "base_prefix", "/somewhere/else")
        inst = uc.installer()
        assert inst is not None
        assert "--user" not in inst.command

    def test_the_repair_hint_keeps_user_once_metadata_is_gone(self) -> None:
        inst = uc.Installer("pip", ("py", "-m", "pip", "install", "-U", "--user", "gflow-cli"))
        assert '--force-reinstall --user "gflow-cli==1.0"' in uc._reinstall_hint(inst, "1.0")

    def test_the_import_probe_keeps_the_user_site_but_not_the_cwd(
        self, user_site: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[list[str]] = []

        def _run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(uc.subprocess, "run", _run)
        assert uc._import_failure() is None
        assert seen[0][1:3] == ["-E", "-P"]


class TestSweepSetAside:
    def test_r9_sweep_set_aside_semantics(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """r9 the sweep deletes gflow.exe.gflow-update-123.old when gflow.exe exists,
        keeps it when gflow.exe is missing (r6 needs it), and skips an OSError on delete.
        It runs before run_update's 'already up to date' return."""
        gflow = tmp_path / "gflow.exe"
        gflow.write_text("live", encoding="utf-8")
        old1 = tmp_path / "gflow.exe.gflow-update-123.old"
        old1.write_text("old1", encoding="utf-8")

        old2 = tmp_path / "flow.exe.gflow-update-456.old"
        old2.write_text("old2", encoding="utf-8")

        other = tmp_path / "other.exe"
        other.write_text("other", encoding="utf-8")
        old3 = tmp_path / "other.exe.gflow-update-789.old"
        old3.write_text("old3", encoding="utf-8")

        fake_dist = _FakeDistribution(
            [
                gflow,
                tmp_path / "flow.exe",
                other,
            ]
        )
        monkeypatch.setattr(uc, "distribution", lambda name: fake_dist)
        monkeypatch.setattr(sys, "platform", "win32")

        real_remove = os.remove

        def _fake_remove(path: str | Path) -> None:
            if str(path) == str(old3):
                raise OSError(32, "File in use")
            real_remove(path)

        monkeypatch.setattr(os, "remove", _fake_remove)

        uc._sweep_set_aside()

        assert not old1.exists()
        assert old2.exists()
        assert old3.exists()

    def test_r9_sweep_runs_before_already_up_to_date_return(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sweep_called = False

        def _fake_sweep() -> None:
            nonlocal sweep_called
            sweep_called = True

        monkeypatch.setattr(uc, "_sweep_set_aside", _fake_sweep)
        monkeypatch.setattr(uc, "installer", lambda: uc.Installer("pip", ("cmd",)))
        monkeypatch.setattr(uc, "fetch_latest", lambda timeout: __version__)

        report = uc.run_update(check=True)
        assert sweep_called
        assert not report.upgraded
