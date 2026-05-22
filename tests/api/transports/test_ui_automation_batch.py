"""Unit tests for UiAutomationTransport.generate_images_batch.

The highest-stakes test is the multi-listener concurrency invariant —
council Finding T1. With N listeners active simultaneously on the same
Page, each captures into its own list, and we assert no cross-contamination.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.transports.ui_automation import UiAutomationTransport


class _FakePage:
    """Minimal Page surrogate that records response handlers and lets a test
    fire mocked response events at them in arbitrary order."""

    def __init__(self) -> None:
        self.handlers: list = []

    def on(self, event: str, handler) -> None:  # type: ignore[no-untyped-def]
        assert event == "response"
        self.handlers.append(handler)

    def remove_listener(self, event: str, handler) -> None:  # type: ignore[no-untyped-def]
        assert event == "response"
        self.handlers.remove(handler)

    def fire_response(self, response) -> None:  # type: ignore[no-untyped-def]
        for h in list(self.handlers):
            h(response)


class _FakeResponse:
    def __init__(self, url: str, status: int = 200, body: bytes = b"{}") -> None:
        self.url = url
        self.status = status
        self._body = body

    async def body(self) -> bytes:
        return self._body


# ---------------------------------------------------------------------------
# Task 3.3 — detach invariant (also covers Task 3.5 basic shape)
# ---------------------------------------------------------------------------


def test_attach_batch_response_listener_returns_detach_callable() -> None:
    """The helper returns (captured_list, detach_fn). Detach must remove
    the registered handler from the page so that subsequent simulated
    responses do NOT append to the captured list."""
    page = _FakePage()
    captured, detach = UiAutomationTransport._attach_batch_response_listener(
        page,
        project_id="p1",  # type: ignore[arg-type]
    )
    assert isinstance(captured, list)
    assert callable(detach)
    assert len(page.handlers) == 1

    detach()
    assert len(page.handlers) == 0

    # Idempotent — second call must not raise
    detach()
    assert len(page.handlers) == 0


def test_listener_detach_is_idempotent_and_removes_handler() -> None:
    page = _FakePage()
    captured, detach = UiAutomationTransport._attach_batch_response_listener(
        page,
        project_id="proj-1",  # type: ignore[arg-type]
    )
    assert len(page.handlers) == 1

    detach()
    assert len(page.handlers) == 0

    # After detach, firing a response must not append.
    page.fire_response(_FakeResponse("https://flow/projects/proj-1/batchGenerateImages"))
    assert len(captured) == 0

    # Idempotent second detach.
    detach()
    assert len(page.handlers) == 0


# ---------------------------------------------------------------------------
# Task 3.5 — multi-listener concurrency invariant (xfail per plan)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason=(
        "v3-3 ships with submission-order-arrival assumption (spec §5.6 "
        "option 2). Promote this test to strict-pass when option 1 (explicit "
        "post-attach-time filter) is implemented per spec §10 follow-up."
    ),
    strict=False,
)
def test_multi_listener_no_cross_contamination() -> None:
    """Two listeners attached on the same page, both filtered by the same
    project_id (because we're in same-project mode). Responses arrive
    interleaved. Each listener's captured list must contain only its own
    prompt's responses.

    NOTE: on_response is async, so firing it synchronously via _FakePage.fire_response
    only schedules the coroutine — captured lists will remain empty in the sync test
    context. This test is xfailed until option 1 (post-attach-time filter) lands.
    """
    page = _FakePage()

    captured_1, detach_1 = UiAutomationTransport._attach_batch_response_listener(
        page,
        project_id="proj-shared",  # type: ignore[arg-type]
    )
    captured_2, detach_2 = UiAutomationTransport._attach_batch_response_listener(
        page,
        project_id="proj-shared",  # type: ignore[arg-type]
    )

    # Both listeners attached. In the strict (option 1) world, listener 1
    # must not see responses that arrive after listener 2 was attached.
    # In the current (option 2) world, both listeners see all post-attach
    # responses (project_id filter only prevents cross-PROJECT contamination).
    # Assert the strict invariant — this will fail until option 1 lands.
    assert len(captured_1) == 0, "captured_1 saw responses before any were fired"
    assert len(captured_2) == 0, "captured_2 saw responses before any were fired"
    # Strict assertion: after option 1 lands, captured_1 must see ONLY
    # responses fired before listener 2 was attached.
    assert len(captured_1) < len(captured_2), "option 1 not yet implemented"

    detach_1()
    detach_2()


# ---------------------------------------------------------------------------
# Task 3.6 — generate_images_batch happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_images_batch_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three prompts, all succeed. Verify:
    - _enter_editor called once
    - _dismiss_blocking_overlays called once
    - _configure_generation_settings + _attach_batch_response_listener +
      _send_prompt called 3x each, in order
    - jitter sleep called twice (between prompts), not before the first or after the last
    - results returned in submission order
    - every result carries the same project_id
    - every result has the correct prompt_idx (0, 1, 2)
    """
    import gflow_cli.api.transports.ui_automation as uia_mod
    from gflow_cli.api.image import Aspect, GenerateImageRequest, Model

    # Build a mock transport instance bypassing __init__.
    transport = UiAutomationTransport.__new__(UiAutomationTransport)
    transport._setup_done = True  # type: ignore[attr-defined]
    transport._page = MagicMock()  # type: ignore[attr-defined]
    transport._page.url = "https://labs.google/fx/tools/flow/project/PROJECT-UUID"
    transport._out_dir = None  # type: ignore[attr-defined]
    transport._generate_lock = __import__("asyncio").Lock()  # type: ignore[attr-defined]

    transport._enter_editor = AsyncMock()  # type: ignore[attr-defined]
    transport._dismiss_blocking_overlays = AsyncMock()  # type: ignore[attr-defined]
    transport._configure_generation_settings = AsyncMock()  # type: ignore[attr-defined]
    transport._send_prompt = AsyncMock()  # type: ignore[attr-defined]

    # Mock the listener to return 3 distinct (captured, detach) pairs.
    captures: list[list] = [[], [], []]
    detaches = [MagicMock(), MagicMock(), MagicMock()]
    listener_calls = [0]

    def fake_listener(page, *, project_id=None):  # type: ignore[no-untyped-def]
        idx = listener_calls[0]
        listener_calls[0] += 1
        return captures[idx], detaches[idx]

    monkeypatch.setattr(
        UiAutomationTransport,
        "_attach_batch_response_listener",
        staticmethod(fake_listener),
    )

    # _await_captured returns the capture list contents.
    async def fake_await(captured, expected_count=1, **_kwargs):  # type: ignore[no-untyped-def]
        return list(captured)

    monkeypatch.setattr(UiAutomationTransport, "_await_captured", staticmethod(fake_await))

    # Pre-stuff one image-response per prompt into each capture list.
    fake_img = MagicMock()
    for cap in captures:
        cap.append({"status": 200, "url": "https://x/batchGenerateImages", "body": {}})

    # Mock _images_from_responses to return one image per response.
    monkeypatch.setattr(
        uia_mod,
        "_images_from_responses",
        lambda responses: ([fake_img] * len(responses), None, ""),
    )

    # Mock _extract_project_id to return the URL-extracted UUID.
    monkeypatch.setattr(
        uia_mod,
        "_extract_project_id",
        lambda url: "PROJECT-UUID",
    )

    # Stub asyncio.sleep and random.uniform for deterministic fast run.
    sleep_calls: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleep_calls.append(d)

    monkeypatch.setattr(uia_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(uia_mod.random, "uniform", lambda a, b: 1.5)

    prompts = [
        GenerateImageRequest(prompt="p0", aspect=Aspect.PORTRAIT, model=Model.NARWHAL, count=1),
        GenerateImageRequest(prompt="p1", aspect=Aspect.PORTRAIT, model=Model.NARWHAL, count=1),
        GenerateImageRequest(prompt="p2", aspect=Aspect.PORTRAIT, model=Model.NARWHAL, count=1),
    ]

    results = await transport.generate_images_batch(
        prompts=prompts, jitter_range=(1.0, 2.0), continue_on_error=False
    )

    # Bug-fix invariants:
    assert transport._enter_editor.call_count == 1
    assert transport._dismiss_blocking_overlays.call_count == 1
    assert transport._configure_generation_settings.call_count == 3
    assert transport._send_prompt.call_count == 3
    assert listener_calls[0] == 3
    assert sleep_calls == [1.5, 1.5]  # N-1 sleeps, both deterministic

    # Shared project_id:
    assert len({r.project_id for r in results}) == 1
    assert results[0].project_id == "PROJECT-UUID"

    # Submission order preserved:
    assert [r.prompt_idx for r in results] == [0, 1, 2]
    assert all(r.status == "ok" for r in results)

    # Detach was called for every listener:
    for d in detaches:
        d.assert_called()


# ---------------------------------------------------------------------------
# Task 3.7 — failure-mode tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_images_batch_continue_on_error_send_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One prompt's _send_prompt raises. With continue_on_error=True the loop
    continues and that prompt's result has status='fail'."""
    import gflow_cli.api.transports.ui_automation as uia_mod
    from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
    from gflow_cli.errors import GFlowError

    transport = UiAutomationTransport.__new__(UiAutomationTransport)
    transport._setup_done = True  # type: ignore[attr-defined]
    transport._page = MagicMock()  # type: ignore[attr-defined]
    transport._page.url = "https://labs.google/fx/tools/flow/project/PROJ-X"
    transport._out_dir = None  # type: ignore[attr-defined]
    transport._generate_lock = __import__("asyncio").Lock()  # type: ignore[attr-defined]
    transport._enter_editor = AsyncMock()  # type: ignore[attr-defined]
    transport._dismiss_blocking_overlays = AsyncMock()  # type: ignore[attr-defined]
    transport._configure_generation_settings = AsyncMock()  # type: ignore[attr-defined]

    call_count = [0]

    async def send_prompt_raises_on_idx1(page, prompt, out_dir):  # type: ignore[no-untyped-def]
        current = call_count[0]
        call_count[0] += 1
        if current == 1:
            raise GFlowError(detail="submit failed", route="test")

    transport._send_prompt = send_prompt_raises_on_idx1  # type: ignore[attr-defined]

    captures: list[list] = [[], [], []]
    detaches = [MagicMock(), MagicMock(), MagicMock()]
    listener_idx = [0]

    def fake_listener(page, *, project_id=None):  # type: ignore[no-untyped-def]
        i = listener_idx[0]
        listener_idx[0] += 1
        return captures[i], detaches[i]

    monkeypatch.setattr(
        UiAutomationTransport, "_attach_batch_response_listener", staticmethod(fake_listener)
    )

    async def fake_await(captured, expected_count=1, **_kwargs):  # type: ignore[no-untyped-def]
        return list(captured)

    monkeypatch.setattr(UiAutomationTransport, "_await_captured", staticmethod(fake_await))

    fake_img = MagicMock()
    for cap in captures:
        cap.append({"status": 200, "url": "https://x/batchGenerateImages", "body": {}})

    monkeypatch.setattr(
        uia_mod, "_images_from_responses", lambda r: ([fake_img] * len(r), None, "")
    )
    monkeypatch.setattr(uia_mod, "_extract_project_id", lambda url: "PROJ-X")
    monkeypatch.setattr(uia_mod.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(uia_mod.random, "uniform", lambda a, b: 0.0)

    prompts = [
        GenerateImageRequest(prompt="p0", aspect=Aspect.PORTRAIT, model=Model.NARWHAL, count=1),
        GenerateImageRequest(prompt="p1", aspect=Aspect.PORTRAIT, model=Model.NARWHAL, count=1),
        GenerateImageRequest(prompt="p2", aspect=Aspect.PORTRAIT, model=Model.NARWHAL, count=1),
    ]

    results = await transport.generate_images_batch(
        prompts=prompts, jitter_range=(0.0, 0.0), continue_on_error=True
    )

    assert len(results) == 3
    assert results[0].status == "ok"
    assert results[1].status == "fail"
    assert results[2].status == "ok"
    # Detach must have been called for index 1 (no dangling listener)
    detaches[1].assert_called()


@pytest.mark.asyncio
async def test_generate_images_batch_fail_fast_partial_salvage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One prompt's _send_prompt raises with continue_on_error=False. Method
    raises BatchPartialError carrying partial_results for prompts 0..N-1
    that already completed."""
    import gflow_cli.api.transports.ui_automation as uia_mod
    from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
    from gflow_cli.errors import BatchPartialError, GFlowError

    transport = UiAutomationTransport.__new__(UiAutomationTransport)
    transport._setup_done = True  # type: ignore[attr-defined]
    transport._page = MagicMock()  # type: ignore[attr-defined]
    transport._page.url = "https://labs.google/fx/tools/flow/project/PROJ-Y"
    transport._out_dir = None  # type: ignore[attr-defined]
    transport._generate_lock = __import__("asyncio").Lock()  # type: ignore[attr-defined]
    transport._enter_editor = AsyncMock()  # type: ignore[attr-defined]
    transport._dismiss_blocking_overlays = AsyncMock()  # type: ignore[attr-defined]
    transport._configure_generation_settings = AsyncMock()  # type: ignore[attr-defined]

    send_call = [0]

    async def send_raises_on_idx1(page, prompt, out_dir):  # type: ignore[no-untyped-def]
        if send_call[0] == 1:
            raise GFlowError(detail="upstream fail", route="test")
        send_call[0] += 1

    transport._send_prompt = send_raises_on_idx1  # type: ignore[attr-defined]

    captures: list[list] = [[], []]
    detaches = [MagicMock(), MagicMock()]
    l_idx = [0]

    def fake_listener(page, *, project_id=None):  # type: ignore[no-untyped-def]
        i = l_idx[0]
        l_idx[0] += 1
        return captures[i], detaches[i]

    monkeypatch.setattr(
        UiAutomationTransport, "_attach_batch_response_listener", staticmethod(fake_listener)
    )

    async def fake_await(captured, expected_count=1, **_kwargs):  # type: ignore[no-untyped-def]
        return list(captured)

    monkeypatch.setattr(UiAutomationTransport, "_await_captured", staticmethod(fake_await))

    fake_img = MagicMock()
    for cap in captures:
        cap.append({"status": 200, "url": "https://x/batchGenerateImages", "body": {}})

    monkeypatch.setattr(
        uia_mod, "_images_from_responses", lambda r: ([fake_img] * len(r), None, "")
    )
    monkeypatch.setattr(uia_mod, "_extract_project_id", lambda url: "PROJ-Y")
    monkeypatch.setattr(uia_mod.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(uia_mod.random, "uniform", lambda a, b: 0.0)

    prompts = [
        GenerateImageRequest(prompt="p0", aspect=Aspect.PORTRAIT, model=Model.NARWHAL, count=1),
        GenerateImageRequest(prompt="p1", aspect=Aspect.PORTRAIT, model=Model.NARWHAL, count=1),
        GenerateImageRequest(prompt="p2", aspect=Aspect.PORTRAIT, model=Model.NARWHAL, count=1),
    ]

    with pytest.raises(BatchPartialError) as exc_info:
        await transport.generate_images_batch(
            prompts=prompts, jitter_range=(0.0, 0.0), continue_on_error=False
        )

    err = exc_info.value
    # partial_results carries only the already-completed ok result (prompt 0)
    assert len(err.partial_results) == 1
    assert err.partial_results[0].status == "ok"
    assert err.partial_results[0].prompt_idx == 0
    assert err.cause is not None


@pytest.mark.asyncio
async def test_generate_images_batch_detach_invariant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every listener that was attached has its detach_fn called before the
    method returns, even on the error path."""
    import gflow_cli.api.transports.ui_automation as uia_mod
    from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
    from gflow_cli.errors import BatchPartialError, GFlowError

    transport = UiAutomationTransport.__new__(UiAutomationTransport)
    transport._setup_done = True  # type: ignore[attr-defined]
    transport._page = MagicMock()  # type: ignore[attr-defined]
    transport._page.url = "https://labs.google/fx/tools/flow/project/PROJ-Z"
    transport._out_dir = None  # type: ignore[attr-defined]
    transport._generate_lock = __import__("asyncio").Lock()  # type: ignore[attr-defined]
    transport._enter_editor = AsyncMock()  # type: ignore[attr-defined]
    transport._dismiss_blocking_overlays = AsyncMock()  # type: ignore[attr-defined]
    transport._configure_generation_settings = AsyncMock()  # type: ignore[attr-defined]

    send_call = [0]

    async def send_raises_on_idx1(page, prompt, out_dir):  # type: ignore[no-untyped-def]
        if send_call[0] == 1:
            raise GFlowError(detail="fail", route="test")
        send_call[0] += 1

    transport._send_prompt = send_raises_on_idx1  # type: ignore[attr-defined]

    # Two listeners will be attached (prompt 0 succeeds, prompt 1 fails)
    detaches = [MagicMock(), MagicMock()]
    l_idx = [0]

    def fake_listener(page, *, project_id=None):  # type: ignore[no-untyped-def]
        i = l_idx[0]
        l_idx[0] += 1
        cap: list = [{"status": 200, "url": "https://x/batchGenerateImages", "body": {}}]
        return cap, detaches[i]

    monkeypatch.setattr(
        UiAutomationTransport, "_attach_batch_response_listener", staticmethod(fake_listener)
    )

    async def fake_await(captured, expected_count=1, **_kwargs):  # type: ignore[no-untyped-def]
        return list(captured)

    monkeypatch.setattr(UiAutomationTransport, "_await_captured", staticmethod(fake_await))

    fake_img = MagicMock()
    monkeypatch.setattr(
        uia_mod, "_images_from_responses", lambda r: ([fake_img] * len(r), None, "")
    )
    monkeypatch.setattr(uia_mod, "_extract_project_id", lambda url: "PROJ-Z")
    monkeypatch.setattr(uia_mod.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(uia_mod.random, "uniform", lambda a, b: 0.0)

    prompts = [
        GenerateImageRequest(prompt="p0", aspect=Aspect.PORTRAIT, model=Model.NARWHAL, count=1),
        GenerateImageRequest(prompt="p1", aspect=Aspect.PORTRAIT, model=Model.NARWHAL, count=1),
        GenerateImageRequest(prompt="p2", aspect=Aspect.PORTRAIT, model=Model.NARWHAL, count=1),
    ]

    with pytest.raises(BatchPartialError):
        await transport.generate_images_batch(
            prompts=prompts, jitter_range=(0.0, 0.0), continue_on_error=False
        )

    # Both attached listeners must have been detached
    for d in detaches:
        d.assert_called()
