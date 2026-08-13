"""Tests for the once-a-day PyPI update notice (#479).

`tests/conftest.py::_isolate_settings` (autouse) redirects GFLOW_CLI_HOME to a
per-test tmp dir, so the cache file lands in an isolated home. The notice is
best-effort by contract: every failure path resolves to "no notice", never an
exception, and the check must never block the command (the network refresh
runs on a daemon thread and only ever feeds the NEXT invocation's notice).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from gflow_cli import __version__
from gflow_cli import update_check as uc
from gflow_cli.config import get_settings, reset_settings

if TYPE_CHECKING:
    from collections.abc import Callable


def _write_cache(latest: str, *, age_seconds: float = 0.0) -> Path:
    cache_path = get_settings().home / "update_check.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"checked_at": time.time() - age_seconds, "latest": latest}),
        encoding="utf-8",
    )
    return cache_path


@pytest.fixture
def index_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend gflow-cli is an index-installed wheel (the notify-eligible case)."""
    monkeypatch.setattr(uc, "_installed_from_index", lambda: True)


class TestMaybeNotifyUpdate:
    def test_notice_when_cache_has_newer_version(
        self, index_install: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CI", raising=False)
        _write_cache("999.0.0")
        notice = uc.maybe_notify_update()
        assert notice is not None
        assert "999.0.0" in notice
        assert __version__ in notice

    def test_no_notice_when_cache_equal_or_older(
        self, index_install: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CI", raising=False)
        _write_cache(__version__)
        assert uc.maybe_notify_update() is None
        _write_cache("0.0.1")
        assert uc.maybe_notify_update() is None

    def test_skipped_in_ci(self, index_install: None, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CI", "true")
        _write_cache("999.0.0")
        assert uc.maybe_notify_update() is None

    def test_skipped_when_disabled(
        self, index_install: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("GFLOW_CLI_UPDATE_CHECK", "0")
        reset_settings()
        _write_cache("999.0.0")
        assert uc.maybe_notify_update() is None

    def test_skipped_for_non_index_install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setattr(uc, "_installed_from_index", lambda: False)
        _write_cache("999.0.0")
        assert uc.maybe_notify_update() is None

    def test_fresh_cache_spawns_no_refresh(
        self, index_install: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CI", raising=False)
        _write_cache("999.0.0", age_seconds=60)
        spawned: list[object] = []
        monkeypatch.setattr(uc.threading, "Thread", lambda **kw: spawned.append(kw))
        assert uc.maybe_notify_update() is not None
        assert spawned == []

    def test_stale_cache_spawns_daemon_refresh(
        self, index_install: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CI", raising=False)
        _write_cache("999.0.0", age_seconds=uc._CHECK_INTERVAL_SECONDS + 60)

        started: list[dict[str, object]] = []

        class _FakeThread:
            def __init__(self, **kwargs: object) -> None:
                started.append(kwargs)

            def start(self) -> None: ...

        monkeypatch.setattr(uc.threading, "Thread", _FakeThread)
        # Stale latest still produces a notice (from the previous fetch) while
        # the refresh runs in the background for the NEXT invocation.
        assert uc.maybe_notify_update() is not None
        assert len(started) == 1
        assert started[0].get("daemon") is True

    def test_missing_or_corrupt_cache_is_no_notice(
        self, index_install: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setattr(uc.threading, "Thread", lambda **kw: _NoopThread())
        assert uc.maybe_notify_update() is None  # no cache at all
        cache_path = get_settings().home / "update_check.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text("{not json", encoding="utf-8")
        assert uc.maybe_notify_update() is None

    def test_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Best-effort by contract: even a broken settings layer yields None."""

        def _boom() -> object:
            raise RuntimeError("settings exploded")

        monkeypatch.setattr(uc, "get_settings", _boom)
        assert uc.maybe_notify_update() is None


class _NoopThread:
    def start(self) -> None: ...


class TestRefreshCache:
    def test_refresh_writes_latest_and_checked_at(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cache_path = get_settings().home / "update_check.json"

        class _Resp:
            def raise_for_status(self) -> None: ...

            def json(self) -> dict[str, dict[str, str]]:
                return {"info": {"version": "1.2.3"}}

        monkeypatch.setattr(uc.httpx, "get", lambda *a, **k: _Resp())
        uc._refresh_cache(cache_path)
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        assert data["latest"] == "1.2.3"
        assert data["checked_at"] == pytest.approx(time.time(), abs=30)

    def test_refresh_failure_still_stamps_checked_at_and_keeps_latest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed poll must not retry on every invocation (once-a-day cap)
        and must not erase a previously fetched latest."""
        cache_path = _write_cache("1.2.3", age_seconds=999_999)

        def _fail(*a: object, **k: object) -> object:
            raise OSError("offline")

        monkeypatch.setattr(uc.httpx, "get", _fail)
        uc._refresh_cache(cache_path)
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        assert data["latest"] == "1.2.3"
        assert data["checked_at"] == pytest.approx(time.time(), abs=30)


class TestVersionCompare:
    @pytest.mark.parametrize(
        ("latest", "installed", "newer"),
        [
            ("0.56.0", "0.55.0", True),
            ("1.0.0", "0.99.9", True),
            ("0.55.0", "0.55.0", False),
            ("0.54.9", "0.55.0", False),
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

    def test_editable_install_is_not(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = json.dumps({"url": "file:///dev/gflow-cli", "dir_info": {"editable": True}})
        assert self._with_direct_url(monkeypatch, payload)() is False

    def test_local_source_install_is_not(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = json.dumps({"url": "file:///dev/gflow-cli", "dir_info": {}})
        assert self._with_direct_url(monkeypatch, payload)() is False

    def test_missing_distribution_is_not(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from importlib.metadata import PackageNotFoundError

        def _raise(_name: str) -> object:
            raise PackageNotFoundError("gflow-cli")

        monkeypatch.setattr(uc, "distribution", _raise)
        assert uc._installed_from_index() is False
