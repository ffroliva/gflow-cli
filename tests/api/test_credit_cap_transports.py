import pytest

from gflow_cli.api.transports.agent_only_composer import gate_quoted_credits
from gflow_cli.flow_selectors.registry import SELECTORS


def test_credit_quote_is_not_registered_on_the_always_loaded_editor_surface() -> None:
    assert not any(item.key == "migrated.composer.credit_cost" for item in SELECTORS)


def test_agent_gate_quote_is_strict_and_unambiguous() -> None:
    assert gate_quoted_credits("Generate 1 video, costing 4 credits?") == 4
    assert gate_quoted_credits("Generate 1 video, costing 1,000 credits?") is None
    assert gate_quoted_credits("Generate 1 video, costing 1.000 credits?") is None
    assert gate_quoted_credits("Generate 1 video, costing 1 000 credits?") is None
    assert gate_quoted_credits("Generate 1 video") is None


@pytest.mark.asyncio
async def test_agent_gate_waits_for_a_stable_complete_title() -> None:
    from gflow_cli.api.transports.agent_only_composer import AgentOnlyComposer, _Run

    class Title:
        def __init__(self, page):
            self.page = page
            self.first = self

        async def inner_text(self, **kwargs):
            return self.page.title

    class Gate:
        def __init__(self, page):
            self.page = page
            self.first = self

        async def count(self):
            return 1

        def nth(self, _):
            return self

        def locator(self, _):
            return Title(self.page)

    class Page:
        title = "Kick off 1 video for 15"

        def locator(self, _):
            return Gate(self)

    run = _Run("video", 60, 1e20, 0, 1, 0, max_credits=20)
    page = Page()
    composer = AgentOnlyComposer()
    assert not await composer._gate_within_count(page, run)
    page.title = "Kick off 1 video for 15 credits"
    assert not await composer._gate_within_count(page, run)
    assert await composer._gate_within_count(page, run)
    assert run.quoted_credits == 15


@pytest.mark.asyncio
async def test_agent_gate_cap_refuses_an_over_limit_quote_even_when_reject_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from gflow_cli.api.transports.agent_only_composer import AgentOnlyComposer, _Run
    from gflow_cli.errors import CreditCapExceededError

    composer = AgentOnlyComposer()
    reject = AsyncMock(return_value=False)
    monkeypatch.setattr(composer, "_reject_gate", reject)
    run = _Run(
        "video",
        60,
        1e20,
        0,
        1,
        0,
        max_credits=3,
        last_gate_title="Kick off 1 video for 4 credits",
        stable_gate_polls=1,
    )

    class Title:
        first = None

        def __init__(self) -> None:
            self.first = self

        async def inner_text(self, **_: object) -> str:
            return "Kick off 1 video for 4 credits"

    class Gate:
        def nth(self, _index: int):
            return self

        def locator(self, _selector: str) -> Title:
            return Title()

    class Page:
        def locator(self, _selector: str) -> Gate:
            return Gate()

    with pytest.raises(CreditCapExceededError) as caught:
        await composer._gate_within_count(Page(), run)
    assert caught.value.charged == "maybe"
    assert caught.value.next_action == "reconcile"
    assert "still open" in caught.value.remediation_hint
    reject.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_gate_waits_until_settle_before_refusing_missing_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from gflow_cli.api.transports import agent_only_composer as agent
    from gflow_cli.errors import CreditCapExceededError

    composer = agent.AgentOnlyComposer()
    run = agent._Run("video", 60, 1e20, 0, 1, 0, max_credits=10, gate_seen_at=100)
    monkeypatch.setattr(agent.time, "monotonic", lambda: 105)
    reject = AsyncMock(return_value=True)
    monkeypatch.setattr(composer, "_reject_gate", reject)
    assert not await composer._gate_cap_ready(object(), run, "Kick off 1 video")
    reject.assert_not_awaited()

    monkeypatch.setattr(agent.time, "monotonic", lambda: 111)
    with pytest.raises(CreditCapExceededError) as caught:
        await composer._gate_cap_ready(object(), run, "Kick off 1 video")
    assert caught.value.quoted is None
    reject.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("fresh", [None, 10])
async def test_migrated_submit_refuses_a_missing_or_changed_second_quote(
    monkeypatch: pytest.MonkeyPatch, fresh: int | None
) -> None:
    from unittest.mock import AsyncMock

    from gflow_cli.api.transports import _mc_settings_pane, migrated_composer
    from gflow_cli.errors import CreditCapExceededError

    read = AsyncMock(return_value=fresh)
    monkeypatch.setattr(_mc_settings_pane, "_read_credit_quote", read)
    page = object()
    with pytest.raises(CreditCapExceededError) as caught:
        await migrated_composer._refresh_credit_quote(page, 10, 8)
    assert caught.value.quoted is None
    read.assert_awaited_once_with(page, 10)


@pytest.mark.asyncio
async def test_unset_cap_skips_the_quote_read(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import AsyncMock

    from gflow_cli.api.transports import _mc_settings_pane, migrated_composer

    read = AsyncMock(side_effect=AssertionError("an unset cap should not read a quote"))
    monkeypatch.setattr(_mc_settings_pane, "_read_credit_quote", read)
    assert await migrated_composer._refresh_credit_quote(object(), None, None) is None
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_migrated_video_reads_quote_again_after_settings_before_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from gflow_cli.api.transports import _mc_settings_pane, migrated_composer
    from gflow_cli.api.transports.batchexecute import STATUS_DONE, GenerationRecord
    from gflow_cli.api.video import GenerateVideoRequest

    read = AsyncMock(side_effect=[8, 8])
    monkeypatch.setattr(_mc_settings_pane, "_read_credit_quote", read)
    composer = MagicMock()

    async def apply_settings(page: object, request: GenerateVideoRequest) -> int | None:
        return await read(page, request.max_credits)

    composer.apply_video_settings = apply_settings
    composer.send_prompt = AsyncMock()
    composer.submit_and_observe = AsyncMock(
        return_value=GenerationRecord("op", "p", "media", STATUS_DONE)
    )
    monkeypatch.setattr(
        migrated_composer,
        "_prepare_video_composer",
        AsyncMock(return_value=("p", composer, "classic")),
    )

    class Watch:
        seen = staticmethod(lambda: False)

        def __init__(self, _page: object) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(migrated_composer, "SubmitWatch", Watch)
    page = MagicMock()
    await migrated_composer.run_video(
        page,
        GenerateVideoRequest(prompt="cat", max_credits=10),
        project_id="p",
        save=AsyncMock(),
        poll_timeout_s=20,
        download=False,
        on_started=None,
    )
    assert read.await_count == 2
    composer.submit_and_observe.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_video_cap_requires_confirmation_when_user_disabled_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from gflow_cli.api.transports import agent_only_composer as agent
    from gflow_cli.api.video import GenerateVideoRequest
    from gflow_cli.errors import CreditCapExceededError

    monkeypatch.setattr(agent, "_confirm_setting", lambda: "never")
    apply = AsyncMock()
    monkeypatch.setattr(agent.AgentOnlyComposer, "apply_defaults", apply)
    with pytest.raises(CreditCapExceededError, match="unavailable"):
        await agent.run_agent_video(
            object(),
            GenerateVideoRequest(prompt="cat", max_credits=4),
            project_id="p",
            save=AsyncMock(),
            poll_timeout_s=1,
            download=False,
            on_started=None,
        )
    apply.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("current", ["account", "always"])
async def test_agent_video_cap_forces_confirm_always(
    monkeypatch: pytest.MonkeyPatch, current: str
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from gflow_cli.api.transports import agent_only_composer as agent
    from gflow_cli.api.video import GenerateVideoRequest

    monkeypatch.setattr(agent, "_confirm_setting", lambda: current)
    apply = AsyncMock(return_value=MagicMock())
    generate = AsyncMock(return_value=[agent.TileMedia("media", "https://flow-content.google/v")])
    restore = AsyncMock()
    monkeypatch.setattr(agent.AgentOnlyComposer, "apply_defaults", apply)
    monkeypatch.setattr(agent.AgentOnlyComposer, "generate", generate)
    monkeypatch.setattr(agent.AgentOnlyComposer, "restore_defaults", restore)
    await agent.run_agent_video(
        object(),
        GenerateVideoRequest(prompt="cat", max_credits=4),
        project_id="p",
        save=AsyncMock(),
        poll_timeout_s=1,
        download=False,
        on_started=None,
    )
    assert apply.await_args.kwargs["confirm"] == "always"
    restore.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_defaults_restore_original_confirm_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from gflow_cli.api.transports import agent_only_composer as agent

    class Radio:
        async def count(self) -> int:
            return 1

        async def is_checked(self) -> bool:
            return False

        async def check(self, **_: object) -> None:
            self.checked = True

    radio = Radio()
    page = type("Page", (), {"locator": lambda _self, _selector: radio})()
    composer = agent.AgentOnlyComposer()
    monkeypatch.setattr(composer, "_open_pane", AsyncMock())
    monkeypatch.setattr(composer, "_check_radio", AsyncMock())
    monkeypatch.setattr(composer, "_choose_model", AsyncMock())
    monkeypatch.setattr(composer, "_save", AsyncMock())
    await composer.restore_defaults(
        page, agent.DefaultsSnapshot("video", 0, 0, "model", confirm_value="2")
    )
    assert radio.checked


@pytest.mark.asyncio
async def test_agent_video_with_media_but_no_approved_gate_is_maybe_charged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from gflow_cli.api.transports import agent_only_composer as agent
    from gflow_cli.errors import CreditCapExceededError

    composer = agent.AgentOnlyComposer()
    monkeypatch.setattr(agent, "_video_uuids", lambda _state: ["orphan-media"])
    monkeypatch.setattr(composer, "_gate_turn", AsyncMock(return_value=False))
    monkeypatch.setattr(
        composer, "_ready_video", AsyncMock(side_effect=AssertionError("cap check was skipped"))
    )
    page = type("Page", (), {"evaluate": AsyncMock(return_value={"pending": 1})})()
    run = agent._Run("video", 60, 1e20, 0, 1, 0, max_credits=10)

    with pytest.raises(CreditCapExceededError) as caught:
        await composer._await_video(page, run, 1, set(), None, AsyncMock(), None)
    assert caught.value.charged == "maybe"
    assert caught.value.next_action == "reconcile"
    assert caught.value.completed_media_ids == ["orphan-media"]
