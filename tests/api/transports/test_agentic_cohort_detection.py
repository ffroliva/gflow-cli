"""Unit tests for the #183 media-library/agentic cohort raise-site handling.

`_detect_non_classic_cohort` scans the union of agentic + full-page media-library
markers so the shared `_fail_mode_switch` raise site can emit a clean, retryable
`FlowAgentUiError` instead of the misleading `UiSelectorDriftError`.
`capture_ui_diagnostics` is the debug-engine DOM+screenshot dump.
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
async def test_capture_ui_diagnostics_writes_json_and_screenshot(tmp_path: Path) -> None:
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
    page.screenshot.assert_awaited_once()
    assert page.screenshot.await_args.kwargs["full_page"] is True  # the black-shot fix


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
    from gflow_cli.errors import UiSelectorDriftError

    page = _page_with_present(set())  # genuine drift: no agentic/library marker
    page.evaluate = AsyncMock(return_value={"ligatures": []})
    page.screenshot = AsyncMock()

    err = await VideoGenerationMixin._mode_switch_error(page, tmp_path, media="video")

    assert isinstance(err, UiSelectorDriftError)


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
