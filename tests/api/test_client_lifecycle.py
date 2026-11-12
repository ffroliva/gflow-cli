"""FlowApiClient browser-lifecycle behaviour pinned against surviving mutants (#1249).

Each test fakes Playwright at the seam the client calls and asserts what a user or
an operator observes: the raised error's text, the incident ref it carries, the
script the browser receives, or the diagnostic event's fields.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import structlog

import gflow_cli.api.client as client_module
from gflow_cli.api.client import FlowApiClient
from gflow_cli.auth.cookies import ChromeCookieSnapshot
from gflow_cli.config import BrowserEngine, Settings
from gflow_cli.diagnostics import IncidentRecorder, IncidentRef
from gflow_cli.errors import ProfileLockedError

_WEBDRIVER_SCRIPT = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"


class TargetClosedError(Exception):  # noqa: N818 — mimics Playwright's class name
    pass


class _Stop(Exception):  # noqa: N818 — a control-flow sentinel, not an error
    """Ends a lifecycle run at the point under test."""


def _client(tmp_path: Path, *, capture: bool = False) -> FlowApiClient:
    return FlowApiClient(
        tmp_path / "profile", settings=Settings(home=tmp_path, incident_capture=capture)
    )


def _failing_pw(exc: Exception) -> Any:
    class _Chromium:
        async def launch_persistent_context(self, **_kw: Any) -> None:
            raise exc

    class _Pw:
        chromium = _Chromium()

    return _Pw()


class _PresettingRecorder:
    """A recorder whose capture already attached ref ``kept`` and returns ``other``."""

    def __init__(self, kept: IncidentRef, other: IncidentRef) -> None:
        self.kept = kept
        self.other = other

    def note_har_pre_launch(self, _har_path: object) -> None: ...

    async def capture_metadata_only(self, exc: Any, *, phase: str) -> IncidentRef:
        exc.incident_ref = self.kept
        return self.other


def _refs() -> tuple[IncidentRef, IncidentRef]:
    return (
        IncidentRef(id="kept", capture_status="complete", path=None, artifacts=()),
        IncidentRef(id="other", capture_status="complete", path=None, artifacts=()),
    )


@pytest.fixture
def log_cap() -> Iterator[structlog.testing.LogCapture]:
    cap = structlog.testing.LogCapture()
    with patch("gflow_cli.api.client.logger", structlog.wrap_logger(None, processors=[cap])):
        yield cap


def _event(cap: structlog.testing.LogCapture, name: str) -> dict[str, Any]:
    return next(e for e in cap.entries if e["event"] == name)


# --- _launch_persistent_context ---------------------------------------------------


@pytest.mark.asyncio
class TestLaunchErrors:
    async def test_multi_line_launch_crash_quotes_only_its_first_line(self, tmp_path: Path) -> None:
        client = _client(tmp_path)
        client._pw = _failing_pw(TargetClosedError("browser has been closed\nCall log: ..."))  # noqa: SLF001
        with pytest.raises(ProfileLockedError) as excinfo:
            await client._launch_persistent_context({})  # noqa: SLF001
        assert "(original error: browser has been closed)" in excinfo.value.detail

    async def test_empty_launch_crash_quotes_the_exception_type(self, tmp_path: Path) -> None:
        client = _client(tmp_path)
        client._pw = _failing_pw(TargetClosedError())  # noqa: SLF001
        with pytest.raises(ProfileLockedError) as excinfo:
            await client._launch_persistent_context({})  # noqa: SLF001
        assert "(original error: TargetClosedError)" in excinfo.value.detail

    async def test_launch_crash_keeps_an_incident_ref_already_attached(
        self, tmp_path: Path
    ) -> None:
        client = _client(tmp_path)
        kept, other = _refs()
        client._recorder = _PresettingRecorder(kept, other)  # type: ignore[assignment]  # noqa: SLF001
        client._pw = _failing_pw(TargetClosedError("closed"))  # noqa: SLF001
        with pytest.raises(ProfileLockedError) as excinfo:
            await client._launch_persistent_context({})  # noqa: SLF001
        assert excinfo.value.incident_ref is kept

    async def test_profile_in_use_error_names_the_real_profile_dir(self, tmp_path: Path) -> None:
        client = _client(tmp_path)
        client._pw = _failing_pw(  # noqa: SLF001
            RuntimeError("the profile is already in use by another instance of Chromium")
        )
        with pytest.raises(ProfileLockedError) as excinfo:
            await client._launch_persistent_context({})  # noqa: SLF001
        assert f"profile dir {tmp_path / 'profile'} is already in use" in excinfo.value.detail


# --- _enter_setup ---------------------------------------------------------------


class _ContendedLease:
    def __init__(self, _profile_dir: object, exc: ProfileLockedError) -> None:
        self._exc = exc

    async def aacquire(self) -> None:
        raise self._exc


class _FreeLease:
    def __init__(self, _profile_dir: object) -> None: ...

    async def aacquire(self) -> _FreeLease:
        return self

    def release(self) -> None: ...


def _stub_pre_launch(client: FlowApiClient) -> None:
    async def _no_preread() -> None:
        return None

    client._pw = object()  # type: ignore[assignment]  # noqa: SLF001
    client._preread_flow_session_cookies = _no_preread  # type: ignore[method-assign]  # noqa: SLF001
    client._persistent_context_kwargs = lambda: {}  # type: ignore[method-assign]  # noqa: SLF001
    client._log_and_guard_launch = lambda kwargs: None  # type: ignore[method-assign]  # noqa: SLF001


@pytest.mark.asyncio
class TestEnterSetup:
    async def test_lease_contention_without_a_recorder_raises_the_lock_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(tmp_path)
        _stub_pre_launch(client)
        locked = ProfileLockedError("held elsewhere")
        monkeypatch.setattr(client_module, "ProfileLease", lambda d: _ContendedLease(d, locked))
        with pytest.raises(ProfileLockedError) as excinfo:
            await client._enter_setup()  # noqa: SLF001
        assert excinfo.value is locked

    async def test_lease_contention_keeps_an_incident_ref_already_attached(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(tmp_path)
        _stub_pre_launch(client)
        kept, other = _refs()
        client._recorder = _PresettingRecorder(kept, other)  # type: ignore[assignment]  # noqa: SLF001
        locked = ProfileLockedError("held elsewhere")
        monkeypatch.setattr(client_module, "ProfileLease", lambda d: _ContendedLease(d, locked))
        with pytest.raises(ProfileLockedError) as excinfo:
            await client._enter_setup()  # noqa: SLF001
        assert excinfo.value.incident_ref is kept

    async def test_launched_context_gets_the_webdriver_hiding_script(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(tmp_path)
        _stub_pre_launch(client)
        monkeypatch.setattr(client_module, "ProfileLease", _FreeLease)
        monkeypatch.setattr(client_module, "ensure_profile_hardened", lambda _d: None)
        scripts: list[str] = []

        class _Context:
            async def add_init_script(self, script: str) -> None:
                scripts.append(script)
                raise _Stop

        async def _launch(_kwargs: object) -> _Context:
            return _Context()

        client._launch_persistent_context = _launch  # type: ignore[method-assign, assignment]  # noqa: SLF001
        with pytest.raises(_Stop):
            await client._enter_setup()  # noqa: SLF001
        assert scripts == [_WEBDRIVER_SCRIPT]


# --- _build_transport_setup -------------------------------------------------------


def test_transport_records_generation_requests_when_capture_is_on(tmp_path: Path) -> None:
    client = _client(tmp_path, capture=True)
    recorder = IncidentRecorder(client.settings)
    client._recorder = recorder  # noqa: SLF001
    setup = client._build_transport_setup()  # noqa: SLF001
    assert setup.record_generation_request == recorder.record_generation_request


def test_transport_records_nothing_when_capture_is_off(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client._recorder = IncidentRecorder(client.settings)  # noqa: SLF001
    assert client._build_transport_setup().record_generation_request is None  # noqa: SLF001


# --- __aenter__ incident retention ---------------------------------------------------


@pytest.fixture
def retention(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Records retention sweeps; the Playwright start that follows raises _Stop."""
    swept: list[Path] = []

    class _NoPlaywright:
        def start(self) -> None:
            raise _Stop

    monkeypatch.setattr(client_module, "run_retention", swept.append)
    monkeypatch.setattr(client_module, "active_engine", lambda: BrowserEngine.PLAYWRIGHT)
    monkeypatch.setattr(client_module, "async_playwright", _NoPlaywright)
    return swept


async def _enter_until_playwright(client: FlowApiClient) -> None:
    with pytest.raises(_Stop):
        await client.__aenter__()


@pytest.mark.asyncio
class TestRetention:
    async def test_runs_with_capture_off_when_an_incidents_dir_exists(
        self, tmp_path: Path, retention: list[Path]
    ) -> None:
        (tmp_path / "incidents").mkdir()
        await _enter_until_playwright(_client(tmp_path))
        assert retention == [client_module.validated_incidents_root(tmp_path)]

    async def test_skipped_with_capture_off_and_no_incidents_dir(
        self, tmp_path: Path, retention: list[Path]
    ) -> None:
        await _enter_until_playwright(_client(tmp_path))
        assert retention == []

    async def test_skipped_when_the_incidents_root_does_not_validate(
        self, tmp_path: Path, retention: list[Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(client_module, "validated_incidents_root", lambda _home: None)
        await _enter_until_playwright(_client(tmp_path, capture=True))
        assert retention == []


# --- health_check -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_reads_the_document_hostname(tmp_path: Path) -> None:
    import asyncio

    class _Page:
        async def evaluate(self, script: str) -> str:
            return "labs.google" if script == "() => document.location.hostname" else ""

    client = _client(tmp_path)
    client._page_queue = asyncio.Queue(maxsize=1)  # noqa: SLF001
    client._page_queue.put_nowait(_Page())  # type: ignore[arg-type]  # noqa: SLF001
    assert await client.health_check() is True


# --- diagnostic events ---------------------------------------------------------------


def test_launch_event_names_the_proxy_and_cookie_db(
    tmp_path: Path, log_cap: structlog.testing.LogCapture
) -> None:
    profile = tmp_path / "profile"
    cookies = profile / "Default" / "Network" / "Cookies"
    cookies.parent.mkdir(parents=True)
    cookies.write_bytes(b"")
    client = FlowApiClient(profile, settings=Settings(home=tmp_path))
    client._log_and_guard_launch(  # noqa: SLF001
        {"channel": "chrome", "proxy": {"server": "http://proxy.example:8080"}}
    )
    ev = _event(log_cap, "client.persistent_context_launch")
    assert ev["proxy"] == "http://proxy.example:8080"
    assert ev["cookies_db_path"] == str(cookies)


def test_preread_event_reports_the_session_cookie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, log_cap: structlog.testing.LogCapture
) -> None:
    import asyncio

    snap = ChromeCookieSnapshot(
        httpx_cookies={"__Secure-next-auth.session-token": "tok"}, google_session=True
    )

    async def _fake_snapshot(_profile_dir: Path) -> ChromeCookieSnapshot:
        return snap

    monkeypatch.setattr("gflow_cli.auth.cookies.get_chrome_cookie_snapshot", _fake_snapshot)
    asyncio.run(_client(tmp_path)._preread_flow_session_cookies())  # noqa: SLF001
    assert _event(log_cap, "client.preread_flow_cookies")["preread_session"] is True
