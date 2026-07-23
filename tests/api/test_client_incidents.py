"""FlowApiClient ↔ IncidentRecorder wiring (Task 10 — S05, S07, S15-S17,
S20, S30, S33/S34). Fake contexts/pages only — no Playwright processes."""

from __future__ import annotations

import gc
import json
import weakref
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.config import Settings
from gflow_cli.diagnostics import IncidentRecorder
from gflow_cli.errors import FlowAppError, ProfileLockedError

CANARY = "SECRETCANARY-w1r1ng"


class FakeEventTarget:
    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}
        self.removed: list[str] = []
        self.order: list[str] = []

    def on(self, event: str, handler: Any) -> None:
        self.order.append(f"on:{event}")
        self.handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler: Any) -> None:
        self.removed.append(event)
        self.handlers.get(event, []).remove(handler)

    def emit(self, event: str, payload: Any) -> None:
        for handler in self.handlers.get(event, []):
            handler(payload)


class FakeContext(FakeEventTarget):
    def __init__(self, *, on_close: Any = None) -> None:
        super().__init__()
        self.closed = False
        self._on_close = on_close

    async def close(self) -> None:
        if self._on_close is not None:
            self._on_close()
        self.order.append("close")
        self.closed = True


class FakePw:
    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class FakeRequest:
    def __init__(self, url: str, failure: str | None = None) -> None:
        self.url = url
        self.method = "POST"
        self.resource_type = "xhr"
        self.failure = failure


class FakeResponse:
    def __init__(self, request: FakeRequest, status: int) -> None:
        self.request = request
        self.url = request.url
        self.status = status


class FakeLease:
    def __init__(self) -> None:
        self.released = False

    def release(self) -> None:
        self.released = True


def _client(tmp_path: Path) -> FlowApiClient:
    settings = Settings(home=tmp_path)
    client = FlowApiClient(tmp_path / "profile", settings=settings)
    client._recorder = IncidentRecorder(settings)
    return client


def _bundles(tmp_path: Path) -> list[Path]:
    root = tmp_path / "incidents"
    if not root.is_dir():
        return []
    return [b for day in root.iterdir() if day.is_dir() for b in day.iterdir()]


class TestListenerWiring:
    def test_context_listeners_cover_all_relevant_events(self, tmp_path: Path) -> None:
        """S30: journal listeners registered on the context (before any
        navigation — _enter_setup attaches immediately after launch)."""
        client = _client(tmp_path)
        context = FakeContext()
        client._attach_recorder_context(context)  # noqa: SLF001
        assert set(context.handlers) == {"request", "response", "requestfailed", "page"}

    def test_attach_is_idempotent_per_target(self, tmp_path: Path) -> None:
        client = _client(tmp_path)
        context = FakeContext()
        client._attach_recorder_context(context)  # noqa: SLF001
        client._attach_recorder_context(context)  # noqa: SLF001
        assert all(len(handlers) == 1 for handlers in context.handlers.values())

    def test_pooled_pages_each_attached_exactly_once(self, tmp_path: Path) -> None:
        """S16: explicit pool attach + context "page" event never double-attach."""
        client = _client(tmp_path)
        context = FakeContext()
        client._attach_recorder_context(context)  # noqa: SLF001
        page = FakeEventTarget()
        client._attach_recorder_page(page)  # noqa: SLF001
        context.emit("page", page)  # late "page" event for an already-known page
        assert len(page.handlers.get("console", [])) == 1
        assert len(page.handlers.get("pageerror", [])) == 1

    def test_handlers_feed_recorder_with_reduced_primitives(self, tmp_path: Path) -> None:
        """S01/S31 wiring: handler output is reduced — no canary, no object."""
        client = _client(tmp_path)
        recorder = client._recorder  # noqa: SLF001
        assert recorder is not None
        context = FakeContext()
        client._attach_recorder_context(context)  # noqa: SLF001
        request = FakeRequest(f"https://evil.example/{CANARY}")
        context.emit("request", request)
        context.emit("response", FakeResponse(request, 500))
        context.emit(
            "requestfailed", FakeRequest(f"https://labs.google/{CANARY}", "net::ERR_FAILED")
        )
        snap = recorder.journal.snapshot()
        assert len(snap.network) == 2
        assert snap.network[0].host_category == "other"
        assert snap.network[1].status_or_failure == "net::ERR_FAILED"
        assert CANARY not in repr(snap)

    def test_late_callbacks_after_detach_are_noops(self, tmp_path: Path) -> None:
        """S17: detach freezes journals; saved handler references become inert."""
        client = _client(tmp_path)
        recorder = client._recorder  # noqa: SLF001
        assert recorder is not None
        context = FakeContext()
        client._attach_recorder_context(context)  # noqa: SLF001
        saved = dict(context.handlers)
        client._detach_recorder()  # noqa: SLF001
        assert set(context.removed) == {"request", "response", "requestfailed", "page"}
        request = FakeRequest("https://labs.google/x")
        for handlers in saved.values():
            for handler in handlers:
                try:
                    handler(FakeResponse(request, 200))
                except Exception:  # noqa: BLE001,S110 — inert is the contract, not typed
                    pass
        assert len(recorder.journal.snapshot().network) == 0

    def test_callbacks_retain_no_request_objects(self, tmp_path: Path) -> None:
        """S18: after the event is journaled the object must be collectable."""
        client = _client(tmp_path)
        context = FakeContext()
        client._attach_recorder_context(context)  # noqa: SLF001
        request = FakeRequest("https://labs.google/x")
        context.emit("request", request)
        context.emit("response", FakeResponse(request, 200))
        ref = weakref.ref(request)
        del request
        gc.collect()
        assert ref() is None


@pytest.mark.asyncio
class TestLifecycle:
    async def test_lease_contention_metadata_only_before_chrome(self, tmp_path: Path) -> None:
        """S07 wiring: ProfileLockedError in _enter_setup produces a
        metadata-only incident, attaches the ref, and never launches Chrome."""
        client = _client(tmp_path)
        client._pw = FakePw()  # type: ignore[assignment]  # noqa: SLF001

        async def _no_preread() -> None:
            return None

        client._preread_flow_session_cookies = _no_preread  # type: ignore[method-assign]  # noqa: SLF001
        client._persistent_context_kwargs = lambda: {}  # type: ignore[method-assign]  # noqa: SLF001
        client._log_and_guard_launch = lambda kwargs: None  # type: ignore[method-assign]  # noqa: SLF001

        async def _forbidden_launch(kwargs: object) -> None:
            raise AssertionError("Chrome must not launch on lease contention")

        client._launch_persistent_context = _forbidden_launch  # type: ignore[method-assign]  # noqa: SLF001

        import gflow_cli.api.client as client_module

        class _ContendedLease:
            def __init__(self, _profile_dir: object) -> None: ...

            def acquire(self) -> None:
                raise ProfileLockedError("held elsewhere")

        original = client_module.ProfileLease
        client_module.ProfileLease = _ContendedLease  # type: ignore[assignment, misc]
        try:
            with pytest.raises(ProfileLockedError) as excinfo:
                await client._enter_setup()  # noqa: SLF001
        finally:
            client_module.ProfileLease = original  # type: ignore[misc]
        assert excinfo.value.incident_ref is not None
        assert excinfo.value.incident_ref.path is not None
        recorder = client._recorder  # noqa: SLF001
        assert recorder is not None
        # HAR honesty was primed before the lease attempt (S05/S32 wiring).
        assert recorder.resolve_har_state(close_ok=True) == "disabled"
        bundles = _bundles(tmp_path)
        assert len(bundles) == 1
        assert not (bundles[0] / "ui.json").exists()

    async def test_teardown_detaches_freezes_then_finalizes_and_releases(
        self, tmp_path: Path
    ) -> None:
        """S20/S33 ordering: freeze BEFORE context close; finalize after close;
        driver stopped; lease released; manifest written."""
        client = _client(tmp_path)
        recorder = client._recorder  # noqa: SLF001
        assert recorder is not None
        ref = await recorder.capture_metadata_only(FlowAppError("crash"), phase="test")
        assert ref is not None and ref.path is not None

        frozen_at_close: list[bool] = []
        context = FakeContext(on_close=lambda: frozen_at_close.append(recorder._frozen))  # noqa: SLF001
        client._attach_recorder_context(context)  # noqa: SLF001
        client._context = context  # type: ignore[assignment]  # noqa: SLF001
        pw = FakePw()
        client._pw = pw  # type: ignore[assignment]  # noqa: SLF001
        lease = FakeLease()
        client._lease = lease  # type: ignore[assignment]  # noqa: SLF001

        await client._close_browser_resources()  # noqa: SLF001

        assert frozen_at_close == [True]  # detach/freeze happened BEFORE close
        assert context.closed
        assert pw.stopped
        assert lease.released
        manifest = json.loads((ref.path / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["error"]["class"] == "FlowAppError"

    async def test_generation_boundary_captures_and_attaches_ref(self, tmp_path: Path) -> None:
        """S10 wiring: a FlowAppError crossing generate_image stages a bundle
        while the page is alive and rides the exception as incident_ref."""
        client = _client(tmp_path)

        class _FakePage:
            async def evaluate(self, script: str, /) -> dict[str, object]:
                return {"ligatures": ["crop_landscape"]}

            async def screenshot(self, *, path: str, full_page: bool = False) -> None:
                Path(path).write_bytes(b"\x89PNG\r\n\x1a\nfake")

        client._page = _FakePage()  # type: ignore[assignment]  # noqa: SLF001

        async def _boom() -> object:
            raise FlowAppError("Flow web app crashed")

        client.create_project = _boom  # type: ignore[method-assign]
        from gflow_cli.api.image import GenerateImageRequest

        with pytest.raises(FlowAppError) as excinfo:
            await client.generate_image(req=GenerateImageRequest(prompt="x"))
        assert excinfo.value.incident_ref is not None
        assert excinfo.value.incident_ref.path is not None
        assert (excinfo.value.incident_ref.path / "ui.json").exists()

    async def test_transport_setup_carries_recorder(self, tmp_path: Path) -> None:
        client = _client(tmp_path)
        setup = client._build_transport_setup()  # noqa: SLF001
        assert setup.recorder is client._recorder  # noqa: SLF001
