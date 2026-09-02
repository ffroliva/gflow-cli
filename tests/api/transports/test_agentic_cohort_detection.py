"""Unit tests for the #183 media-library/agentic cohort raise-site handling.

`_detect_non_classic_cohort` scans the union of agentic + full-page media-library
markers so the shared `_mode_switch_error` raise site can emit a clean, retryable
`FlowAgentUiError` instead of the misleading `UiSelectorDriftError`.
`capture_ui_diagnostics` writes the structural DOM-signature JSON (no screenshot).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.transports.ui_automation_video import (
    LIBRARY_UI_INDICATORS,
    NON_CLASSIC_COHORT_INDICATORS,
    VideoGenerationMixin,
    capture_ui_diagnostics,
)


def _page_with_present(present: set[str]) -> MagicMock:
    """Page mock whose ``locator(sel).count()`` returns 1 iff sel is in *present*."""
    page = MagicMock()

    def locator(sel: str) -> MagicMock:
        loc = MagicMock()
        loc.count = AsyncMock(return_value=1 if sel in present else 0)
        return loc

    page.locator = MagicMock(side_effect=locator)
    page.title = AsyncMock(return_value="Google Flow")  # not the app-crash error page
    return page


# --- _detect_non_classic_cohort ------------------------------------------------


def test_library_indicators_are_a_subset_of_the_cohort_union() -> None:
    assert set(LIBRARY_UI_INDICATORS) <= set(NON_CLASSIC_COHORT_INDICATORS)
    # The full-page library is keyed on locale-invariant sidebar ligatures.
    assert any("left_panel_close" in s for s in LIBRARY_UI_INDICATORS)


@pytest.mark.asyncio
async def test_detects_full_page_library_marker() -> None:
    lib = next(s for s in LIBRARY_UI_INDICATORS if "left_panel_close" in s)
    page = _page_with_present({lib})
    hit = await VideoGenerationMixin._detect_non_classic_cohort(page)
    assert hit == lib


@pytest.mark.asyncio
async def test_detects_agentic_marker() -> None:
    agentic = next(s for s in NON_CLASSIC_COHORT_INDICATORS if "apps_spark_2" in s)
    page = _page_with_present({agentic})
    hit = await VideoGenerationMixin._detect_non_classic_cohort(page)
    assert hit == agentic


@pytest.mark.asyncio
async def test_returns_none_when_no_cohort_marker() -> None:
    """Genuine selector drift (classic composer just renamed something) — no
    agentic/library marker → None, so the caller keeps UiSelectorDriftError."""
    page = _page_with_present(set())
    hit = await VideoGenerationMixin._detect_non_classic_cohort(page)
    assert hit is None


@pytest.mark.asyncio
async def test_swallows_locator_errors() -> None:
    page = MagicMock()
    page.locator = MagicMock(side_effect=RuntimeError("execution context destroyed"))
    hit = await VideoGenerationMixin._detect_non_classic_cohort(page)
    assert hit is None  # best-effort: a probe error never raises out of detection


# --- capture_ui_diagnostics ----------------------------------------------------


@pytest.mark.asyncio
async def test_capture_ui_diagnostics_writes_json_and_no_screenshot(tmp_path: Path) -> None:
    """Review fix: out_dir is the user's plain output directory on every
    ordinary run, so the legacy wrapper writes structural JSON only — the
    incident bundle owns the (full-page) screenshot under sensitive/."""
    page = MagicMock()
    page.evaluate = AsyncMock(
        return_value={
            "url": "https://labs.google/x",
            "ligatures": ["dashboard"],
            "cropPresent": False,
        }
    )
    page.screenshot = AsyncMock()

    out = await capture_ui_diagnostics(page, tmp_path, "diag_mode_switch_miss")

    assert out == tmp_path / "diag_mode_switch_miss.json"
    assert out is not None and out.exists()
    assert json.loads(out.read_text(encoding="utf-8"))["ligatures"] == ["dashboard"]
    page.screenshot.assert_not_awaited()
    assert not (tmp_path / "diag_mode_switch_miss.png").exists()


@pytest.mark.asyncio
async def test_capture_ui_diagnostics_none_without_out_dir() -> None:
    assert await capture_ui_diagnostics(MagicMock(), None, "x") is None


@pytest.mark.asyncio
async def test_capture_ui_diagnostics_uses_structural_engine_no_raw_text(tmp_path: Path) -> None:
    """§6.3 consolidation (S12): ONE DOM engine — the legacy wrapper now runs
    the diagnostics module's structural JS + allowlist validation, so raw
    url/title/body text can never reach the artifact."""
    canary = "SECRETCANARY-legacy"
    page = MagicMock()
    page.evaluate = AsyncMock(
        return_value={
            "url": f"https://labs.google/x?tok={canary}",
            "title": f"My private {canary} doc",
            "bodyTextPreview": f"prompt {canary}",
            "ligatures": ["dashboard"],
        }
    )
    page.screenshot = AsyncMock()

    out = await capture_ui_diagnostics(page, tmp_path, "diag_mode_switch_miss")

    assert out is not None
    blob = out.read_text(encoding="utf-8")
    assert canary not in blob
    assert "bodyTextPreview" not in blob
    assert json.loads(blob)["ligatures"] == ["dashboard"]


@pytest.mark.asyncio
async def test_capture_ui_diagnostics_survives_evaluate_error(tmp_path: Path) -> None:
    page = MagicMock()
    page.evaluate = AsyncMock(side_effect=RuntimeError("no execution context"))
    assert await capture_ui_diagnostics(page, tmp_path, "x") is None


# --- _mode_switch_error (shared image+video raise site; RETURNS the exception) --


@pytest.mark.asyncio
async def test_mode_switch_error_is_flow_agent_ui_error_on_cohort(tmp_path: Path) -> None:
    from gflow_cli.errors import FlowAgentUiError

    lib = next(s for s in LIBRARY_UI_INDICATORS if "left_panel_close" in s)
    page = _page_with_present({lib})
    page.evaluate = AsyncMock(return_value={"ligatures": [lib]})
    page.screenshot = AsyncMock()

    err = await VideoGenerationMixin._mode_switch_error(page, tmp_path, media="image")

    assert isinstance(err, FlowAgentUiError)
    msg = str(err)
    assert "media-library" in msg and "image generation" in msg  # media verb interpolated


@pytest.mark.asyncio
async def test_mode_switch_error_is_drift_error_when_no_cohort(tmp_path: Path) -> None:
    """#493: reaching the drift fall-through PROVES no known cohort indicator
    matched — the editor may be a brand-new Flow layout (e.g. the composer
    frame-slots + Agent-toggle variant). The detail must carry that hypothesis
    so the report that reaches the tracker points at the right cause."""
    from gflow_cli.errors import UiSelectorDriftError

    page = _page_with_present(set())  # genuine drift: no agentic/library marker
    page.evaluate = AsyncMock(return_value={"ligatures": []})
    page.screenshot = AsyncMock()

    err = await VideoGenerationMixin._mode_switch_error(page, tmp_path, media="video")

    assert isinstance(err, UiSelectorDriftError)
    msg = str(err)
    assert "mode_switch_trigger" in msg
    assert "does not recognize" in msg


@pytest.mark.asyncio
async def test_mode_switch_error_is_flow_app_error_on_app_crash(tmp_path: Path) -> None:
    from gflow_cli.errors import FlowAppError

    # Flow's React error boundary rendered (title), not the editor — a transient
    # Flow crash. Takes priority over cohort/drift classification.
    page = _page_with_present(set())
    page.title = AsyncMock(return_value="Application error: a client-side exception has occurred")
    page.evaluate = AsyncMock(return_value={"ligatures": []})
    page.screenshot = AsyncMock()

    err = await VideoGenerationMixin._mode_switch_error(page, tmp_path, media="image")

    assert isinstance(err, FlowAppError)
    assert "crashed" in str(err)


# --- #639: the flow.google.com migration branch of _mode_switch_error ----------


def _migrated_page(present: set[str] | None = None) -> MagicMock:
    """A page served by the migrated origin: zero ligatures, healthy render."""
    page = _page_with_present(present or set())
    page.url = "https://flow.google.com/project/9d0f4c22-0000-4000-8000-abcdef123456"
    page.evaluate = AsyncMock(return_value={"ligatures": []})
    page.screenshot = AsyncMock()
    return page


@pytest.mark.asyncio
async def test_migrated_host_yields_flow_host_migrated_error(tmp_path: Path) -> None:
    """The whole point of #639: on flow.google.com every ligature probe misses
    for a reason that is NOT selector rot, so the operator must not be told to
    file a selector bug."""
    from gflow_cli.errors import FlowHostMigratedError

    err = await VideoGenerationMixin._mode_switch_error(_migrated_page(), tmp_path, media="image")

    assert isinstance(err, FlowHostMigratedError)
    msg = str(err)
    assert "flow.google.com" in msg
    assert "image generation" in msg  # media verb interpolated, as the sibling arms do


@pytest.mark.asyncio
async def test_migrated_host_branch_is_symmetric_for_video(tmp_path: Path) -> None:
    from gflow_cli.errors import FlowHostMigratedError

    err = await VideoGenerationMixin._mode_switch_error(_migrated_page(), tmp_path, media="video")
    assert isinstance(err, FlowHostMigratedError)
    assert "video generation" in str(err)


@pytest.mark.asyncio
async def test_migrated_error_is_retryable_with_its_own_exit_code(tmp_path: Path) -> None:
    """The migration flaps per page load, so a re-navigation often lands the old
    host and succeeds — callers with automatic retry must not give up (#639)."""
    from gflow_cli.errors import EXIT_CODE_MAP, FlowHostMigratedError, is_retryable

    err = await VideoGenerationMixin._mode_switch_error(_migrated_page(), tmp_path, media="image")
    assert is_retryable(err)
    assert EXIT_CODE_MAP[FlowHostMigratedError] == 36


@pytest.mark.asyncio
async def test_flow_app_crash_still_wins_over_migrated_host(tmp_path: Path) -> None:
    """Ordering: a crashed app is the more specific diagnosis — the editor never
    rendered at all, so which origin served it is not the actionable fact."""
    from gflow_cli.errors import FlowAppError

    page = _migrated_page()
    page.title = AsyncMock(return_value="Application error: a client-side exception")

    err = await VideoGenerationMixin._mode_switch_error(page, tmp_path, media="image")
    assert isinstance(err, FlowAppError)


@pytest.mark.asyncio
async def test_migrated_host_wins_over_cohort_indicator(tmp_path: Path) -> None:
    """Ordering: if a cohort ligature somehow matches on the migrated origin,
    the host is still the more useful fact — the #174/#183 agentic remediation
    does not apply to a different frontend."""
    from gflow_cli.errors import FlowHostMigratedError

    lib = next(s for s in LIBRARY_UI_INDICATORS if "left_panel_close" in s)
    err = await VideoGenerationMixin._mode_switch_error(
        _migrated_page({lib}), tmp_path, media="image"
    )
    assert isinstance(err, FlowHostMigratedError)


@pytest.mark.asyncio
async def test_old_host_drift_is_unchanged_and_not_retryable(tmp_path: Path) -> None:
    """No regression: genuine selector rot on labs.google must still be exit 23
    and must still NOT be retryable — retrying real drift loops forever."""
    from gflow_cli.errors import UiSelectorDriftError, is_retryable

    page = _page_with_present(set())
    page.url = "https://labs.google/fx/tools/flow/project/abc-123"
    page.evaluate = AsyncMock(return_value={"ligatures": []})
    page.screenshot = AsyncMock()

    err = await VideoGenerationMixin._mode_switch_error(page, tmp_path, media="video")
    assert isinstance(err, UiSelectorDriftError)
    assert not is_retryable(err)


@pytest.mark.asyncio
async def test_unreadable_page_url_falls_through_to_drift(tmp_path: Path) -> None:
    """Defensive: a page whose .url is not a usable string (closed context,
    test double) must not crash the diagnosis — it degrades to the old verdict."""
    from gflow_cli.errors import UiSelectorDriftError

    page = _page_with_present(set())  # MagicMock .url — never assigned a string
    page.evaluate = AsyncMock(return_value={"ligatures": []})
    page.screenshot = AsyncMock()

    err = await VideoGenerationMixin._mode_switch_error(page, tmp_path, media="image")
    assert isinstance(err, UiSelectorDriftError)
