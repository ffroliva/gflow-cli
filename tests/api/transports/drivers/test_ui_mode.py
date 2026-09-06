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

from unittest.mock import AsyncMock, MagicMock

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
    assert infer_required_ui_mode(UiMode.CLASSIC, has_instructions=False) is UiMode.CLASSIC
    assert infer_required_ui_mode(UiMode.AGENTIC, has_instructions=False) is UiMode.AGENTIC


def test_infer_auto_requires_classic() -> None:
    """#595: ``auto`` means "no arm was asked for", and classic is the arm that
    can satisfy an image request. An account in Flow's agentic cohort otherwise
    binds a driver that cannot produce an image and fails mid-run."""
    assert infer_required_ui_mode(UiMode.AUTO, has_instructions=False) is UiMode.CLASSIC


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
    import gflow_cli.api.transports.mode_control as mode_control_mod

    # Page starts classic; the switch "succeeds" -> re-detect reads agentic.
    # #299 PR-B: the factory delegates to mode_control.ensure_agent_mode.
    force = AsyncMock(return_value=True)
    monkeypatch.setattr(mode_control_mod, "ensure_agent_mode", force)
    monkeypatch.setattr(factory_mod, "detect_ui_mode", AsyncMock(return_value="agentic"))
    driver = await get_ui_driver(_fake_page({_TUNE}), ui_mode=UiMode.AGENTIC)
    assert isinstance(driver, AgenticFlowUiDriver)
    force.assert_awaited_once()


@pytest.mark.asyncio
async def test_agentic_unreachable_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    import gflow_cli.api.transports.drivers.factory as factory_mod
    import gflow_cli.api.transports.mode_control as mode_control_mod

    # The switch does not take: page stays classic after the attempt.
    monkeypatch.setattr(mode_control_mod, "ensure_agent_mode", AsyncMock(return_value=False))
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


# ---------------------------------------------------------------------------
# #639 follow-up: fail fast on the migrated origin instead of probing a DOM
# that cannot answer
# ---------------------------------------------------------------------------


_CROP = "i.google-symbols:text-is('crop_16_9')"


def _page_with_present(present: set[str]):
    page = MagicMock()

    def locator(sel: str):
        loc = MagicMock()
        loc.count = AsyncMock(return_value=1 if sel in present else 0)
        return loc

    page.locator = MagicMock(side_effect=locator)
    return page


class TestMigratedOriginFailsFast:
    """`flow.google.com` renders none of the controls gflow drives, so every
    probe below is doomed before it starts. Measured cost of finding that out
    the slow way, per attempt:

        detect_ui_mode poll window   ~8 s   (both arms miss, falls to deadline)
        crop selector cascade       ~24 s
                                    -----
                                    ~32 s   before FlowHostMigratedError is raised

    Before the machine flag was corrected, callers retried on exit 36, so that
    cost was paid on every attempt of a doomed loop. The host is knowable in
    microseconds from the navigation event, so none of it needs to be spent.
    """

    @pytest.mark.asyncio
    async def test_raises_before_probing_the_dom(self) -> None:
        from gflow_cli.api.transports.drivers.factory import get_ui_driver
        from gflow_cli.errors import FlowHostMigratedError

        page = MagicMock()
        page.url = "https://flow.google.com/project/abc-123"
        page.locator = MagicMock(side_effect=AssertionError("must not probe the DOM"))

        with pytest.raises(FlowHostMigratedError):
            await get_ui_driver(page)
        page.locator.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_is_not_retryable_and_keeps_exit_36(self) -> None:
        from gflow_cli.api.transports.drivers.factory import get_ui_driver
        from gflow_cli.errors import EXIT_CODE_MAP, FlowHostMigratedError, is_retryable

        page = MagicMock()
        page.url = "https://flow.google.com/"
        page.locator = MagicMock(side_effect=AssertionError("must not probe the DOM"))

        with pytest.raises(FlowHostMigratedError) as exc:
            await get_ui_driver(page)
        # Server-assigned per-account handoff, 5/5 and 7/7 measured: not a flap.
        assert not is_retryable(exc.value)
        assert EXIT_CODE_MAP[FlowHostMigratedError] == 36

    @pytest.mark.asyncio
    async def test_bail_names_the_migration_not_selector_drift(self) -> None:
        from gflow_cli.api.transports.drivers.factory import get_ui_driver
        from gflow_cli.errors import FlowHostMigratedError

        page = MagicMock()
        page.url = "https://flow.google.com/"
        page.locator = MagicMock(side_effect=AssertionError("must not probe the DOM"))
        with pytest.raises(FlowHostMigratedError) as exc:
            await get_ui_driver(page)
        assert "flow.google.com" in str(exc.value)

    @pytest.mark.asyncio
    async def test_labs_host_is_untouched(self) -> None:
        """No regression: the old host must still go through full DOM detection."""
        from gflow_cli.api.transports.drivers.factory import detect_ui_mode

        page = _page_with_present({_CROP})
        page.url = "https://labs.google/fx/tools/flow/project/abc"
        assert await detect_ui_mode(page, timeout_s=0.1, poll_interval_s=0.01) == "classic"

    @pytest.mark.asyncio
    async def test_unreadable_url_still_probes_rather_than_bailing(self) -> None:
        """Defensive: a page whose .url is not a usable string must NOT be
        mistaken for a migrated host — that would turn a transient into a
        permanent-looking abort."""
        from gflow_cli.api.transports.drivers.factory import detect_ui_mode

        page = _page_with_present({_CROP})  # MagicMock .url, never assigned
        assert await detect_ui_mode(page, timeout_s=0.1, poll_interval_s=0.01) == "classic"


class TestCharacterEditorOnTheMigratedOrigin:
    """`character create` WORKS on flow.google.com. It must not be gated off.

    This class previously asserted the opposite — that the editor is "a labs-only
    surface: flow.google.com renders no prompt textbox for it, ever", and pinned a
    `raise_if_migrated` guard that returned exit 36 before probing the DOM.

    That premise was false, and the guard was the defect. Recon on 2026-09-06
    (`scripts/dev/spike_migrated_character_editor_anchor.py`) found the editor
    fully rendered on the migrated host — name, voice, personality, Upload / Add
    from project, Create portrait / Create body — driven by the SAME labs tRPC and
    aisandbox backend. What differed was the view layer: labs is React + Slate,
    flow.google.com is Angular + ProseMirror, so the readiness selector missed and
    a 20 s timeout got read as "the feature does not exist here". With the anchor
    widened and the guard removed, a full create succeeded end to end on
    `ci-probe` — display_name patched, workflow id and thumbnail media id bound.

    The lesson worth keeping: a selector that does not match is evidence about the
    selector, never about the feature.
    """

    @pytest.mark.asyncio
    async def test_the_migrated_origin_does_not_short_circuit_the_editor(self) -> None:
        """The DOM must be probed, not pre-judged by the host."""
        from unittest.mock import AsyncMock

        from gflow_cli.api.transports.ui_automation import UiAutomationTransport

        t = UiAutomationTransport.__new__(UiAutomationTransport)
        t._out_dir = None  # type: ignore[attr-defined]
        t._settle_if_redirecting = AsyncMock()  # type: ignore[attr-defined]
        t._dismiss_blocking_overlays = AsyncMock()  # type: ignore[attr-defined]
        t._settle_on_character_route = AsyncMock()  # type: ignore[attr-defined]

        probed: list[str] = []

        def _locator(sel: str) -> MagicMock:
            probed.append(sel)
            loc = MagicMock()
            loc.first = loc
            loc.wait_for = AsyncMock()
            return loc

        page = MagicMock()
        page.goto = AsyncMock()
        page.url = "https://flow.google.com/project/abc-123/character/e-1"
        page.locator = MagicMock(side_effect=_locator)

        await t._enter_character_editor(page, project_id="p-1", entity_id="e-1", locale="en")

        assert probed, "the migrated host must be driven, not bailed on"
        assert any("ProseMirror" in sel for sel in probed), (
            f"the readiness gate must offer the migrated anchor; probed {probed}"
        )
