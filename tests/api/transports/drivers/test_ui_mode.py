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


# ---------------------------------------------------------------------------
# CLI --ui-mode flag → GenerateImageRequest.ui_mode threading
# ---------------------------------------------------------------------------


def test_t2i_ui_mode_flag_threads_onto_request() -> None:
    from unittest.mock import AsyncMock, patch

    from click.testing import CliRunner

    from gflow_cli.cli import main

    run_t2i = AsyncMock()
    with (
        patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_image._make_provider_dir"),
        patch("gflow_cli.cli_image._run_t2i", run_t2i),
    ):
        result = CliRunner().invoke(
            main, ["image", "t2i", "a prompt", "--ui-mode", "classic"], catch_exceptions=False
        )

    assert result.exit_code == 0, result.output
    assert run_t2i.await_args.kwargs["req"].ui_mode is UiMode.CLASSIC


def test_t2i_ui_mode_multi_prompt_rejected() -> None:
    from click.testing import CliRunner

    from gflow_cli.cli import main

    result = CliRunner().invoke(
        main, ["image", "t2i", "a", "b", "--ui-mode", "classic"], catch_exceptions=False
    )
    assert result.exit_code == 2
    assert "single-prompt only" in result.output


def test_t2i_ui_mode_classic_with_instructions_conflict() -> None:
    from click.testing import CliRunner

    from gflow_cli.cli import main

    result = CliRunner().invoke(
        main,
        ["image", "t2i", "a", "--ui-mode", "classic", "-i", "crayon"],
        catch_exceptions=False,
    )
    assert result.exit_code == 2
    assert "incompatible" in result.output.lower()


# ---------------------------------------------------------------------------
# Worker payload → GenerateImageRequest.ui_mode (MCP path)
# ---------------------------------------------------------------------------


def test_worker_builds_ui_mode_from_payload() -> None:
    from gflow_cli.worker.daemon import FlowWorker

    req = FlowWorker._build_image_request(  # type: ignore[arg-type]
        object.__new__(FlowWorker),
        {"prompt": "a", "model": "nano2", "aspect": "1:1", "ui_mode": "agentic"},
    )
    assert req.ui_mode is UiMode.AGENTIC


def test_worker_ui_mode_absent_is_none() -> None:
    from gflow_cli.worker.daemon import FlowWorker

    req = FlowWorker._build_image_request(  # type: ignore[arg-type]
        object.__new__(FlowWorker),
        {"prompt": "a", "model": "nano2", "aspect": "1:1"},
    )
    assert req.ui_mode is None


def test_worker_builds_video_ui_mode_from_payload() -> None:
    # #299 PR-A: the video queue payload round-trips ui_mode (the image-only
    # decode at codec.py was the documented MCP->FlowWorker silent-drop trap).
    from gflow_cli.worker.codec import build_video_request

    req = build_video_request({"prompt": "a", "ui_mode": "classic"})
    assert req.ui_mode is UiMode.CLASSIC


def test_worker_video_ui_mode_absent_is_none() -> None:
    from gflow_cli.worker.codec import build_video_request

    req = build_video_request({"prompt": "a"})
    assert req.ui_mode is None


def test_ui_mode_unavailable_is_retryable() -> None:
    # #299 code-review finding: every doc surface (exit-code table, CHANGELOG,
    # error docstring) says exit 28 is retryable — the machine flag consumed by
    # CLI --json / MCP / worker envelopes must agree.
    from gflow_cli.errors import UiModeUnavailableError, is_retryable

    assert is_retryable(UiModeUnavailableError(UiMode.CLASSIC)) is True


def test_worker_video_ui_mode_agentic_payload_rejected() -> None:
    # A hand-edited / cross-version queue payload carrying agentic must fail
    # typed (ValueError -> QueueSchemaError at decode_payload), never clamp.
    import pytest as _pytest

    from gflow_cli.worker.codec import build_video_request

    with _pytest.raises(ValueError, match="agentic"):
        build_video_request({"prompt": "a", "ui_mode": "agentic"})
