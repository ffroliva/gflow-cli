"""E2E for the editor gate's way out of Flow's agent mode and agent panel (#1374, #1384).

Binds ``tests/features/agent_panel_recovery.feature``. Its ``@e2e @e2e_auth`` tags become
pytest markers, see ``docs/E2E_TESTING.md`` § BDD-bound e2e.

    GFLOW_CLI_E2E_PROFILE=<profile> GFLOW_CLI_E2E_PROJECT=<project-uuid> \\
        uv run pytest -m e2e_auth tests/e2e/test_agent_panel_recovery_bdd.py -v

**Why an e2e.** The defect is what Flow renders: which toggle it uses, whether that toggle
is in the page while the panel is open, and which ligature closes the panel. Both builds
changed all three (spike ``2026-11-20-agent-mode-switch-and-panel-rendering.md``). An offline
page can only replay what was measured once.

**Cost: $0.** Layout clicks, plus video submits aborted in Chrome before they leave
(``_abort_video_submits``, the same guard as ``test_project_create_e2e.py``).

**Account state.** Agent mode and the open panel are remembered for the account. Every Given
starts from the classic composer, and the ``_restore_classic`` teardown puts the account
back whatever the outcome. The setup and teardown helpers deliberately use their own
selectors, not the constants under test, so a broken fix cannot also break the restore.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar

import pytest
from pytest_bdd import given, scenarios, then, when

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports.migrated_composer import MIGRATED_PROJECT_URL, MigratedComposer
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode
from gflow_cli.config import reset_settings
from gflow_cli.errors import TransportTimeoutError
from gflow_cli.mcp import tools as mcp_tools
from tests.e2e.test_project_create_e2e import _abort_video_submits

scenarios("../features/agent_panel_recovery.feature")

_PROJECT_ENV = "GFLOW_CLI_E2E_PROJECT"
_PROMPT = "a teal origami crane on a wooden table"
_TRIGGER = ".settings-trigger-button"
#: Both builds' Agent toggle: the 2026-11-19 chip and the 2026-11-20 switch.
_TOGGLE = "button.agent-mode-chip, flow-agent-mode-switch button.switch"
_PRESSED = (
    "button.agent-mode-chip[aria-pressed='true'], "
    "flow-agent-mode-switch button.switch[aria-pressed='true']"
)
_EXPAND_LIGATURES = ("right_panel_open", "expand_content")
_CLOSE_LIGATURES = ("right_panel_close", "close")

T = TypeVar("T")


def _project_id() -> str:
    pid = os.environ.get(_PROJECT_ENV, "").strip()
    if not pid:
        pytest.skip(f"{_PROJECT_ENV} must name an existing Flow project id")
    return pid


async def _on_project(profile_dir: Path, project: str, fn: Callable[[Any], Awaitable[T]]) -> T:
    """Open the profile, land on the project, run `fn(page)`, and always release the profile."""
    transport = UiAutomationTransport()
    try:
        await transport.setup(profile_dir)
        page = transport._page  # noqa: SLF001 - the e2e reads the live page
        assert page is not None
        await page.goto(
            MIGRATED_PROJECT_URL.format(project_id=project), wait_until="domcontentloaded"
        )
        await page.locator(f"{_TOGGLE}, {_TRIGGER}, flow-agent-panel").first.wait_for(
            state="attached", timeout=45_000
        )
        await page.wait_for_timeout(3000)
        return await fn(page)
    finally:
        await transport.teardown()


async def _restore(page: Any) -> bool:
    """Close the panel (either build), turn a pressed toggle off; True when classic is back."""
    for ligature in _CLOSE_LIGATURES:
        close = page.locator(f"flow-agent-panel button:has(mat-icon:text-is('{ligature}'))").first
        if await close.count() and await close.is_visible():
            await close.click(timeout=10_000)
            await page.wait_for_timeout(2500)
            break
    pressed = page.locator(_PRESSED).first
    if await pressed.count():
        await pressed.click(timeout=10_000)
        await page.wait_for_timeout(2500)
    return bool(await page.locator(_TRIGGER).first.is_visible())


async def _leave_in_agent_mode(page: Any, *, panel: bool) -> None:
    if not await _restore(page):
        pytest.fail("the project did not start from the classic composer")
    toggle = page.locator(_TOGGLE).first
    if not await toggle.count():
        pytest.skip("this account's composer renders no Agent toggle")
    await toggle.click(timeout=10_000)
    await page.locator(_TRIGGER).first.wait_for(state="hidden", timeout=15_000)
    if panel:
        for ligature in _EXPAND_LIGATURES:
            expand = page.locator(
                f"flow-creative-agent-prompt-box button:has(mat-icon:text-is('{ligature}'))"
            ).first
            if await expand.count() and await expand.is_visible():
                await expand.click(timeout=10_000)
                break
        await page.locator("flow-agent-panel").first.wait_for(state="visible", timeout=15_000)
    # Flow keeps the state across a reload (spike step C3); prove it before the When.
    await page.reload(wait_until="domcontentloaded")
    await page.wait_for_timeout(4000)


@pytest.fixture
def world() -> dict[str, Any]:
    return {}


@pytest.fixture(autouse=True)
def _restore_classic(e2e_profile_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("GFLOW_CLI_FLOW_HOST", raising=False)
    reset_settings()
    yield
    project = os.environ.get(_PROJECT_ENV, "").strip()
    if not project:
        return
    try:
        if not asyncio.run(_on_project(e2e_profile_dir, project, _restore)):
            print("agent-mode restore: the classic composer did not come back")
    except Exception as cleanup_error:  # noqa: BLE001 - never mask the verdict
        print(f"agent-mode restore failed, the account may be left in agent mode: {cleanup_error}")


# --- Given ------------------------------------------------------------------------------


@given("the project is left with agent mode on and the agent panel open")
def _panel_open(world: dict[str, Any], e2e_profile_dir: Path) -> None:
    world["project"] = _project_id()

    async def _arrange(page: Any) -> None:
        await _leave_in_agent_mode(page, panel=True)

    asyncio.run(_on_project(e2e_profile_dir, world["project"], _arrange))


@given("the project is left with agent mode on and the agent panel closed")
def _panel_closed(world: dict[str, Any], e2e_profile_dir: Path) -> None:
    world["project"] = _project_id()

    async def _arrange(page: Any) -> None:
        await _leave_in_agent_mode(page, panel=False)

    asyncio.run(_on_project(e2e_profile_dir, world["project"], _arrange))


# --- When -------------------------------------------------------------------------------


@when("gflow prepares the editor")
def _prepare(world: dict[str, Any], e2e_profile_dir: Path) -> None:
    project = world["project"]

    async def _gate(page: Any) -> str:
        return str(await MigratedComposer().ensure_editor(page, project, timeout_s=45.0))

    world["kind"] = asyncio.run(_on_project(e2e_profile_dir, project, _gate))


@when("the CLI client generates a text-to-video clip with the submit aborted")
def _cli_video(
    world: dict[str, Any], e2e_profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []
    _abort_video_submits(monkeypatch, captured)
    world["captured"] = captured
    req = GenerateVideoRequest(prompt=_PROMPT, mode=Mode.T2V, aspect=Aspect.LANDSCAPE)

    async def _run() -> None:
        async with FlowApiClient(profile_dir=e2e_profile_dir) as client:
            with pytest.raises(TransportTimeoutError):
                await client.generate_video(
                    req=req, project_id=world["project"], poll_timeout_s=20.0, download=False
                )

    asyncio.run(_run())


@when("the MCP video tool generates a text-to-video clip with the submit aborted")
def _mcp_video(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []
    _abort_video_submits(monkeypatch, captured)
    world["captured"] = captured

    async def _run() -> dict[str, Any]:
        return await mcp_tools.gflow_generate_video(
            prompt=_PROMPT,
            mode="t2v",
            aspect="16:9",
            profile=os.environ["GFLOW_CLI_E2E_PROFILE"].strip(),
            project=world["project"],
            wait=True,
        )

    world["result"] = asyncio.run(_run())


# --- Then -------------------------------------------------------------------------------


@then("the composer is the classic one")
def _classic(world: dict[str, Any]) -> None:
    assert world["kind"] == "classic"


@then("the classic composer is still served after a reload")
def _persists(world: dict[str, Any], e2e_profile_dir: Path) -> None:
    async def _visible(page: Any) -> bool:
        await page.locator(_TRIGGER).first.wait_for(state="visible", timeout=15_000)
        return True

    assert asyncio.run(_on_project(e2e_profile_dir, world["project"], _visible))


@then("a video submit was attempted")
def _submitted(world: dict[str, Any]) -> None:
    assert world["captured"], f"no video submit was attempted: {world.get('result')}"
