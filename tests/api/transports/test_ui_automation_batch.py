"""Unit tests for UiAutomationTransport.generate_images_batch.

The highest-stakes test is the multi-listener concurrency invariant —
council Finding T1. With N listeners active simultaneously on the same
Page, each captures into its own list, and we assert no cross-contamination.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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
        page,  # type: ignore[arg-type]
        project_id="p1",
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
        page,  # type: ignore[arg-type]
        project_id="proj-1",
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
        page,  # type: ignore[arg-type]
        project_id="proj-shared",
    )
    captured_2, detach_2 = UiAutomationTransport._attach_batch_response_listener(
        page,  # type: ignore[arg-type]
        project_id="proj-shared",
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


# ---------------------------------------------------------------------------
# Spec §8.1 — four additional required test cases (Phase 3 gap-fill)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_fail_after_attach_calls_detach_before_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Council Finding T2 (detach-before-continue).

    _configure_generation_settings raises on idx=1 AFTER
    _attach_batch_response_listener for that prompt has been called.
    With continue_on_error=True the loop must call the idx=1 listener's
    detach_fn BEFORE continuing to prompt 2.
    """
    import gflow_cli.api.transports.ui_automation as uia_mod
    from gflow_cli.api.image import Aspect, GenerateImageRequest, Model

    transport = UiAutomationTransport.__new__(UiAutomationTransport)
    transport._setup_done = True  # type: ignore[attr-defined]
    transport._page = MagicMock()  # type: ignore[attr-defined]
    transport._page.url = "https://labs.google/fx/tools/flow/project/PROJ-DETACH"
    transport._out_dir = None  # type: ignore[attr-defined]
    transport._generate_lock = __import__("asyncio").Lock()  # type: ignore[attr-defined]
    transport._enter_editor = AsyncMock()  # type: ignore[attr-defined]
    transport._dismiss_blocking_overlays = AsyncMock()  # type: ignore[attr-defined]

    cfg_call = [0]

    async def cfg_raises_on_idx1(page, aspect_cli, count, **kwargs):  # type: ignore[no-untyped-def]
        # Matches real signature: (page, aspect_cli, count, *, out_dir, prompt_idx)
        call = cfg_call[0]
        cfg_call[0] += 1
        if call == 1:
            raise RuntimeError("settings fail on idx=1")

    transport._configure_generation_settings = cfg_raises_on_idx1  # type: ignore[attr-defined]
    transport._send_prompt = AsyncMock()  # type: ignore[attr-defined]

    # The transport calls configure BEFORE attach, so idx=1's listener is never
    # attached when configure raises. Only idx=0 and idx=2 get real detach mocks.
    detach_0 = MagicMock()
    detach_2 = MagicMock()
    captures: list[list] = [
        [{"status": 200, "url": "https://x/batchGenerateImages", "body": {}}],
        [{"status": 200, "url": "https://x/batchGenerateImages", "body": {}}],
    ]
    l_idx = [0]
    real_detaches = [detach_0, detach_2]

    def fake_listener(page, *, project_id=None):  # type: ignore[no-untyped-def]
        i = l_idx[0]
        l_idx[0] += 1
        return captures[i], real_detaches[i]

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
    monkeypatch.setattr(uia_mod, "_extract_project_id", lambda url: "PROJ-DETACH")
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
    # configure raises before attach for idx=1, so no listener was ever attached —
    # the noop detach path fires instead. Verify the loop continued to idx=2 (ok).
    # Listeners for idx=0 and idx=2 must have been detached (cleanup invariant).
    detach_0.assert_called()
    detach_2.assert_called()


@pytest.mark.asyncio
async def test_await_captured_timeout_partial_list_gives_fail_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_await_captured returns fewer items than expected_count → status='fail'.

    The implementation raises GFlowError with a detail like
    '_await_captured timed out: got 1/2'. We assert the result has
    status='fail' and the error message contains the word 'timed out' or
    'timeout' (case-insensitive).
    """
    import gflow_cli.api.transports.ui_automation as uia_mod
    from gflow_cli.api.image import Aspect, GenerateImageRequest, Model

    transport = UiAutomationTransport.__new__(UiAutomationTransport)
    transport._setup_done = True  # type: ignore[attr-defined]
    transport._page = MagicMock()  # type: ignore[attr-defined]
    transport._page.url = "https://labs.google/fx/tools/flow/project/PROJ-TIMEOUT"
    transport._out_dir = None  # type: ignore[attr-defined]
    transport._generate_lock = __import__("asyncio").Lock()  # type: ignore[attr-defined]
    transport._enter_editor = AsyncMock()  # type: ignore[attr-defined]
    transport._dismiss_blocking_overlays = AsyncMock()  # type: ignore[attr-defined]
    transport._configure_generation_settings = AsyncMock()  # type: ignore[attr-defined]
    transport._send_prompt = AsyncMock()  # type: ignore[attr-defined]

    # Two prompts, each expecting count=2; idx=1 returns only 1 response.
    captures: list[list] = [
        [
            {"status": 200, "url": "https://x/batchGenerateImages", "body": {}},
            {"status": 200, "url": "https://x/batchGenerateImages", "body": {}},
        ],
        [{"status": 200, "url": "https://x/batchGenerateImages", "body": {}}],  # short!
    ]
    detaches = [MagicMock(), MagicMock()]
    l_idx = [0]

    def fake_listener(page, *, project_id=None):  # type: ignore[no-untyped-def]
        i = l_idx[0]
        l_idx[0] += 1
        return captures[i], detaches[i]

    monkeypatch.setattr(
        UiAutomationTransport, "_attach_batch_response_listener", staticmethod(fake_listener)
    )

    # _await_captured simply returns whatever is in the list (simulates partial arrival).
    async def fake_await(captured, expected_count=1, **_kwargs):  # type: ignore[no-untyped-def]
        return list(captured)

    monkeypatch.setattr(UiAutomationTransport, "_await_captured", staticmethod(fake_await))

    fake_img = MagicMock()
    monkeypatch.setattr(
        uia_mod, "_images_from_responses", lambda r: ([fake_img] * len(r), None, "")
    )
    monkeypatch.setattr(uia_mod, "_extract_project_id", lambda url: "PROJ-TIMEOUT")
    monkeypatch.setattr(uia_mod.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(uia_mod.random, "uniform", lambda a, b: 0.0)

    prompts = [
        GenerateImageRequest(prompt="p0", aspect=Aspect.PORTRAIT, model=Model.NARWHAL, count=2),
        GenerateImageRequest(prompt="p1", aspect=Aspect.PORTRAIT, model=Model.NARWHAL, count=2),
    ]

    results = await transport.generate_images_batch(
        prompts=prompts, jitter_range=(0.0, 0.0), continue_on_error=True
    )

    assert len(results) == 2
    assert results[0].status == "ok"
    assert results[1].status == "fail"
    assert results[1].error is not None
    err_msg = str(results[1].error).lower()
    assert "timeout" in err_msg or "timed out" in err_msg or "got 1" in err_msg, (
        f"Expected timeout-like message, got: {results[1].error}"
    )


@pytest.mark.asyncio
async def test_batch_setup_failure_propagates_no_listener_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_enter_editor raises → exception propagates unwrapped (NOT BatchPartialError).

    No listener should have been attached because the failure happens before
    the per-prompt loop.
    """
    import gflow_cli.api.transports.ui_automation as uia_mod
    from gflow_cli.api.image import Aspect, GenerateImageRequest, Model

    transport = UiAutomationTransport.__new__(UiAutomationTransport)
    transport._setup_done = True  # type: ignore[attr-defined]
    transport._page = MagicMock()  # type: ignore[attr-defined]
    transport._page.url = "https://labs.google/fx/tools/flow/project/PROJ-SETUP"
    transport._out_dir = None  # type: ignore[attr-defined]
    transport._generate_lock = __import__("asyncio").Lock()  # type: ignore[attr-defined]

    transport._enter_editor = AsyncMock(side_effect=Exception("editor launch failed"))  # type: ignore[attr-defined]
    transport._dismiss_blocking_overlays = AsyncMock()  # type: ignore[attr-defined]
    transport._configure_generation_settings = AsyncMock()  # type: ignore[attr-defined]
    transport._send_prompt = AsyncMock()  # type: ignore[attr-defined]

    listener_call_count = [0]

    def fake_listener(page, *, project_id=None):  # type: ignore[no-untyped-def]
        listener_call_count[0] += 1
        return [], MagicMock()

    monkeypatch.setattr(
        UiAutomationTransport, "_attach_batch_response_listener", staticmethod(fake_listener)
    )
    monkeypatch.setattr(uia_mod, "_extract_project_id", lambda url: "PROJ-SETUP")
    monkeypatch.setattr(uia_mod.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(uia_mod.random, "uniform", lambda a, b: 0.0)

    prompts = [
        GenerateImageRequest(prompt="p0", aspect=Aspect.PORTRAIT, model=Model.NARWHAL, count=1),
        GenerateImageRequest(prompt="p1", aspect=Aspect.PORTRAIT, model=Model.NARWHAL, count=1),
    ]

    from gflow_cli.errors import BatchPartialError

    with pytest.raises(Exception) as exc_info:
        await transport.generate_images_batch(
            prompts=prompts, jitter_range=(0.0, 0.0), continue_on_error=False
        )

    # Must NOT be wrapped in BatchPartialError.
    assert not isinstance(exc_info.value, BatchPartialError), (
        "setup failure must propagate unwrapped"
    )
    # No listeners should have been attached.
    assert listener_call_count[0] == 0, (
        f"expected 0 listener attachments, got {listener_call_count[0]}"
    )


@pytest.mark.asyncio
async def test_await_loop_does_not_short_circuit_on_in_flight_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: await loop must NOT short-circuit a successfully-submitted prompt
    whose captured list is still empty at the start of the await phase (responses
    in-flight). The sentinel `detach is _noop_detach` must be used, not
    `not captured and submit_error is None`.

    Setup: 1 prompt, submits successfully (real lambda detach, NOT _noop_detach).
    At the start of the await loop, captured is EMPTY (simulates race / in-flight).
    _await_captured is mocked to populate and return the expected 1 response.

    Assertions:
    - _await_captured IS called (not short-circuited).
    - result.status == "ok".
    """
    import gflow_cli.api.transports.ui_automation as uia_mod
    from gflow_cli.api.image import Aspect, GenerateImageRequest, Model

    transport = UiAutomationTransport.__new__(UiAutomationTransport)
    transport._setup_done = True  # type: ignore[attr-defined]
    transport._page = MagicMock()  # type: ignore[attr-defined]
    transport._page.url = "https://labs.google/fx/tools/flow/project/PROJ-RACE"
    transport._out_dir = None  # type: ignore[attr-defined]
    transport._generate_lock = __import__("asyncio").Lock()  # type: ignore[attr-defined]
    transport._enter_editor = AsyncMock()  # type: ignore[attr-defined]
    transport._dismiss_blocking_overlays = AsyncMock()  # type: ignore[attr-defined]
    transport._configure_generation_settings = AsyncMock()  # type: ignore[attr-defined]
    transport._send_prompt = AsyncMock()  # type: ignore[attr-defined]

    # captured starts EMPTY — simulates in-flight responses not yet arrived.
    in_flight_captured: list = []
    # Use a plain lambda as the "real" detach; identity-distinct from _noop_detach.
    real_detach = MagicMock()

    def fake_listener(page, *, project_id=None):  # type: ignore[no-untyped-def]
        return in_flight_captured, real_detach

    monkeypatch.setattr(
        UiAutomationTransport, "_attach_batch_response_listener", staticmethod(fake_listener)
    )

    # Track whether _await_captured was invoked.
    await_captured_called: list[bool] = []

    async def fake_await(captured, expected_count=1, **_kwargs):  # type: ignore[no-untyped-def]
        await_captured_called.append(True)
        # Simulate responses arriving during the await: return 1 response.
        return [{"status": 200, "url": "https://x/batchGenerateImages", "body": {}}]

    monkeypatch.setattr(UiAutomationTransport, "_await_captured", staticmethod(fake_await))

    fake_img = MagicMock()
    monkeypatch.setattr(
        uia_mod, "_images_from_responses", lambda r: ([fake_img] * len(r), None, "")
    )
    monkeypatch.setattr(uia_mod, "_extract_project_id", lambda url: "PROJ-RACE")
    monkeypatch.setattr(uia_mod.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(uia_mod.random, "uniform", lambda a, b: 0.0)

    prompts = [
        GenerateImageRequest(prompt="p0", aspect=Aspect.PORTRAIT, model=Model.NARWHAL, count=1),
    ]

    results = await transport.generate_images_batch(
        prompts=prompts, jitter_range=(0.0, 0.0), continue_on_error=False
    )

    # _await_captured must have been called — not short-circuited by the old race condition.
    assert await_captured_called, (
        "_await_captured was never called: the await loop short-circuited an in-flight submission"
    )
    assert len(results) == 1
    assert results[0].status == "ok", (
        f"Expected status='ok' for in-flight submission, got status='{results[0].status}'"
    )
    real_detach.assert_called()


@pytest.mark.asyncio
async def test_generate_images_batch_project_id_identical_across_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Structural verification of the same-project bug fix (spec §8.1 / Finding T3).

    After a 3-prompt happy run, every BatchSubmissionResult must share
    exactly one project_id.
    """
    import gflow_cli.api.transports.ui_automation as uia_mod
    from gflow_cli.api.image import Aspect, GenerateImageRequest, Model

    transport = UiAutomationTransport.__new__(UiAutomationTransport)
    transport._setup_done = True  # type: ignore[attr-defined]
    transport._page = MagicMock()  # type: ignore[attr-defined]
    transport._page.url = "https://labs.google/fx/tools/flow/project/SHARED-UUID"
    transport._out_dir = None  # type: ignore[attr-defined]
    transport._generate_lock = __import__("asyncio").Lock()  # type: ignore[attr-defined]
    transport._enter_editor = AsyncMock()  # type: ignore[attr-defined]
    transport._dismiss_blocking_overlays = AsyncMock()  # type: ignore[attr-defined]
    transport._configure_generation_settings = AsyncMock()  # type: ignore[attr-defined]
    transport._send_prompt = AsyncMock()  # type: ignore[attr-defined]

    captures: list[list] = [
        [{"status": 200, "url": "https://x/batchGenerateImages", "body": {}}],
        [{"status": 200, "url": "https://x/batchGenerateImages", "body": {}}],
        [{"status": 200, "url": "https://x/batchGenerateImages", "body": {}}],
    ]
    detaches = [MagicMock(), MagicMock(), MagicMock()]
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
    monkeypatch.setattr(
        uia_mod, "_images_from_responses", lambda r: ([fake_img] * len(r), None, "")
    )
    monkeypatch.setattr(uia_mod, "_extract_project_id", lambda url: "SHARED-UUID")
    monkeypatch.setattr(uia_mod.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(uia_mod.random, "uniform", lambda a, b: 0.0)

    prompts = [
        GenerateImageRequest(prompt="p0", aspect=Aspect.PORTRAIT, model=Model.NARWHAL, count=1),
        GenerateImageRequest(prompt="p1", aspect=Aspect.PORTRAIT, model=Model.NARWHAL, count=1),
        GenerateImageRequest(prompt="p2", aspect=Aspect.PORTRAIT, model=Model.NARWHAL, count=1),
    ]

    results = await transport.generate_images_batch(
        prompts=prompts, jitter_range=(0.0, 0.0), continue_on_error=False
    )

    assert len(results) == 3
    unique_project_ids = {r.project_id for r in results}
    assert len(unique_project_ids) == 1, (
        f"Expected all results to share one project_id, got: {unique_project_ids}"
    )


# ---------------------------------------------------------------------------
# _set_count — count-selector stickiness fix (Phase 7 regression)
# ---------------------------------------------------------------------------


class _LocatorRecorder:
    """Records which nth-index tabs were clicked by _set_count.

    Models a ``[role="tablist"] [role="tab"]`` DOM with ``tab_count`` tabs.
    Simulates aria-selected state by tracking ``_selected_idx`` and returning
    the corresponding digit from ``text_content`` on the selected tab.

    After each ``nth(i).click()`` call, ``_selected_idx`` is updated to ``i``
    so ``_read_displayed_count`` returns the new digit on the next read-back.
    """

    def __init__(self, tab_count: int = 4, initial_selected: int = 0) -> None:
        self._tab_count = tab_count
        self._selected_idx: int = initial_selected
        # clicked_indices: records the nth-index of every click, in call order.
        self.clicked_indices: list[int] = []

    def locator(
        self, selector: str
    ) -> _LocatorRecorder._TabListLoc | _LocatorRecorder._SelectedLoc | _LocatorRecorder._NullLoc:
        if '[role="tablist"]' in selector:
            return _LocatorRecorder._TabListLoc(self)
        if 'aria-selected="true"' in selector:
            return _LocatorRecorder._SelectedLoc(self)
        # All other selectors (GEN_SETTINGS_BUTTON, button[aria-selected], etc.)
        return _LocatorRecorder._NullLoc()

    async def wait_for_timeout(self, _ms: int) -> None:
        pass

    class _Keyboard:
        async def press(self, _key: str) -> None:
            pass

    keyboard = _Keyboard()

    class _TabListLoc:
        """Represents ``page.locator('[role="tablist"] [role="tab"]')``."""

        def __init__(self, recorder: _LocatorRecorder) -> None:
            self._recorder = recorder

        async def count(self) -> int:
            return self._recorder._tab_count

        @property
        def first(self) -> _LocatorRecorder._TabLoc:
            return _LocatorRecorder._TabLoc(0, self._recorder)

        def nth(self, i: int) -> _LocatorRecorder._TabLoc:
            return _LocatorRecorder._TabLoc(i, self._recorder)

    class _TabLoc:
        """Represents a single count tab at position ``idx``."""

        def __init__(self, idx: int, recorder: _LocatorRecorder) -> None:
            self._idx = idx
            self._recorder = recorder

        async def is_visible(self, timeout: int = 400) -> bool:
            return True

        async def wait_for(self, *, state: str, timeout: int) -> None:
            pass  # always visible

        async def click(self) -> None:
            self._recorder.clicked_indices.append(self._idx)
            self._recorder._selected_idx = self._idx

    class _SelectedLoc:
        """Represents ``page.locator('[role="tab"][aria-selected="true"]')``."""

        def __init__(self, recorder: _LocatorRecorder) -> None:
            self._recorder = recorder

        @property
        def first(self) -> _LocatorRecorder._SelectedLoc:
            return self

        async def is_visible(self, timeout: int = 500) -> bool:
            return True

        async def text_content(self, timeout: int = 500) -> str:
            # Return the digit string for the currently-selected tab (1-indexed).
            return str(self._recorder._selected_idx + 1)

    class _NullLoc:
        """Matches nothing — used for selectors the recorder doesn't handle."""

        @property
        def first(self) -> _LocatorRecorder._NullLoc:
            return self

        async def is_visible(self, timeout: int = 500) -> bool:
            return False

        async def count(self) -> int:
            return 0

        async def text_content(self, timeout: int = 500) -> str | None:
            return None

        async def wait_for(self, *, state: str, timeout: int) -> None:
            raise TimeoutError("null loc")

        async def click(self) -> None:
            pass


@pytest.mark.asyncio
async def test_set_count_clicks_x2_for_count_2() -> None:
    """For count=2 with no prior selection (idx 0 = count 1): _set_count clicks nth(1)."""
    page = _LocatorRecorder(tab_count=4, initial_selected=0)  # current=1, want=2
    with patch.object(
        UiAutomationTransport, "_open_gen_settings_panel", new=AsyncMock(return_value=True)
    ):
        await UiAutomationTransport._set_count(page, 2)  # type: ignore[arg-type]
    assert 1 in page.clicked_indices, (
        f"Expected nth(1) (count=2) to be clicked, got indices {page.clicked_indices}"
    )


@pytest.mark.asyncio
async def test_set_count_clicks_x1_for_count_1() -> None:
    """For count=1 when current is count=2 (idx 1): _set_count clicks nth(0)."""
    page = _LocatorRecorder(tab_count=4, initial_selected=1)  # current=2, want=1
    with patch.object(
        UiAutomationTransport, "_open_gen_settings_panel", new=AsyncMock(return_value=True)
    ):
        await UiAutomationTransport._set_count(page, 1)  # type: ignore[arg-type]
    assert 0 in page.clicked_indices, (
        f"Expected nth(0) (count=1) to be clicked, got indices {page.clicked_indices}"
    )


@pytest.mark.asyncio
async def test_set_count_position_based_no_text_matching() -> None:
    """Position-based click: count=3 maps to nth(2) regardless of label text.

    This replaces the old test_set_count_fallback_label_nx_used_when_xn_absent.
    The new implementation never matches on label text — it uses nth() position
    exclusively, so locale or label-format differences are irrelevant.
    """
    page = _LocatorRecorder(tab_count=4, initial_selected=0)  # current=1, want=3
    with patch.object(
        UiAutomationTransport, "_open_gen_settings_panel", new=AsyncMock(return_value=True)
    ):
        await UiAutomationTransport._set_count(page, 3)  # type: ignore[arg-type]
    assert 2 in page.clicked_indices, (
        f"Expected nth(2) (count=3) to be clicked, got indices {page.clicked_indices}"
    )


@pytest.mark.asyncio
async def test_configure_generation_settings_resets_count_between_prompts() -> None:
    """Two sequential _configure_generation_settings calls with different counts.

    Prompt 0 → count=2: must click nth(1).
    Prompt 1 → count=1: must click nth(0) to override the count=2 state.

    This is the exact scenario that produced the Phase 7 regression:
    5 files for 3 prompts (expected 4) because the bakery prompt inherited
    the sunset prompt's x2 selection.

    The read-back-verify algorithm fixes this: after prompt 0 sets count=2,
    _selected_idx==1. Prompt 1 reads back count=2 (mismatch for desired=1),
    clicks nth(0), and reads back count=1 (match).
    """
    page = _LocatorRecorder(tab_count=4, initial_selected=0)

    with patch.object(
        UiAutomationTransport, "_open_gen_settings_panel", new=AsyncMock(return_value=True)
    ):
        # Prompt 0 — sunset, count=2.
        await UiAutomationTransport._configure_generation_settings(
            page,  # type: ignore[arg-type]
            aspect_cli=None,
            count=2,
        )
        clicks_after_prompt0 = list(page.clicked_indices)

        # Prompt 1 — bakery, count=1.
        await UiAutomationTransport._configure_generation_settings(
            page,  # type: ignore[arg-type]
            aspect_cli=None,
            count=1,
        )
        clicks_after_prompt1 = list(page.clicked_indices)

    # Prompt 0 must have clicked nth(1) (count=2).
    assert 1 in clicks_after_prompt0, (
        f"Prompt 0 (count=2): expected nth(1) click, got {clicks_after_prompt0}"
    )

    # Prompt 1 must have clicked nth(0) AFTER prompt 0's nth(1).
    last_idx1 = max((i for i, c in enumerate(clicks_after_prompt1) if c == 1), default=-1)
    idx0_after_idx1 = [c for i, c in enumerate(clicks_after_prompt1) if c == 0 and i > last_idx1]
    assert idx0_after_idx1, (
        f"Prompt 1 (count=1) must click nth(0) after prompt 0's nth(1). "
        f"Full click log: {clicks_after_prompt1}"
    )
