"""ensure-required-mode gate: verify the arm the command needs, switch as a
prerequisite, verify the switch took, else fail fast (#299).

The gate keys on the DOM `detect_ui_mode` ground truth. A command's *required*
arm comes from `--ui-mode`/`GFLOW_CLI_UI_MODE`, or is inferred (agent
instructions `-i` are agentic-only, so they force agentic). When the required
arm can't be reached, `UiModeUnavailableError` (exit 28, retryable) aborts
BEFORE submission — zero credits — instead of silently generating on the wrong
arm (which today drops `-i` cards and mis-hints aspect ratios).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from gflow_cli.api.transports.drivers.agentic import AgenticFlowUiDriver
from gflow_cli.api.transports.drivers.classic import ClassicFlowUiDriver
from gflow_cli.api.transports.drivers.factory import get_ui_driver
from gflow_cli.config import (
    UiMode,
    infer_required_ui_mode,
    reset_settings,
    resolve_ui_mode,
)
from gflow_cli.errors import EXIT_CODE_MAP, ConfigurationError, UiModeUnavailableError

_CROP = "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_16_9'))"
_TUNE = "i.google-symbols:text-is('tune')"


def _fake_page(present: set[str]):
    class _Loc:
        def __init__(self, n: int) -> None:
            self._n = n

        async def count(self) -> int:
            return self._n

    class _Page:
        def locator(self, sel: str):  # noqa: ANN202
            return _Loc(1 if sel in present else 0)

    return _Page()


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    for var in ("GFLOW_CLI_UI_MODE", "GFLOW_CLI_PREFER_CLASSIC", "GFLOW_CLI_FORCE_AGENT_UI"):
        monkeypatch.delenv(var, raising=False)
    reset_settings()
    yield
    reset_settings()


# ---------------------------------------------------------------------------
# resolve_ui_mode — explicit intent + deprecated aliases
# ---------------------------------------------------------------------------


def test_default_is_auto() -> None:
    assert resolve_ui_mode(None) is UiMode.AUTO


def test_cli_value_wins() -> None:
    assert resolve_ui_mode("classic") is UiMode.CLASSIC
    assert resolve_ui_mode("agentic") is UiMode.AGENTIC


def test_env_used_when_no_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_UI_MODE", "agentic")
    reset_settings()
    assert resolve_ui_mode(None) is UiMode.AGENTIC


def test_deprecated_prefer_classic_maps_to_classic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_PREFER_CLASSIC", "true")
    reset_settings()
    with pytest.warns(DeprecationWarning, match="GFLOW_CLI_PREFER_CLASSIC"):
        assert resolve_ui_mode(None) is UiMode.CLASSIC


def test_deprecated_force_agent_maps_to_agentic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_FORCE_AGENT_UI", "1")
    reset_settings()
    with pytest.warns(DeprecationWarning, match="GFLOW_CLI_FORCE_AGENT_UI"):
        assert resolve_ui_mode(None) is UiMode.AGENTIC


def test_explicit_ui_mode_beats_deprecated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_PREFER_CLASSIC", "true")
    monkeypatch.setenv("GFLOW_CLI_UI_MODE", "agentic")
    reset_settings()
    assert resolve_ui_mode(None) is UiMode.AGENTIC


# ---------------------------------------------------------------------------
# infer_required_ui_mode — command needs drive the required arm
# ---------------------------------------------------------------------------


def test_infer_passthrough_without_instructions() -> None:
    assert infer_required_ui_mode(UiMode.AUTO, has_instructions=False) is UiMode.AUTO
    assert infer_required_ui_mode(UiMode.CLASSIC, has_instructions=False) is UiMode.CLASSIC
    assert infer_required_ui_mode(UiMode.AGENTIC, has_instructions=False) is UiMode.AGENTIC


def test_infer_instructions_force_agentic_from_auto() -> None:
    # -i cards are agentic-only, so a classic bind would silently drop them.
    assert infer_required_ui_mode(UiMode.AUTO, has_instructions=True) is UiMode.AGENTIC


def test_infer_instructions_ok_with_explicit_agentic() -> None:
    assert infer_required_ui_mode(UiMode.AGENTIC, has_instructions=True) is UiMode.AGENTIC


def test_infer_classic_plus_instructions_is_a_conflict() -> None:
    with pytest.raises(ConfigurationError, match="instructions"):
        infer_required_ui_mode(UiMode.CLASSIC, has_instructions=True)


# ---------------------------------------------------------------------------
# get_ui_driver — detect → switch (prerequisite) → verify → fail fast
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_binds_detected() -> None:
    assert isinstance(
        await get_ui_driver(_fake_page({_TUNE}), ui_mode=UiMode.AUTO), AgenticFlowUiDriver
    )
    assert isinstance(
        await get_ui_driver(_fake_page({_CROP}), ui_mode=UiMode.AUTO), ClassicFlowUiDriver
    )


@pytest.mark.asyncio
async def test_classic_recovers_and_binds(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.api.transports import ui_automation_video

    monkeypatch.setattr(
        ui_automation_video.VideoGenerationMixin,
        "_exit_agent_mode",
        AsyncMock(return_value=None),
    )
    driver = await get_ui_driver(_fake_page({_CROP}), ui_mode=UiMode.CLASSIC)
    assert isinstance(driver, ClassicFlowUiDriver)


@pytest.mark.asyncio
async def test_classic_unreachable_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.api.transports import ui_automation_video
    from gflow_cli.errors import FlowAgentUiError

    monkeypatch.setattr(
        ui_automation_video.VideoGenerationMixin,
        "_exit_agent_mode",
        AsyncMock(side_effect=FlowAgentUiError("cannot exit")),
    )
    with pytest.raises(UiModeUnavailableError) as exc:
        await get_ui_driver(_fake_page({_TUNE}), ui_mode=UiMode.CLASSIC)
    assert exc.value.requested is UiMode.CLASSIC


@pytest.mark.asyncio
async def test_agentic_switch_and_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    import gflow_cli.api.transports.drivers.factory as factory_mod
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport

    # Page starts classic; the force switch "succeeds" -> re-detect reads agentic.
    force = AsyncMock(return_value=True)
    monkeypatch.setattr(UiAutomationTransport, "_force_agent_mode", force)
    monkeypatch.setattr(factory_mod, "detect_ui_mode", AsyncMock(return_value="agentic"))
    driver = await get_ui_driver(_fake_page({_TUNE}), ui_mode=UiMode.AGENTIC)
    assert isinstance(driver, AgenticFlowUiDriver)
    force.assert_awaited_once()


@pytest.mark.asyncio
async def test_agentic_unreachable_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    import gflow_cli.api.transports.drivers.factory as factory_mod
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport

    # Force does not take: page stays classic after the switch attempt.
    monkeypatch.setattr(UiAutomationTransport, "_force_agent_mode", AsyncMock(return_value=False))
    monkeypatch.setattr(factory_mod, "detect_ui_mode", AsyncMock(return_value="classic"))
    with pytest.raises(UiModeUnavailableError) as exc:
        await get_ui_driver(_fake_page({_CROP}), ui_mode=UiMode.AGENTIC)
    assert exc.value.requested is UiMode.AGENTIC


# ---------------------------------------------------------------------------
# exit code
# ---------------------------------------------------------------------------


def test_ui_mode_unavailable_exit_code_is_28() -> None:
    assert EXIT_CODE_MAP[UiModeUnavailableError] == 28
