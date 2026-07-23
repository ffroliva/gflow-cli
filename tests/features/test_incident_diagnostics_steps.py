"""Step bindings for incident_diagnostics.feature (Task 12 — S14/S15/S20/S21).

Scoped to this feature only. The gherkin documents the operator contract; the
bindings exercise the highest practical offline seam for each scenario —
IncidentRecorder / FlowApiClient teardown / RFC 9457 projection — with fakes
only (the autouse tripwire in conftest.py forbids live Playwright).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
import structlog
from pytest_bdd import given, scenarios, then, when

from gflow_cli.config import Settings
from gflow_cli.diagnostics import BundleDir, IncidentRecorder, IncidentRef
from gflow_cli.errors import (
    FlowAppError,
    ProfileLockedError,
    UiSelectorDriftError,
)
from gflow_cli.json_output import error_payload, exit_code_for
from gflow_cli.mcp.tools import _gflow_error_dict
from gflow_cli.profile_lease import ProfileLease

scenarios("incident_diagnostics.feature")

CANARY = "SECRETCANARY-bdd"


@pytest.fixture
def state(tmp_path: Path) -> dict[str, Any]:
    return {"home": tmp_path, "settings": Settings(home=tmp_path)}


def _bundles(home: Path) -> list[Path]:
    root = home / "incidents"
    if not root.is_dir():
        return []
    return [b for day in root.iterdir() if day.is_dir() for b in day.iterdir() if b.is_dir()]


class _ObservingPage:
    """Fake page that records calls — proves observation-only capture (S15)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def evaluate(self, script: str, /) -> dict[str, Any]:
        self.calls.append("evaluate")
        return {"ligatures": ["crop_landscape"]}

    async def screenshot(self, *, path: str, full_page: bool = False) -> None:
        self.calls.append("screenshot")
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\nfake")


# --- Scenario: Capture failure preserves the operational error ---------------


@given("a Flow UI failure with exit code 31", target_fixture="failure")
def _failure(state: dict[str, Any]) -> dict[str, Any]:
    return {"exc": FlowAppError(f"crash {CANARY}"), "page": _ObservingPage()}


@given("the incident directory is read-only")
def _readonly_root(state: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def _denied(*_a: object, **_k: object) -> BundleDir:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(BundleDir, "create_exclusive", classmethod(_denied))


@when("the command handles the failure")
def _handle_failure(state: dict[str, Any], failure: dict[str, Any]) -> None:
    cap = structlog.testing.LogCapture()
    structlog.configure(processors=[cap])
    recorder = IncidentRecorder(state["settings"])
    ref = asyncio.run(
        recorder.capture_failure(failure["exc"], page=failure["page"], phase="mode_switch")
    )
    structlog.reset_defaults()
    state["ref"] = ref
    state["log_entries"] = cap.entries
    state["exit_code"] = exit_code_for(failure["exc"])


@then("the command exits with code 31")
def _exit_31(state: dict[str, Any]) -> None:
    assert state["exit_code"] == 31


@then("no raw exception text is emitted")
def _no_raw_text(state: dict[str, Any]) -> None:
    assert CANARY not in repr(state["log_entries"])


@then("incident capture does not retry generation")
def _no_retry(state: dict[str, Any], failure: dict[str, Any]) -> None:
    # Observation-only: the page saw at most evaluate/screenshot — no goto,
    # click, fill, or resubmission of any kind (S15).
    assert set(failure["page"].calls) <= {"evaluate", "screenshot"}


# --- Scenario: A systemic batch failure is captured once ---------------------


@given("a manifest with fifty rows", target_fixture="batch")
def _batch(state: dict[str, Any]) -> dict[str, Any]:
    return {"rows": 50}


@given("every row hits the same selector failure")
def _same_failure(batch: dict[str, Any]) -> None:
    batch["exc_factory"] = lambda: UiSelectorDriftError("probe=mode_switch_trigger: miss")


@when("the manifest runs with continue-on-error")
def _run_batch(state: dict[str, Any], batch: dict[str, Any]) -> None:
    recorder = IncidentRecorder(state["settings"])

    async def _drive() -> None:
        for _ in range(batch["rows"]):
            # continue-on-error: every row's failure reaches the capture
            # boundary and the command keeps going.
            await recorder.capture_failure(
                batch["exc_factory"](), page=_ObservingPage(), phase="image_batch"
            )
        await recorder.finalize_all(close_ok=True)

    asyncio.run(_drive())
    state["recorder"] = recorder


@then("one incident bundle is staged for that fingerprint")
def _one_bundle(state: dict[str, Any]) -> None:
    assert len(_bundles(state["home"])) == 1


@then("the manifest records forty-nine suppressed occurrences")
def _suppressed(state: dict[str, Any]) -> None:
    manifest_path = _bundles(state["home"])[0] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["suppressed_count"] == 49


@then("no more than three distinct bundles exist for the command")
def _bundle_cap(state: dict[str, Any]) -> None:
    assert len(_bundles(state["home"])) <= 3


# --- Scenario: Profile contention reports evidence but never reclaims --------


@given("another process holds the selected profile lease", target_fixture="held")
def _held_lease(state: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("GFLOW_CLI_HOME", str(state["home"]))
    from gflow_cli import config as config_module

    config_module.reset_settings()
    holder = ProfileLease(state["home"] / "profile").acquire()
    return {"holder": holder, "lock_path": holder.lock_path}


@when("a generation command starts")
def _start_generation(state: dict[str, Any], held: dict[str, Any]) -> None:
    recorder = IncidentRecorder(state["settings"])
    chrome_launched: list[bool] = []
    try:
        try:
            ProfileLease(state["home"] / "profile").acquire()
            chrome_launched.append(True)  # would only proceed to launch on success
        except ProfileLockedError as exc:
            ref = asyncio.run(recorder.capture_metadata_only(exc, phase="profile_lease"))
            if ref is not None:
                exc.incident_ref = ref
            asyncio.run(recorder.finalize_all(close_ok=True))
            state["exc"] = exc
    finally:
        held["holder"].release()
    state["chrome_launched"] = bool(chrome_launched)


@then("it exits with ProfileLockedError code 11 before Chrome launches")
def _exit_11(state: dict[str, Any]) -> None:
    assert exit_code_for(state["exc"]) == 11
    assert state["chrome_launched"] is False


@then("a metadata-only incident contains validated owner evidence")
def _owner_evidence(state: dict[str, Any]) -> None:
    exc = state["exc"]
    assert exc.incident_ref is not None
    assert exc.incident_ref.path is not None
    assert not (exc.incident_ref.path / "ui.json").exists()  # metadata-only
    evidence = exc.owner_evidence
    assert evidence is not None
    assert evidence.pid == os.getpid()


@then("no lock file or process is deleted")
def _no_reclaim(state: dict[str, Any], held: dict[str, Any]) -> None:
    assert held["lock_path"].exists()


# --- Scenario: Remote errors do not expose local incident paths --------------


@given(
    "an incident was captured under a home path containing a username",
    target_fixture="remote_exc",
)
def _captured_incident() -> FlowAppError:
    exc = FlowAppError("crash")
    exc.incident_ref = IncidentRef(
        id="corr-fp",
        capture_status="complete",
        path=Path("/home/CANARYUSER/gflow/incidents/2026-07-22/x"),
        artifacts=("ui.json", "sensitive/screenshot.png"),
    )
    return exc


@when("the failure is returned through MCP or HTTP")
def _through_mcp(state: dict[str, Any], remote_exc: FlowAppError) -> None:
    state["envelope"] = _gflow_error_dict(remote_exc)
    state["local_payload"] = error_payload(remote_exc)


@then("the response contains an opaque incident id and status")
def _opaque_incident(state: dict[str, Any]) -> None:
    assert state["envelope"]["incident"] == {"id": "corr-fp", "capture_status": "complete"}


@then("it does not contain the absolute path or username")
def _no_path_leak(state: dict[str, Any]) -> None:
    blob = json.dumps(state["envelope"])
    assert "CANARYUSER" not in blob
    assert "incidents" not in blob


# --- Scenario: Cancellation leaves no browser or lease -----------------------


@given("incident capture is staging DOM evidence", target_fixture="teardown_state")
def _staging(state: dict[str, Any]) -> dict[str, Any]:
    from gflow_cli.api.client import FlowApiClient

    # HAR configured but the session will not finalize it cleanly (S32).
    settings = Settings(home=state["home"], har_path=state["home"] / "session.har")
    client = FlowApiClient(state["home"] / "profile", settings=settings)
    recorder = IncidentRecorder(settings)
    recorder.note_har_pre_launch(settings.har_path)
    client._recorder = recorder  # noqa: SLF001
    ref = asyncio.run(recorder.capture_metadata_only(FlowAppError("crash"), phase="staging"))
    assert ref is not None
    return {"client": client, "recorder": recorder, "ref": ref}


@when("cancellation arrives during browser context close")
def _cancel_during_close(state: dict[str, Any], teardown_state: dict[str, Any]) -> None:
    client = teardown_state["client"]

    class _CancellingContext:
        async def close(self) -> None:
            raise asyncio.CancelledError

    class _Pw:
        def __init__(self) -> None:
            self.stopped = False

        async def stop(self) -> None:
            self.stopped = True

    class _Lease:
        def __init__(self) -> None:
            self.released = False

        def release(self) -> None:
            self.released = True

    pw, lease = _Pw(), _Lease()
    client._context = _CancellingContext()  # type: ignore[assignment]  # noqa: SLF001
    client._pw = pw  # type: ignore[assignment]  # noqa: SLF001
    client._lease = lease  # type: ignore[assignment]  # noqa: SLF001

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(client._close_browser_resources())  # noqa: SLF001
    state["pw"], state["lease"] = pw, lease
    state["cancelled"] = True


@then("HAR state is possibly incomplete")
def _har_possibly_incomplete(state: dict[str, Any], teardown_state: dict[str, Any]) -> None:
    recorder = teardown_state["recorder"]
    assert recorder.resolve_har_state(close_ok=False) == "possibly_incomplete"
    ref = teardown_state["ref"]
    manifest = json.loads((ref.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["har_state"] == "possibly_incomplete"


@then("the original cancellation propagates")
def _cancellation_propagates(state: dict[str, Any]) -> None:
    assert state["cancelled"] is True


@then("the driver stops and the profile lease is released")
def _teardown_complete(state: dict[str, Any]) -> None:
    assert state["pw"].stopped is True
    assert state["lease"].released is True


# --- Scenario: Successful generation creates no incident ---------------------


@given("incident capture is enabled", target_fixture="success_recorder")
def _enabled(state: dict[str, Any]) -> IncidentRecorder:
    recorder = IncidentRecorder(state["settings"])
    assert recorder.enabled  # default true
    return recorder


@when("a generation completes successfully")
def _success(state: dict[str, Any], success_recorder: IncidentRecorder) -> None:
    # A successful command crosses no failure boundary: normal traffic flows
    # through the journals, then teardown finalizes with nothing staged.
    success_recorder.record_response(
        url="https://aisandbox-pa.googleapis.com/v1/flow/uploadImage",
        method="POST",
        resource_type="xhr",
        status=200,
        request_key="r1",
        monotonic_ts=1.0,
    )
    artifact = state["home"] / "out.png"
    artifact.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    state["artifact"] = artifact
    asyncio.run(success_recorder.finalize_all(close_ok=True))


@then("the media artifact is valid")
def _artifact_valid(state: dict[str, Any]) -> None:
    assert state["artifact"].read_bytes().startswith(b"\x89PNG")


@then("no incident directory is created for the command")
def _no_incident(state: dict[str, Any]) -> None:
    assert _bundles(state["home"]) == []
