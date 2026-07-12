"""--ui-mode policy: fail-fast when the wanted UI arm isn't the live one (#299).

Item 1 (reshaped per /gflow:predict): the gate keys on the DOM `detect_ui_mode`
ground truth (NOT a pre-navigation tRPC read — the arm flaps per load). A
`classic`-strict request aborts pre-submission with `ClassicUiUnavailableError`
(exit 28) when the arm is agentic. `--ui-mode` subsumes the deprecated
`GFLOW_CLI_PREFER_CLASSIC`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from gflow_cli.api.transports.drivers.agentic import AgenticFlowUiDriver
from gflow_cli.api.transports.drivers.classic import ClassicFlowUiDriver
from gflow_cli.api.transports.drivers.factory import get_ui_driver
from gflow_cli.config import UiMode, reset_settings, resolve_ui_mode
from gflow_cli.errors import EXIT_CODE_MAP, ClassicUiUnavailableError, FlowAgentUiError

_CROP = "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_16_9'))"
_TUNE = "i.google-symbols:text-is('tune')"


def _fake_page(present: set[str]):
    """A Page stub whose locator(sel).count() is >0 only for `present` selectors."""

    class _Loc:
        def __init__(self, n: int) -> None:
            self._n = n

        async def count(self) -> int:
            return self._n

    class _Page:
        def locator(self, sel: str):  # noqa: ANN202
            return _Loc(1 if sel in present else 0)

    return _Page()


# ---------------------------------------------------------------------------
# resolve_ui_mode — precedence + prefer_classic deprecation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GFLOW_CLI_UI_MODE", raising=False)
    monkeypatch.delenv("GFLOW_CLI_PREFER_CLASSIC", raising=False)
    reset_settings()
    yield
    reset_settings()


def test_default_is_auto() -> None:
    assert resolve_ui_mode(None) is UiMode.AUTO


def test_cli_value_wins() -> None:
    assert resolve_ui_mode("classic") is UiMode.CLASSIC
    assert resolve_ui_mode("agentic") is UiMode.AGENTIC


def test_env_used_when_no_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_UI_MODE", "classic")
    reset_settings()
    assert resolve_ui_mode(None) is UiMode.CLASSIC


def test_cli_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_UI_MODE", "agentic")
    reset_settings()
    assert resolve_ui_mode("classic") is UiMode.CLASSIC


def test_deprecated_prefer_classic_maps_to_classic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_PREFER_CLASSIC", "true")
    reset_settings()
    with pytest.warns(DeprecationWarning, match="GFLOW_CLI_PREFER_CLASSIC is deprecated"):
        assert resolve_ui_mode(None) is UiMode.CLASSIC


def test_ui_mode_beats_deprecated_prefer_classic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_PREFER_CLASSIC", "true")
    monkeypatch.setenv("GFLOW_CLI_UI_MODE", "agentic")
    reset_settings()
    assert resolve_ui_mode(None) is UiMode.AGENTIC


# ---------------------------------------------------------------------------
# get_ui_driver — the fail-fast gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_binds_detected_agentic() -> None:
    driver = await get_ui_driver(_fake_page({_TUNE}), ui_mode=UiMode.AUTO)
    assert isinstance(driver, AgenticFlowUiDriver)


@pytest.mark.asyncio
async def test_auto_binds_detected_classic() -> None:
    driver = await get_ui_driver(_fake_page({_CROP}), ui_mode=UiMode.AUTO)
    assert isinstance(driver, ClassicFlowUiDriver)


@pytest.mark.asyncio
async def test_classic_strict_aborts_on_agentic(monkeypatch: pytest.MonkeyPatch) -> None:
    # Best-effort exit is attempted and fails (native agentic cohort), so the
    # arm stays agentic -> fail fast BEFORE any submission.
    from gflow_cli.api.transports import ui_automation_video

    monkeypatch.setattr(
        ui_automation_video.VideoGenerationMixin,
        "_exit_agent_mode",
        AsyncMock(side_effect=FlowAgentUiError("cannot exit")),
    )
    with pytest.raises(ClassicUiUnavailableError):
        await get_ui_driver(_fake_page({_TUNE}), ui_mode=UiMode.CLASSIC)


@pytest.mark.asyncio
async def test_classic_strict_binds_classic_when_recovered(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.api.transports import ui_automation_video

    # Exit attempt "succeeds": after it, the page reads classic.
    page = _fake_page({_CROP})
    monkeypatch.setattr(
        ui_automation_video.VideoGenerationMixin,
        "_exit_agent_mode",
        AsyncMock(return_value=None),
    )
    driver = await get_ui_driver(page, ui_mode=UiMode.CLASSIC)
    assert isinstance(driver, ClassicFlowUiDriver)


@pytest.mark.asyncio
async def test_agentic_mode_skips_exit_attempt_binds_agentic() -> None:
    # No exit attempt; binds whatever renders (agentic here).
    driver = await get_ui_driver(_fake_page({_TUNE}), ui_mode=UiMode.AGENTIC)
    assert isinstance(driver, AgenticFlowUiDriver)


# ---------------------------------------------------------------------------
# exit code
# ---------------------------------------------------------------------------


def test_classic_unavailable_exit_code_is_28() -> None:
    assert EXIT_CODE_MAP[ClassicUiUnavailableError] == 28


def test_classic_unavailable_is_flow_agent_ui_subclass() -> None:
    # Preserves `except FlowAgentUiError` catch-compatibility.
    assert issubclass(ClassicUiUnavailableError, FlowAgentUiError)
