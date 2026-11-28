"""The refusal retry loop (Task 13): one direct attempt, one retry from a fresh browser.

Rows cite docs/superpowers/plans/2026-11-27-headless-direct-transport/SCENARIO.md.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from gflow_cli.api import flow_wire
from gflow_cli.api._flow_retry import REFUSAL_RETRY_NOTICE, run_with_retry
from gflow_cli.api.client import FlowApiClient, RelaunchFailed
from gflow_cli.api.dto import GeneratedImage, GenerationCheckpoint
from gflow_cli.api.image import GenerateImageRequest, Model
from gflow_cli.api.submit_policy import (
    BrowserVisibility,
    DirectAttemptEnded,
    Indeterminate,
    NotSent,
    RefusedNotBilled,
    VisibilitySetting,
)
from gflow_cli.api.transports.batchexecute import UNUSUAL_ACTIVITY_REASON
from gflow_cli.api.transports.direct_http import DirectHttpTransport
from gflow_cli.api.transports.direct_http._gateway import InFlightGate
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.errors import (
    AuthExpiredError,
    NetworkError,
    RateLimitError,
    TaskCancelledError,
    UiModeUnavailableError,
    WafRejectionError,
    exit_code_for,
)
from tests.api.transports.test_direct_http import _UPLOADED, _maseq_reply, _png
from tests.api.transports.test_direct_http_gateway import _SHELL_HTML, _error_reply, _ogiz0b_reply

_REFUSED = RefusedNotBilled("ogiZ0b", UNUSUAL_ACTIVITY_REASON)


class FakeHost:
    """A ``RetryHost``: records each relaunch and the visibility it asked for."""

    def __init__(
        self,
        visibility: BrowserVisibility = BrowserVisibility.HEADLESS,
        *,
        fail: BaseException | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.visibility = visibility
        self.relaunches: list[BrowserVisibility] = []
        self.events = events if events is not None else []
        self._fail = fail

    async def relaunch(self, visibility: BrowserVisibility) -> None:
        self.events.append(f"relaunch:{visibility.value}")
        if self._fail is not None:
            raise self._fail
        self.relaunches.append(visibility)
        self.visibility = visibility


class Script:
    """A direct attempt that answers from a script, one entry per send."""

    def __init__(self, *answers: Any, events: list[str] | None = None) -> None:
        self.answers = list(answers)
        self.sends = 0
        self.events = events if events is not None else []

    async def direct(self, before: Any) -> str:
        answer = self.answers.pop(0)
        if isinstance(answer, NotSent):
            raise DirectAttemptEnded(answer)
        if before is not None:
            before()
        self.sends += 1
        self.events.append("send")
        if isinstance(answer, RefusedNotBilled):
            raise DirectAttemptEnded(answer)
        if isinstance(answer, Indeterminate):
            # As the gateway does: an outcome past the send carries its reconcile error.
            raise DirectAttemptEnded(answer, error=_reconcile_error(answer))
        return answer


def _reconcile_error(outcome: Indeterminate) -> NetworkError:
    return NetworkError(
        detail=f"{outcome.cause} on an image submit; not resent",
        status=outcome.status,
        route=f"batchexecute:{outcome.rpcid}",
        next_action="reconcile",
    )


def _observer(events: list[str]) -> Any:
    def observe(cp: GenerationCheckpoint) -> None:
        events.append(cp.phase)

    return observe


async def _no_ui() -> str:
    raise AssertionError("the UI path must not run")


@pytest.mark.asyncio
async def test_refusal_retries_once_from_new_headless_process() -> None:
    """Row 1: exactly two sends, the second after a relaunch at HEADLESS."""
    host = FakeHost(BrowserVisibility.HEADLESS)
    script = Script(_REFUSED, "image")

    result = await run_with_retry(
        host, direct=script.direct, ui=_no_ui, setting=VisibilitySetting.AUTO
    )

    assert result == "image"
    assert script.sends == 2
    assert host.relaunches == [BrowserVisibility.HEADLESS]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        (VisibilitySetting.AUTO, BrowserVisibility.HEADLESS),
        (VisibilitySetting.HEADLESS, BrowserVisibility.HEADLESS),
        (VisibilitySetting.HEADED, BrowserVisibility.HEADED),
    ],
)
async def test_refusal_retry_headed_only_under_explicit_headed(
    setting: VisibilitySetting, expected: BrowserVisibility
) -> None:
    """Rows 1, 22: the retry runs at the launch visibility the setting asks for."""
    host = FakeHost(BrowserVisibility.HEADED)  # a page launched headed for a mixed run
    script = Script(_REFUSED, "image")

    await run_with_retry(host, direct=script.direct, ui=_no_ui, setting=setting)

    assert host.relaunches == [expected]


@pytest.mark.asyncio
async def test_second_refusal_raises_final_waf_no_third_send() -> None:
    """Row 2: a second refusal is final, not charged, and nothing is sent a third time."""
    events: list[str] = []
    host = FakeHost(events=events)
    script = Script(_REFUSED, _REFUSED, "never", events=events)

    with pytest.raises(WafRejectionError) as caught:
        await run_with_retry(
            host,
            direct=script.direct,
            ui=_no_ui,
            setting=VisibilitySetting.AUTO,
            on_checkpoint=_observer(events),
        )

    assert script.sends == 2
    assert len(host.relaunches) == 1
    err = caught.value
    assert (err.charged, err.retryable, err.resolved_next_action()) == ("no", False, "none")
    assert events[-1] == "submit_refused"  # the job is reset to claimed: not charged


@pytest.mark.asyncio
async def test_indeterminate_raises_reconcile_and_sends_once() -> None:
    """Row 3: past the send nothing is resent; the error says reconcile, maybe charged."""
    host = FakeHost()
    script = Script(Indeterminate("ogiZ0b", "timeout after send"), "never")

    with pytest.raises(NetworkError) as caught:
        await run_with_retry(host, direct=script.direct, ui=_no_ui, setting=VisibilitySetting.AUTO)

    assert script.sends == 1
    assert host.relaunches == []
    err = caught.value
    assert (err.charged, err.retryable, err.resolved_next_action()) == ("maybe", False, "reconcile")


@pytest.mark.asyncio
async def test_indeterminate_keeps_the_attempts_typed_error() -> None:
    """A typed error past the send keeps its type; it is never made retryable."""
    typed = NetworkError(detail="HTTP 500 on an image submit; not resent", next_action="reconcile")
    typed.retryable = True

    async def direct(before: Any) -> str:
        raise DirectAttemptEnded(Indeterminate("ogiZ0b", "HTTP 500", 500), error=typed)

    with pytest.raises(NetworkError) as caught:
        await run_with_retry(FakeHost(), direct=direct, ui=_no_ui, setting=VisibilitySetting.AUTO)
    assert caught.value is typed
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_not_sent_falls_back_to_ui_after_headed_relaunch() -> None:
    """Row 7: nothing reached Flow, so the UI path runs, on a new headed browser."""
    events: list[str] = []
    host = FakeHost(BrowserVisibility.HEADLESS, events=events)
    script = Script(NotSent("unsupported_shape"), events=events)

    async def ui() -> str:
        events.append("ui")
        return "ui-image"

    result = await run_with_retry(
        host,
        direct=script.direct,
        ui=ui,
        setting=VisibilitySetting.AUTO,
        on_checkpoint=_observer(events),
    )

    assert result == "ui-image"
    assert events == ["relaunch:headed", "submit_attempted", "ui"]
    assert script.sends == 0


@pytest.mark.asyncio
async def test_not_sent_on_a_headed_page_runs_the_ui_path_without_a_relaunch() -> None:
    """A page already headed needs no new process for the UI path."""
    host = FakeHost(BrowserVisibility.HEADED)
    script = Script(NotSent("unsupported_shape"))

    async def ui() -> str:
        return "ui-image"

    assert (
        await run_with_retry(host, direct=script.direct, ui=ui, setting=VisibilitySetting.AUTO)
        == "ui-image"
    )
    assert host.relaunches == []


@pytest.mark.asyncio
async def test_ui_fallback_passes_submit_gate_before_ui_submit() -> None:
    """Row 12: the UI transport never receives ``on_before_submit``, so the loop writes the
    ``submit_attempted`` checkpoint (through the #1198 gate) before the UI submit."""
    events: list[str] = []
    script = Script(NotSent("navigation_failed:TimeoutError"), events=events)

    async def ui() -> str:
        events.append("ui")
        return "ui-image"

    await run_with_retry(
        FakeHost(BrowserVisibility.HEADED),
        direct=script.direct,
        ui=ui,
        setting=VisibilitySetting.AUTO,
        on_checkpoint=_observer(events),
    )
    assert events == ["submit_attempted", "ui"]


@pytest.mark.asyncio
async def test_on_before_submit_exception_propagates_unchanged() -> None:
    """Rows 12, 13: a cancel raised by the submit gate is never mapped to a fallback."""
    cancelled = TaskCancelledError(detail="Aborted by gflow before it was submitted.")
    ui = AsyncMock()

    def gate(cp: GenerationCheckpoint) -> None:
        raise cancelled

    with pytest.raises(TaskCancelledError) as caught:
        await run_with_retry(
            FakeHost(),
            direct=Script("never").direct,
            ui=ui,
            setting=VisibilitySetting.AUTO,
            on_checkpoint=gate,
        )
    assert caught.value is cancelled
    ui.assert_not_awaited()


@pytest.mark.asyncio
async def test_gate_cancel_before_the_ui_fallback_propagates_and_skips_the_ui() -> None:
    cancelled = TaskCancelledError(detail="Aborted by gflow before it was submitted.")
    ui = AsyncMock()

    def gate(cp: GenerationCheckpoint) -> None:
        raise cancelled

    with pytest.raises(TaskCancelledError):
        await run_with_retry(
            FakeHost(BrowserVisibility.HEADED),
            direct=Script(NotSent("unsupported_shape")).direct,
            ui=ui,
            setting=VisibilitySetting.AUTO,
            on_checkpoint=gate,
        )
    ui.assert_not_awaited()


@pytest.mark.asyncio
async def test_relaunch_failure_raises_final_refusal_not_charged() -> None:
    """Row 8: a relaunch that fails ends in the final refusal; nothing is sent again."""
    host = FakeHost(fail=RelaunchFailed("Browser relaunch failed: boom"))
    script = Script(_REFUSED, "never")

    with pytest.raises(WafRejectionError) as caught:
        await run_with_retry(host, direct=script.direct, ui=_no_ui, setting=VisibilitySetting.AUTO)

    assert script.sends == 1
    assert (caught.value.charged, caught.value.retryable) == ("no", False)
    assert isinstance(caught.value.__cause__, RelaunchFailed)


@pytest.mark.asyncio
async def test_a_failed_relaunch_before_the_ui_fallback_is_final_and_not_charged() -> None:
    """Nothing reached Flow and no refusal happened: the relaunch failure is reported as
    itself (not charged; this client is unusable, a new run is the remedy), not as a
    refusal."""
    host = FakeHost(BrowserVisibility.HEADLESS, fail=RelaunchFailed("boom"))
    with pytest.raises(RelaunchFailed) as caught:
        await run_with_retry(
            host,
            direct=Script(NotSent("unsupported_shape")).direct,
            ui=_no_ui,
            setting=VisibilitySetting.AUTO,
        )
    assert caught.value.charged == "no"


@pytest.mark.asyncio
async def test_signed_out_profile_matches_ui_auth_error() -> None:
    """Row 19: a signed-out session raises the UI path's class, with no relaunch or retry."""
    host = FakeHost()
    script = Script(NotSent("signed_out_shell", auth=True))

    with pytest.raises(AuthExpiredError):
        await run_with_retry(host, direct=script.direct, ui=_no_ui, setting=VisibilitySetting.AUTO)
    assert host.relaunches == []
    assert script.sends == 0


@pytest.mark.asyncio
async def test_run_with_retry_not_sent_auth_raises_auth_expired_no_ui_no_relaunch() -> None:
    """The transport's ``NotSent(auth=True)`` (a 401 shell) stops for reauth: no relaunch,
    no UI run, no send, and no ``submit_attempted`` checkpoint."""
    events: list[str] = []
    host = FakeHost(BrowserVisibility.HEADLESS, events=events)
    ui = AsyncMock()

    with pytest.raises(AuthExpiredError) as caught:
        await run_with_retry(
            host,
            direct=Script(NotSent("http_401", auth=True), events=events).direct,
            ui=ui,
            setting=VisibilitySetting.AUTO,
            on_checkpoint=_observer(events),
        )

    assert "http_401" in caught.value.detail
    ui.assert_not_awaited()
    assert events == []


@pytest.mark.asyncio
async def test_explicit_headed_without_display_fails_before_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Row 20: a headed launch on Linux with no display fails before Playwright runs, naming
    the display; a headed retry propagates the same error unchanged."""
    monkeypatch.setattr("gflow_cli.api.client._on_linux", lambda: True)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    launched = MagicMock(side_effect=AssertionError("Playwright must not start"))
    monkeypatch.setattr("gflow_cli.api.client.async_playwright", launched)

    with pytest.raises(UiModeUnavailableError) as caught:
        async with FlowApiClient(tmp_path, visibility=BrowserVisibility.HEADED):
            pass
    assert "display" in caught.value.detail.lower()
    assert exit_code_for(caught.value) == 28

    no_display = UiModeUnavailableError(BrowserVisibility.HEADED)
    with pytest.raises(UiModeUnavailableError) as retried:
        await run_with_retry(
            FakeHost(BrowserVisibility.HEADED, fail=no_display),
            direct=Script(_REFUSED, "never").direct,
            ui=_no_ui,
            setting=VisibilitySetting.HEADED,
        )
    assert retried.value is no_display


@pytest.mark.asyncio
async def test_checkpoint_written_just_before_post_and_reset_on_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 12: ``submit_attempted`` is written after the mint and immediately before the
    billed POST; a refusal writes ``submit_refused`` before the relaunch."""
    events: list[str] = []
    replies = [_error_reply(UNUSUAL_ACTIVITY_REASON), _ogiz0b_reply()]

    async def mint(_self: Any, action: str) -> str:
        events.append("mint")
        return "mock_token"

    monkeypatch.setattr("gflow_cli.api.recaptcha.TokenMinter.mint", mint)
    monkeypatch.setattr(
        "gflow_cli.api.transports.direct_http._images.build_ogiz0b_body",
        MagicMock(return_value="f.req=mock_body&at=at-x&"),
    )
    monkeypatch.setattr(flow_wire, "getproxies", dict)
    flow_wire.reset_state()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=_SHELL_HTML)
        events.append("POST")
        return httpx.Response(200, text=replies.pop(0))

    transport = _direct_transport(tmp_path, handler)
    host = FakeHost(events=events)
    request = GenerateImageRequest(prompt="x")

    images = await run_with_retry(
        host,
        direct=lambda before: transport.generate_images(
            project_id="proj-123", request=request, on_before_submit=before
        ),
        ui=_no_ui,
        setting=VisibilitySetting.AUTO,
        on_checkpoint=_observer(events),
    )

    assert images[0].transport == "direct_http"
    assert events == [
        "mint",
        "submit_attempted",
        "POST",
        "submit_refused",
        "relaunch:headless",
        "mint",
        "submit_attempted",
        "POST",
    ]
    flow_wire.reset_state()


@pytest.mark.asyncio
async def test_route_mismatch_and_upload_fallback_raise_not_sent_no_in_place_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows 6, 7: the transport never runs the UI path on its own page; it reports
    ``NotSent`` and the loop decides where the request goes."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected {request.method} {request.url}")

    transport = _direct_transport(tmp_path, handler)
    ui_images = AsyncMock()
    transport._generate_images_locked = ui_images  # type: ignore[method-assign]
    ui_upload = AsyncMock()
    monkeypatch.setattr(UiAutomationTransport, "upload_reference", ui_upload)

    unmeasured = GenerateImageRequest(prompt="x", model=Model.IMAGEN_3_5)
    with pytest.raises(DirectAttemptEnded) as shape:
        await transport.generate_images(project_id="proj-123", request=unmeasured)
    assert shape.value.outcome == NotSent("unsupported_shape")

    with pytest.raises(DirectAttemptEnded) as no_project:
        await transport.generate_images(project_id=None, request=GenerateImageRequest(prompt="x"))
    assert no_project.value.outcome == NotSent("no_project")

    monkeypatch.setattr(
        "gflow_cli.api.transports.direct_http._transport._proxy_configured", lambda: True
    )
    with pytest.raises(DirectAttemptEnded) as upload:
        await transport.upload_reference(project_id="proj-123", path=tmp_path / "face.png")
    assert upload.value.outcome == NotSent("proxy_configured")

    ui_images.assert_not_awaited()
    ui_upload.assert_not_awaited()
    transport.park_deferred_page.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_the_ui_fallback_methods_run_the_inherited_ui_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = _direct_transport(tmp_path, lambda _r: httpx.Response(404))
    ui_images = AsyncMock(return_value=["ui-image"])
    transport._generate_images_locked = ui_images  # type: ignore[method-assign]
    ui_upload = AsyncMock(return_value="ref")
    monkeypatch.setattr(UiAutomationTransport, "upload_reference", ui_upload)

    request = GenerateImageRequest(prompt="x", model=Model.IMAGEN_3_5)
    assert await transport.generate_images_ui(project_id="p", request=request) == ["ui-image"]
    assert await transport.upload_reference_ui(project_id="p", path=tmp_path / "a.png") == "ref"
    ui_images.assert_awaited_once()
    ui_upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_about_blank_park_after_direct_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The armed page stays armed after a direct call through the loop; parking stays on the
    UI path."""
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    monkeypatch.setattr(
        "gflow_cli.api.transports.direct_http._images.build_ogiz0b_body",
        MagicMock(return_value="f.req=mock_body&at=at-x&"),
    )
    monkeypatch.setattr(flow_wire, "getproxies", dict)
    flow_wire.reset_state()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=_SHELL_HTML)
        return httpx.Response(200, text=_ogiz0b_reply())

    transport = _direct_transport(tmp_path, handler)
    park = AsyncMock()
    transport._park_composer_page = park  # type: ignore[method-assign]
    request = GenerateImageRequest(prompt="x")

    await run_with_retry(
        FakeHost(),
        direct=lambda before: transport.generate_images(
            project_id="proj-123", request=request, on_before_submit=before
        ),
        ui=_no_ui,
        setting=VisibilitySetting.AUTO,
    )
    park.assert_not_awaited()
    transport.park_deferred_page.assert_not_awaited()  # type: ignore[attr-defined]
    flow_wire.reset_state()


@pytest.mark.asyncio
async def test_two_in_flight_one_refused_accepted_one_completes() -> None:
    """Rows 8, 14: the refused job's relaunch waits until ``quiesce()`` has let the accepted
    job finish its poll and download; the accepted job is not retried."""
    gate = InFlightGate()
    events: list[str] = []
    accepted_polling = asyncio.Event()
    finish_accepted = asyncio.Event()

    class QuiescingHost(FakeHost):
        async def relaunch(self, visibility: BrowserVisibility) -> None:
            finish_accepted.set()
            async with gate.quiesce():
                events.append("relaunch")
                self.relaunches.append(visibility)

    host = QuiescingHost()
    sends = {"a": 0, "b": 0}

    async def accepted(before: Any) -> str:
        async with gate.slot():
            sends["a"] += 1
            accepted_polling.set()
            await finish_accepted.wait()
            await asyncio.sleep(0)
            events.append("accepted_done")
            return "a"

    async def refused_once(before: Any) -> str:
        await accepted_polling.wait()
        async with gate.slot():
            sends["b"] += 1
            if sends["b"] == 1:
                raise DirectAttemptEnded(_REFUSED)
            events.append("retry_done")
            return "b"

    results = await asyncio.gather(
        run_with_retry(host, direct=accepted, ui=_no_ui, setting=VisibilitySetting.AUTO),
        run_with_retry(host, direct=refused_once, ui=_no_ui, setting=VisibilitySetting.AUTO),
    )

    assert results == ["a", "b"]
    assert sends == {"a": 1, "b": 2}
    assert host.relaunches == [BrowserVisibility.HEADLESS]
    assert events == ["accepted_done", "relaunch", "retry_done"]


@pytest.mark.asyncio
async def test_the_refusal_notice_is_logged_once_before_the_relaunch(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Human mode: one stderr line before the relaunch; stdout stays clean for ``--json``."""
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        await run_with_retry(
            FakeHost(),
            direct=Script(_REFUSED, "image").direct,
            ui=_no_ui,
            setting=VisibilitySetting.AUTO,
        )
    notices = [e for e in logs if e.get("detail") == REFUSAL_RETRY_NOTICE]
    assert len(notices) == 1
    assert notices[0]["log_level"] == "warning"
    assert capsys.readouterr().out == ""


@pytest.mark.asyncio
async def test_the_image_surface_runs_a_direct_transport_through_the_loop(
    tmp_path: Path,
) -> None:
    """``GenerationSurface`` sends a direct-capable transport through the loop: a refusal
    relaunches the client and the retry runs on the transport the relaunch set up."""
    client = FlowApiClient(tmp_path, visibility=BrowserVisibility.HEADLESS)
    events: list[str] = []

    class FakeDirect:
        def __init__(self, answer: Any) -> None:
            self.answer = answer

        def uses_page_owned_image_recaptcha(self) -> bool:
            return True

        async def generate_images(self, **kwargs: Any) -> list[GeneratedImage]:
            kwargs["on_before_submit"]()
            events.append("send")
            if isinstance(self.answer, RefusedNotBilled):
                raise DirectAttemptEnded(self.answer)
            return [self.answer]

        async def generate_images_ui(self, **kwargs: Any) -> list[GeneratedImage]:
            raise AssertionError("the UI path must not run")

    image = MagicMock()
    image.transport = "direct_http"
    client.transport = FakeDirect(_REFUSED)  # type: ignore[assignment]

    async def relaunch(visibility: BrowserVisibility) -> None:
        events.append(f"relaunch:{visibility.value}")
        client.transport = FakeDirect(image)  # type: ignore[assignment]

    client.relaunch = relaunch  # type: ignore[method-assign]

    images = await client._drive_images_generation(
        project_id="proj-123",
        req=GenerateImageRequest(prompt="x"),
        recaptcha_action="imageGeneration",
        on_checkpoint=_observer(events),
    )

    assert images == [image]
    assert events == [
        "submit_attempted",
        "send",
        "submit_refused",
        "relaunch:headless",
        "submit_attempted",
        "send",
        "remote_started",
    ]


def _fake_page() -> MagicMock:
    page = MagicMock()
    page.url = "https://flow.google.com/project/proj-123"
    page.goto = AsyncMock()
    page.close = AsyncMock()
    page.context = MagicMock()
    page.context.cookies = AsyncMock(
        return_value=[{"name": "SID", "value": "sid-1", "domain": ".google.com"}]
    )
    # A refusal discards the minting page; the next mint opens a fresh one.
    page.context.new_page = AsyncMock(side_effect=lambda: _fake_page())
    return page


def _direct_transport(tmp_path: Path, handler: Any) -> DirectHttpTransport:
    t = DirectHttpTransport(http_transport=httpx.MockTransport(handler))
    t._setup_done = True
    t._profile_dir = tmp_path
    t._page = _fake_page()
    t.park_deferred_page = AsyncMock()  # type: ignore[method-assign]
    return t


@pytest.mark.asyncio
async def test_reauth_error_charged_no() -> None:
    """The reauth stop happens before any send, so its error says nothing was charged."""
    with pytest.raises(AuthExpiredError) as caught:
        await run_with_retry(
            FakeHost(),
            direct=Script(NotSent("signed_out_shell", auth=True)).direct,
            ui=_no_ui,
            setting=VisibilitySetting.AUTO,
        )
    assert caught.value.charged == "no"
    assert caught.value.resolved_next_action() == "reauth"


@pytest.mark.asyncio
async def test_post_send_error_reports_reconcile() -> None:
    """A typed error that says ``charged="no"`` but ended past the send may have billed: the
    loop raises it as ``maybe`` so ``next_action`` reads reconcile."""
    typed = RateLimitError(detail="HTTP 429 after the send")
    assert typed.charged == "no"

    async def direct(before: Any) -> str:
        raise DirectAttemptEnded(Indeterminate("ogiZ0b", "HTTP 429", 429), error=typed)

    with pytest.raises(RateLimitError) as caught:
        await run_with_retry(FakeHost(), direct=direct, ui=_no_ui, setting=VisibilitySetting.AUTO)
    assert caught.value is typed
    assert (caught.value.charged, caught.value.retryable) == ("maybe", False)
    assert caught.value.resolved_next_action() == "reconcile"


#: Any reason Flow names other than UNUSUAL_ACTIVITY (and not a content-safety reason).
_OTHER_REASON = "PUBLIC_ERROR_OUT_OF_CREDITS"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [httpx.Response(429), httpx.Response(200, text=_error_reply(_OTHER_REASON))],
    ids=["http_429", "other_reason"],
)
async def test_429_and_out_of_credits_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: httpx.Response
) -> None:
    """Only an UNUSUAL_ACTIVITY refusal is resent: a 429, or any other reason, is one POST,
    no relaunch, and a reconcile error."""
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    monkeypatch.setattr(
        "gflow_cli.api.transports.direct_http._images.build_ogiz0b_body",
        MagicMock(return_value="f.req=mock_body&at=at-x&"),
    )
    monkeypatch.setattr(flow_wire, "getproxies", dict)
    flow_wire.reset_state()
    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=_SHELL_HTML)
        posts.append("POST")
        return reply

    transport = _direct_transport(tmp_path, handler)
    host = FakeHost()
    request = GenerateImageRequest(prompt="x")

    with pytest.raises(NetworkError) as caught:
        await run_with_retry(
            host,
            direct=lambda before: transport.generate_images(
                project_id="proj-123", request=request, on_before_submit=before
            ),
            ui=_no_ui,
            setting=VisibilitySetting.AUTO,
        )
    assert posts == ["POST"]
    assert host.relaunches == []
    assert (caught.value.charged, caught.value.resolved_next_action()) == ("maybe", "reconcile")
    flow_wire.reset_state()


def _maseq_error(reason: str) -> str:
    frame = [["wrb.fr", "maseQ", None, None, None, [3, None, [[reason]]], "generic"]]
    return f")]}}'\n{json.dumps(frame)}"


def _upload_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replies: list[httpx.Response]
) -> tuple[FlowApiClient, list[str]]:
    """A client whose transport uploads direct; ``relaunch`` is recorded, the UI is fenced."""
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    monkeypatch.setattr(flow_wire, "getproxies", dict)
    monkeypatch.setattr(
        UiAutomationTransport, "upload_reference", AsyncMock(side_effect=AssertionError("UI"))
    )
    flow_wire.reset_state()
    events: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=_SHELL_HTML)
        events.append(request.url.params["rpcids"])
        return replies.pop(0)

    client = FlowApiClient(tmp_path, visibility=BrowserVisibility.HEADLESS)
    client.transport = _direct_transport(tmp_path, handler)

    async def relaunch(visibility: BrowserVisibility) -> None:
        events.append(f"relaunch:{visibility.value}")

    client.relaunch = relaunch  # type: ignore[method-assign]
    return client, events


@pytest.mark.asyncio
async def test_upload_refused_reuploads_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused ``maseQ`` is not billed: one relaunch, one more upload, and its media id."""
    client, events = _upload_client(
        tmp_path,
        monkeypatch,
        [
            httpx.Response(200, text=_maseq_error(UNUSUAL_ACTIVITY_REASON)),
            httpx.Response(200, text=_maseq_reply()),
        ],
    )

    ref = await client.upload_reference_in_place("proj-123", _png(tmp_path))

    assert ref is not None
    assert ref.name == _UPLOADED
    assert events == ["maseQ", "relaunch:headless", "maseQ"]
    flow_wire.reset_state()


@pytest.mark.asyncio
async def test_upload_indeterminate_not_resent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An upload that may have landed is never sent again, nor retried through the UI."""
    client, events = _upload_client(tmp_path, monkeypatch, [httpx.Response(500)])

    with pytest.raises(NetworkError) as caught:
        await client.upload_reference_in_place("proj-123", _png(tmp_path))

    assert events == ["maseQ"]
    assert (caught.value.charged, caught.value.resolved_next_action()) == ("maybe", "reconcile")
    flow_wire.reset_state()


@pytest.mark.asyncio
async def test_fallback_relaunch_failure_exit_code_and_not_charged() -> None:
    """A relaunch that fails before the UI fallback is reported as itself: nothing was sent,
    a stable exit code (the browser-session-closed family, 15), and the earlier refusal named
    in its detail."""

    class FailsSecond(FakeHost):
        async def relaunch(self, visibility: BrowserVisibility) -> None:
            if self.relaunches:
                raise RelaunchFailed("Browser relaunch failed: boom")
            await super().relaunch(visibility)

    host = FailsSecond(BrowserVisibility.HEADLESS)
    script = Script(_REFUSED, NotSent("navigation_failed:TimeoutError"))

    with pytest.raises(RelaunchFailed) as caught:
        await run_with_retry(host, direct=script.direct, ui=_no_ui, setting=VisibilitySetting.AUTO)

    err = caught.value
    assert exit_code_for(err) == 15
    assert (err.charged, err.resolved_next_action()) == ("no", "retry")
    assert "boom" in err.detail
    assert UNUSUAL_ACTIVITY_REASON in err.detail
    assert host.relaunches == [BrowserVisibility.HEADLESS]
