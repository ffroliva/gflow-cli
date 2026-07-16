# Agentic Image Count Enforcement — Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix confirmed reliability bugs in the issue #313 fix (PR #325, merged to `develop`) by reworking `AgenticFlowUiDriver._enforce_image_count_via_settings_panel` to reuse classic mode's already-robust, already-tested count-tab primitives instead of the fragile hand-rolled version that shipped — and add a live e2e regression test so this is verified end-to-end, not just against mocks.

**Why this plan exists:** An xhigh-effort code review (10 finder angles + verification + gap sweep) and a ponytail (YAGNI/simplicity) review of PR #325 both independently found the same root cause: `src/gflow_cli/api/transports/ui_automation.py` already has `_set_count`/`_count_tabs_locator`/`_COUNT_TAB_TEXT_RE` — classic mode's count-tab setter — which already solves every reliability problem the review found in the new agentic-mode code: it waits for React re-renders, short-circuits when the count already matches, and does read-back verification with retries. The shipped fix reinvented a weaker version from scratch (no waits, no short-circuit, no verification) instead of reusing it.

Live investigation (2026-07-16, this session) confirmed Flow's Agent-mode settings panel uses the **same `role="tab"`/`aria-selected` Radix pattern** as classic mode's count popover — `_count_tabs_locator`/`_COUNT_TAB_TEXT_RE` work unmodified against it. Confirmed via `page.evaluate` against a real Flow project: 8 `[role="tab"]` elements match the count-tab text pattern when the Agent panel is open (4 for the "Image generation default" section, 4 for "Video generation default"), with the image section's 4 tabs always appearing first in DOM order — the same ordering assumption the shipped fix already relied on, now confirmed to hold for the reused primitive too.

**Real, confirmed structural differences from classic mode that prevent calling `_set_count` unmodified:**
1. Classic mode's popover applies a tab click **live** (no persist step). The Agent panel requires an explicit **Save** click to persist — confirmed live: `aria-selected` flips immediately on click, but reopening the panel without clicking Save reverts to the prior selection.
2. Classic mode's `_set_count` **raises `RuntimeError`** on failure to converge — there's no fallback for classic, so a hard failure is correct there. The agentic driver's natural-language directive is a genuine fallback, so this method must stay **best-effort and never raise** (matches the existing method's contract, which the shipped version violated by leaving the panel open on failure paths and thereby causing `send_prompt` to hard-fail downstream).
3. Classic mode opens its popover via the `crop_*` trigger (`_open_gen_settings_panel`, tries `GEN_SETTINGS_BUTTON_SELECTORS = MODE_SWITCH_TRIGGER_SELECTORS`). The Agent panel opens via the `tune` icon (`AGENT_TUNE_INDICATOR_SELECTOR`, canonical in `factory.py`) — a different trigger entirely.

**Architecture:** Reuse `_count_tabs_locator`, `_COUNT_TAB_TEXT_RE` (module-level, `ui_automation.py`) and `UiAutomationTransport._is_settings_panel_open` (static method, same file) via late import (matches this module's existing circular-import discipline — `agentic.py` must never import `ui_automation`/`ui_automation_video`/`factory` at module load time). Add Agent-specific glue: open via `tune` (canonical selector from `factory.py`, not a local redefinition — this also fixes the shipped version's `test_selector_symmetry.py`-violating duplicate), a scoped (not page-wide-ambiguous) read-back check on the target tab itself, an explicit Save step, and — the fix for the most severe confirmed bug — **always close the panel via its `arrow_back` header button before returning on any failure/short-circuit path**, so `send_prompt`'s composer click is never blocked by a panel left open.

**Tech Stack:** Python 3.11+, Playwright async API, pytest + pytest-asyncio, existing `AgenticFlowUiDriver` mock-page test conventions, live e2e via `tests/e2e/` (credit-free — image generation spends no Flow credits).

## Global Constraints

- The method must remain **best-effort and never raise** — any failure degrades to the pre-existing natural-language-only behavior, never a hard failure of the generation.
- The method must **always leave the settings panel closed / composer visible** by the time it returns, on every code path (success, short-circuit, or failure) — this is the direct fix for the most severe bug found in review.
- Reuse `AGENT_TUNE_INDICATOR_SELECTOR` from `factory.py` (canonical, `is`-identity-tested by `tests/api/transports/test_selector_symmetry.py`) — do not redefine an equal-looking local copy.
- Reuse `_count_tabs_locator`/`_COUNT_TAB_TEXT_RE` from `ui_automation.py` — do not redefine a local `_IMAGE_COUNT_TABLIST_SELECTOR`.
- All new/changed selectors are locale-invariant (Material Symbol ligatures + structural DOM position, never UI text, never hashed CSS-module class names as the sole anchor).
- Count is validated to 1–4 by `GenerateImageRequest.__post_init__` (`src/gflow_cli/api/image.py:443-444`) — the 4-tab range covers it fully, no clamping needed.
- Scope stays count-only — no aspect/model automation via this panel.

---

### Task 1: Rework `_enforce_image_count_via_settings_panel` to reuse classic mode's count-tab primitives

**Files:**
- Modify: `src/gflow_cli/api/transports/drivers/agentic.py`
- Modify: `tests/api/transports/drivers/test_agentic.py`

**Interfaces:**
- Consumes: `_count_tabs_locator(page: Page) -> Locator` and `_COUNT_TAB_TEXT_RE` (module-level, `gflow_cli.api.transports.ui_automation`); `UiAutomationTransport._is_settings_panel_open(page: Page) -> bool` (static method, same module); `AGENT_TUNE_INDICATOR_SELECTOR: str` (module-level, `gflow_cli.api.transports.drivers.factory`).
- Produces: `AgenticFlowUiDriver._enforce_image_count_via_settings_panel(page: Page, count: int) -> None` — same signature as before, replaces the existing implementation entirely.

- [ ] **Step 1: Read the current state of the file before editing**

Read `src/gflow_cli/api/transports/drivers/agentic.py` in full (it's ~700 lines) so the replacement lands consistent with the module's existing style (structlog event names prefixed `agentic_driver.`, late-import discipline per the module docstring, `# noqa: PLC0415` on late imports).

- [ ] **Step 2: Replace the module-level selector constants**

Replace the existing block (currently lines ~62-120: `_TUNE_BUTTON_SELECTOR`, `_IMAGE_COUNT_TABLIST_SELECTOR`, `_COUNT_BUTTON_TEXT`, `_FIND_SAVE_BUTTON_JS`) with:

```python
# Agent settings panel (issue #313, reworked 2026-07-16 after code review) —
# driven as a best-effort FALLBACK only, because prompt-encoding is the
# primary mechanism and this popover is the most drift-prone surface on
# Flow's UI (docs/AGENT_UI_RECON.md § "Settings via prompt, not the tune
# popover"). A sticky prior value in this panel can silently override the
# natural-language directive's requested count — that mismatch is issue
# #313's root cause.
#
# Reuses classic mode's count-tab primitives (_count_tabs_locator,
# _COUNT_TAB_TEXT_RE, UiAutomationTransport._is_settings_panel_open) via late
# import — live-verified 2026-07-16 that Flow's Agent settings panel uses the
# SAME role="tab"/aria-selected Radix pattern as classic mode's count
# popover: 8 matching [role="tab"] elements appear when the panel is open (4
# for "Image generation default", 4 for "Video generation default"), with
# the image section's 4 tabs always rendering first in DOM order. The tune
# open-trigger, the explicit Save requirement (classic's popover applies
# live, this panel does not), and the never-raise contract (classic's
# _set_count raises on failure since it has no fallback; this method DOES
# have a fallback — the natural-language directive — so it must degrade
# instead) are the genuine differences that prevent calling _set_count
# directly.

# The panel's "Save" button has no icon ligature, no data-* attribute, no
# type="submit", and its text ("Salvar"/"Save"/etc.) is locale-dependent —
# per memory flow-locale-leak-icon-ligatures this cannot be text-matched.
# Locate it structurally instead: walk up from the panel's arrow_back header
# icon to the nearest ancestor that ALSO contains a count tablist (i.e. the
# panel root), then take the last visible <button> in that scope — that
# button is Save in every live-verified capture (2026-07-16). Tags the match
# with a data attribute so Playwright can locate it without re-running the
# walk in a second round-trip.
_FIND_SAVE_BUTTON_JS = """
() => {
  const backBtn = [...document.querySelectorAll('button')].find((b) => {
    const i = b.querySelector('i.google-symbols');
    return i && (i.textContent || '').trim() === 'arrow_back';
  });
  if (!backBtn) return false;
  let node = backBtn.parentElement;
  for (let i = 0; i < 8 && node; i++) {
    const hasCountTablist = [...node.querySelectorAll("[role='tab']")].some((t) =>
      /^(1x|x[2-4])$/.test((t.textContent || '').trim())
    );
    if (hasCountTablist) {
      const visible = [...node.querySelectorAll('button')].filter((b) => {
        const r = b.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      });
      const save = visible[visible.length - 1];
      if (!save) return false;
      save.setAttribute('data-gflow-save-target', '1');
      return true;
    }
    node = node.parentElement;
  }
  return false;
}
"""

# The panel's own header "back" arrow — the verified way to abandon/close the
# panel WITHOUT saving (user-confirmed live: "the back arrow returns to the
# main area"). Used to guarantee the panel is never left open on a
# failure/short-circuit path, which is what caused send_prompt's composer
# click to hang in the version this replaces.
_ARROW_BACK_SELECTOR = "button:has(i.google-symbols:text-is('arrow_back'))"
```

- [ ] **Step 2 verify: syntax-only check**

Run: `.venv/Scripts/python.exe -m py_compile src/gflow_cli/api/transports/drivers/agentic.py`
Expected: no output (clean compile) — the file isn't fully valid yet since the method body hasn't been replaced, but this step just confirms the constants block itself has no syntax errors. Skip straight to Step 3 if this fails only because of the (expected, not-yet-fixed) method body below.

- [ ] **Step 3: Replace `_enforce_image_count_via_settings_panel`**

Replace the entire existing method (currently the whole block from `async def _enforce_image_count_via_settings_panel` through its closing `except Exception` block) with:

```python
    async def _enforce_image_count_via_settings_panel(self, page: Page, count: int) -> None:
        """Best-effort: set Agent mode's sticky "Image generation default"
        count to match ``count`` via the ``tune`` Settings panel.

        Fallback mechanism for issue #313 — see the module-level selector
        comments for why this reuses classic mode's count-tab primitives and
        why it cannot simply call classic's ``_set_count`` unmodified.

        **Never raises, and always leaves the panel closed / composer
        visible before returning** — on every path (success, short-circuit,
        or failure), so ``send_prompt``'s composer click is never blocked by
        a panel this method left open. Any selector miss (older UI cohort
        with no ``tune`` panel at all, a future Flow redesign, a transient
        render delay) degrades to the natural-language directive alone —
        the pre-existing behavior — rather than a hard failure.
        """
        # Late imports — agentic.py must not import ui_automation or
        # drivers.factory at module load time (circular: factory imports
        # this module to build AgenticFlowUiDriver).
        from gflow_cli.api.transports.drivers.factory import (  # noqa: PLC0415
            AGENT_TUNE_INDICATOR_SELECTOR,
        )
        from gflow_cli.api.transports.ui_automation import (  # noqa: PLC0415
            UiAutomationTransport,
            _count_tabs_locator,
        )

        try:
            already_open = await UiAutomationTransport._is_settings_panel_open(page)
            if not already_open:
                tune_btn = page.locator(f"button:has({AGENT_TUNE_INDICATOR_SELECTOR})").first
                if await tune_btn.count() == 0:
                    log.debug("agentic_driver.settings_panel.tune_not_found")
                    return
                await tune_btn.click(timeout=5_000)
                # Allow the panel to render — matches classic mode's
                # _open_gen_settings_panel wait, needed for the same reason
                # (React re-render lag; Locator.count() does not auto-wait).
                await page.wait_for_timeout(600)

            tabs = _count_tabs_locator(page)
            total = await tabs.count()
            if total < count:
                log.warning(
                    "agentic_driver.settings_panel.count_button_not_found",
                    count=count,
                    tabs_found=total,
                )
                await self._close_agent_settings_panel(page)
                return

            # Image section's tabs render first in DOM order (live-verified
            # 2026-07-16) — nth(count - 1) targets the image count tab, not
            # the video section's tabs that follow it.
            target_tab = tabs.nth(count - 1)

            if await target_tab.get_attribute("aria-selected") == "true":
                # Already correct — the common case. No click, no Save
                # needed; just restore the composer.
                log.debug("agentic_driver.settings_panel.count_already_correct", count=count)
                await self._close_agent_settings_panel(page)
                return

            _max_attempts = 3
            converged = False
            for attempt in range(1, _max_attempts + 1):
                await target_tab.click(timeout=5_000)
                await page.wait_for_timeout(300)
                if await target_tab.get_attribute("aria-selected") == "true":
                    converged = True
                    break
                if attempt < _max_attempts:
                    # Brief pause before retry to allow React re-render —
                    # same rationale as classic mode's _set_count.
                    await page.wait_for_timeout(500)

            if not converged:
                log.warning(
                    "agentic_driver.settings_panel.count_not_converged",
                    count=count,
                    attempts=_max_attempts,
                )
                await self._close_agent_settings_panel(page)
                return

            found_save = await page.evaluate(_FIND_SAVE_BUTTON_JS)
            if not found_save:
                log.warning("agentic_driver.settings_panel.save_button_not_found")
                await self._close_agent_settings_panel(page)
                return

            # Save auto-closes the panel back to the composer (live-verified
            # 2026-07-16) — no further close step needed on this path.
            await page.locator("[data-gflow-save-target='1']").first.click(timeout=5_000)
            await page.wait_for_timeout(300)
            log.debug("agentic_driver.settings_panel.count_enforced", count=count)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "agentic_driver.settings_panel.enforce_count_failed",
                error=str(e)[:120],
            )
            await self._close_agent_settings_panel(page)

    @staticmethod
    async def _close_agent_settings_panel(page: Page) -> None:
        """Best-effort: click the panel's back arrow to abandon it WITHOUT
        saving, restoring the composer. Never raises — this is itself a
        failure-path cleanup step, so any error here is logged and
        swallowed rather than propagated.

        Safe to call even if the panel is already closed (the back-arrow
        locator simply won't match, `.count() == 0` short-circuits).
        """
        try:
            back_btn = page.locator(_ARROW_BACK_SELECTOR).first
            if await back_btn.count() == 0:
                return
            await back_btn.click(timeout=3_000)
            await page.wait_for_timeout(300)
        except Exception as e:  # noqa: BLE001
            log.debug("agentic_driver.settings_panel.close_failed", error=str(e)[:120])
```

Note: the panel is closed unconditionally on every non-success return path regardless of whether THIS call opened it (vs. it already being open on entry) — simpler than tracking an `opened_here` flag, and correct either way since `_close_agent_settings_panel` is itself a no-op when nothing is open.

- [ ] **Step 4: Update the module-level docstring**

The module's top-of-file docstring (lines 1-20) currently states:

```
- Settings are encoded in the prompt directive, not driven via the ``tune``
  Radix-popover (avoids the most drift-prone surface on a volatile A/B UI).
```

Replace with:

```
- Settings are encoded in the prompt directive AS THE PRIMARY mechanism; the
  ``tune`` Radix-popover is driven as a best-effort FALLBACK for image count
  only (issue #313 — a stale sticky default there can silently override the
  natural-language directive). See ``_enforce_image_count_via_settings_panel``.
```

- [ ] **Step 5: Update `tests/api/transports/drivers/test_agentic.py`'s imports**

Find the existing import block:

```python
from gflow_cli.api.transports.drivers.agentic import (
    _MEDIA_REDIRECT_BASE,
    AgenticFlowUiDriver,
    _extract_uuids,
)
```

Leave it as-is — the rework no longer exposes `_IMAGE_COUNT_TABLIST_SELECTOR`/`_TUNE_BUTTON_SELECTOR`/`_COUNT_BUTTON_TEXT` as module constants tests need to import directly (they're gone; the tune selector is now built inline from the late-imported `AGENT_TUNE_INDICATOR_SELECTOR`). Tests instead mock `page.locator` generically as shown below.

- [ ] **Step 6: Replace the `_enforce_image_count_via_settings_panel` unit tests**

Find and delete the entire test section from the previous implementation: the `_mock_page_with_settings_panel` helper and the 6 tests `test_enforce_count_clicks_matching_button_and_saves` through `test_enforce_count_swallows_exceptions_and_never_raises` (the section header comment `# _enforce_image_count_via_settings_panel — issue #313 fallback`).

Replace with:

```python
# ---------------------------------------------------------------------------
# _enforce_image_count_via_settings_panel — issue #313 fallback (reworked)
# ---------------------------------------------------------------------------


def _mock_settings_panel_page(
    *,
    panel_already_open: bool = False,
    tune_button_present: bool = True,
    tab_count: int = 8,
    target_initially_selected: bool = False,
    converges_on_click: bool = True,
    save_button_found: bool = True,
) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
    """Build a page mock for the reworked enforcement method.

    Returns (page, tune_btn, target_tab, back_btn) so tests can assert on
    click() call counts for specific elements. Patches
    ``UiAutomationTransport._is_settings_panel_open`` and
    ``_count_tabs_locator`` via ``unittest.mock.patch`` in each test (they
    are imported inside the method under test via a late import, so the
    patch target is the real module, not the local re-export).
    """
    tune_btn = MagicMock()
    tune_btn.count = AsyncMock(return_value=1 if tune_button_present else 0)
    tune_btn.click = AsyncMock()

    selected_state = {"value": target_initially_selected}

    async def _get_attribute(name: str) -> str | None:
        if name != "aria-selected":
            return None
        return "true" if selected_state["value"] else "false"

    async def _click_target(**_kwargs: object) -> None:
        if converges_on_click:
            selected_state["value"] = True

    target_tab = MagicMock()
    target_tab.get_attribute = AsyncMock(side_effect=_get_attribute)
    target_tab.click = AsyncMock(side_effect=_click_target)

    tabs = MagicMock()
    tabs.count = AsyncMock(return_value=tab_count)
    tabs.nth = MagicMock(return_value=target_tab)

    back_btn = MagicMock()
    back_btn.count = AsyncMock(return_value=1)
    back_btn.click = AsyncMock()

    save_btn = MagicMock()
    save_btn.click = AsyncMock()

    def _locator(selector: str) -> MagicMock:
        result = MagicMock()
        if "arrow_back" in selector:
            result.first = back_btn
        elif "data-gflow-save-target" in selector:
            result.first = save_btn
        elif "tune" in selector:
            result.first = tune_btn
        else:
            result.first = MagicMock()
        return result

    page = MagicMock()
    page.locator = MagicMock(side_effect=_locator)
    page.evaluate = AsyncMock(return_value=save_button_found)

    return page, tune_btn, target_tab, back_btn, tabs


@pytest.mark.asyncio
async def test_enforce_count_opens_panel_clicks_and_saves() -> None:
    driver = AgenticFlowUiDriver()
    page, tune_btn, target_tab, back_btn, tabs = _mock_settings_panel_page(
        panel_already_open=False,
        target_initially_selected=False,
    )
    with (
        patch(
            "gflow_cli.api.transports.ui_automation.UiAutomationTransport._is_settings_panel_open",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "gflow_cli.api.transports.ui_automation._count_tabs_locator",
            return_value=tabs,
        ),
    ):
        await driver._enforce_image_count_via_settings_panel(page, 3)  # noqa: SLF001
    tune_btn.click.assert_awaited_once()
    target_tab.click.assert_awaited_once()
    page.evaluate.assert_awaited_once()
    back_btn.click.assert_not_awaited()  # Save auto-closes; no separate back-arrow click needed


@pytest.mark.asyncio
async def test_enforce_count_skips_click_and_save_when_already_correct() -> None:
    driver = AgenticFlowUiDriver()
    page, tune_btn, target_tab, back_btn, tabs = _mock_settings_panel_page(
        target_initially_selected=True,
    )
    with (
        patch(
            "gflow_cli.api.transports.ui_automation.UiAutomationTransport._is_settings_panel_open",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "gflow_cli.api.transports.ui_automation._count_tabs_locator",
            return_value=tabs,
        ),
    ):
        await driver._enforce_image_count_via_settings_panel(page, 1)  # noqa: SLF001
    target_tab.click.assert_not_awaited()
    page.evaluate.assert_not_awaited()  # no Save needed
    back_btn.click.assert_awaited_once()  # but the panel we opened must still be closed


@pytest.mark.asyncio
async def test_enforce_count_does_not_reopen_already_open_panel() -> None:
    driver = AgenticFlowUiDriver()
    page, tune_btn, target_tab, back_btn, tabs = _mock_settings_panel_page(
        target_initially_selected=True,
    )
    with (
        patch(
            "gflow_cli.api.transports.ui_automation.UiAutomationTransport._is_settings_panel_open",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "gflow_cli.api.transports.ui_automation._count_tabs_locator",
            return_value=tabs,
        ),
    ):
        await driver._enforce_image_count_via_settings_panel(page, 1)  # noqa: SLF001
    tune_btn.click.assert_not_awaited()  # already open — must not toggle it closed


@pytest.mark.asyncio
async def test_enforce_count_closes_panel_on_target_not_found() -> None:
    driver = AgenticFlowUiDriver()
    page, tune_btn, target_tab, back_btn, tabs = _mock_settings_panel_page(tab_count=2)
    with (
        patch(
            "gflow_cli.api.transports.ui_automation.UiAutomationTransport._is_settings_panel_open",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "gflow_cli.api.transports.ui_automation._count_tabs_locator",
            return_value=tabs,
        ),
    ):
        await driver._enforce_image_count_via_settings_panel(page, 4)  # noqa: SLF001
    back_btn.click.assert_awaited_once()  # panel opened, target missing — must still close


@pytest.mark.asyncio
async def test_enforce_count_closes_panel_when_click_never_converges() -> None:
    driver = AgenticFlowUiDriver()
    page, tune_btn, target_tab, back_btn, tabs = _mock_settings_panel_page(
        converges_on_click=False,
    )
    with (
        patch(
            "gflow_cli.api.transports.ui_automation.UiAutomationTransport._is_settings_panel_open",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "gflow_cli.api.transports.ui_automation._count_tabs_locator",
            return_value=tabs,
        ),
    ):
        await driver._enforce_image_count_via_settings_panel(page, 2)  # noqa: SLF001
    assert target_tab.click.await_count == 3  # noqa: PLR2004  # exhausted all 3 attempts
    back_btn.click.assert_awaited_once()


@pytest.mark.asyncio
async def test_enforce_count_closes_panel_when_save_not_found() -> None:
    driver = AgenticFlowUiDriver()
    page, tune_btn, target_tab, back_btn, tabs = _mock_settings_panel_page(
        save_button_found=False,
    )
    with (
        patch(
            "gflow_cli.api.transports.ui_automation.UiAutomationTransport._is_settings_panel_open",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "gflow_cli.api.transports.ui_automation._count_tabs_locator",
            return_value=tabs,
        ),
    ):
        await driver._enforce_image_count_via_settings_panel(page, 2)  # noqa: SLF001
    target_tab.click.assert_awaited_once()
    back_btn.click.assert_awaited_once()


@pytest.mark.asyncio
async def test_enforce_count_skips_gracefully_when_tune_button_absent() -> None:
    driver = AgenticFlowUiDriver()
    page, tune_btn, target_tab, back_btn, tabs = _mock_settings_panel_page(
        tune_button_present=False,
    )
    with patch(
        "gflow_cli.api.transports.ui_automation.UiAutomationTransport._is_settings_panel_open",
        new=AsyncMock(return_value=False),
    ):
        await driver._enforce_image_count_via_settings_panel(page, 1)  # noqa: SLF001
    # No exception — graceful skip before the panel was ever touched.
    back_btn.click.assert_not_awaited()


@pytest.mark.asyncio
async def test_enforce_count_swallows_exceptions_and_still_closes_panel() -> None:
    driver = AgenticFlowUiDriver()
    page, tune_btn, target_tab, back_btn, tabs = _mock_settings_panel_page()
    tabs.count = AsyncMock(side_effect=RuntimeError("boom"))
    with (
        patch(
            "gflow_cli.api.transports.ui_automation.UiAutomationTransport._is_settings_panel_open",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "gflow_cli.api.transports.ui_automation._count_tabs_locator",
            return_value=tabs,
        ),
    ):
        # Must not raise.
        await driver._enforce_image_count_via_settings_panel(page, 1)  # noqa: SLF001
    back_btn.click.assert_awaited_once()  # even the exception path closes the panel


@pytest.mark.asyncio
async def test_close_agent_settings_panel_is_noop_when_already_closed() -> None:
    page = MagicMock()
    absent = MagicMock()
    absent.count = AsyncMock(return_value=0)
    page.locator = MagicMock(return_value=absent)
    await AgenticFlowUiDriver._close_agent_settings_panel(page)  # noqa: SLF001
    # No exception, no click attempted on a nonexistent element.


@pytest.mark.asyncio
async def test_close_agent_settings_panel_swallows_click_errors() -> None:
    page = MagicMock()
    back_btn = MagicMock()
    back_btn.count = AsyncMock(return_value=1)
    back_btn.click = AsyncMock(side_effect=RuntimeError("boom"))
    result = MagicMock()
    result.first = back_btn
    page.locator = MagicMock(return_value=result)
    # Must not raise.
    await AgenticFlowUiDriver._close_agent_settings_panel(page)  # noqa: SLF001
```

- [ ] **Step 7: Fix the 3 `configure_image_settings` tests and the instructions test that broke under the ORIGINAL (now-replaced) implementation**

These were already fixed by the previous PR (#325) using a `_mock_page_no_settings_panel()` helper that returns `count()==0` for every locator. That helper and its usages remain correct for the reworked method too (the tune-button-absent path is unchanged in shape — still `page.locator(...).count() == 0` triggers a graceful skip), **except** the reworked method also calls `UiAutomationTransport._is_settings_panel_open(page)` FIRST, before ever touching `page.locator` for the tune button. `_mock_page_no_settings_panel()`'s bare `MagicMock()` page, passed through `_is_settings_panel_open` (which internally calls `_count_tabs_locator(page).first.is_visible()`), will raise a `TypeError` on the unconfigured mock's non-awaitable return — but that's caught by this method's own outer `except Exception`, so the 4 existing tests (`test_configure_image_settings_stores_count_and_aspect`, `test_configure_image_settings_portrait_aspect`, `test_configure_image_settings_square_aspect`, `test_reconcile_instructions_no_op_when_none`) should continue to pass UNCHANGED — verify this by running them (Step 8), do not edit them speculatively.

- [ ] **Step 8: Run the full test file**

Run: `.venv/Scripts/python.exe -m pytest tests/api/transports/drivers/test_agentic.py -v`
Expected: all tests PASS, including the new/replaced tests from Step 6 and the untouched Step 7 tests.

- [ ] **Step 9: Run ruff + pyright**

Run: `.venv/Scripts/python.exe -m ruff check src/gflow_cli/api/transports/drivers/agentic.py tests/api/transports/drivers/test_agentic.py`
Run: `.venv/Scripts/python.exe -m ruff format --check src/gflow_cli/api/transports/drivers/agentic.py tests/api/transports/drivers/test_agentic.py`
Run: `.venv/Scripts/python.exe -m pyright src`
Expected: all clean. Fix any issues before committing.

- [ ] **Step 10: Run the selector symmetry test**

Run: `.venv/Scripts/python.exe -m pytest tests/api/transports/test_selector_symmetry.py -v`
Expected: all 4 tests PASS — this confirms the rework's use of `AGENT_TUNE_INDICATOR_SELECTOR` (imported, not redefined) doesn't violate the existing canonical-selector convention.

- [ ] **Step 11: Commit**

```bash
git add src/gflow_cli/api/transports/drivers/agentic.py tests/api/transports/drivers/test_agentic.py
git commit -m "fix(agentic): reuse classic mode's count-tab primitives for #313 fallback

An xhigh-effort code review + a ponytail simplicity review of the original
fix (PR #325) both found the same root cause: ui_automation.py already has
_set_count/_count_tabs_locator, a more robust count-tab setter (waits for
React re-render, short-circuits when already correct, read-back verifies
with retry) that the original fix reinvented weaker from scratch.

This reworks _enforce_image_count_via_settings_panel to reuse those
primitives (live-verified 2026-07-16: Flow's Agent settings panel uses the
same role=tab/aria-selected pattern), while keeping the genuine differences
Agent mode needs: opens via tune (not crop_*), requires an explicit Save
click classic mode's live-apply popover doesn't need, and stays best-effort/
never-raising since the natural-language directive is its fallback (unlike
classic's _set_count, which correctly raises since it has none).

Also fixes the most severe bug the review found: every code path now
explicitly closes the panel (via its arrow_back button) before returning,
so a failure here can never leave the panel open blocking send_prompt's
composer click — the original version's panel-left-open bug could turn a
previously-working generation into a hard failure."
```

---

### Task 2: Add a live e2e regression test for count enforcement

**Files:**
- Create: `tests/e2e/test_agentic_count_enforcement_e2e.py`

**Interfaces:**
- Consumes: `gflow_cli.api.client.FlowApiClient`, `gflow_cli.api.image.GenerateImageRequest`, existing e2e fixtures/conventions from `tests/e2e/conftest.py` and `tests/e2e/test_live_agentic_instructions.py` (read both before writing — this task's test must match their fixture/marker conventions exactly, not invent new ones).

**Why this belongs in the suite, not just a manual script:** issue #313 was originally caused by a sticky UI default silently diverging from the requested count — a bug class that only a REAL browser against REAL Flow can catch (mocks can't represent Flow's own persisted state). This test is the durable regression guard; the manual live verification done during PR #325 and this rework was a one-off check, not a repeatable one.

- [ ] **Step 1: Read the existing live agentic e2e test and conftest for conventions**

Read `tests/e2e/test_live_agentic_instructions.py` in full and `tests/e2e/conftest.py` in full. Note: the profile-resolution fixture name, the pytest markers used (`@pytest.mark.e2e`, `@pytest.mark.e2e_image`, `@pytest.mark.asyncio`), how the test obtains a `FlowApiClient`, and how it obtains/parametrizes a project id. Match these exactly — do not invent a parallel convention.

- [ ] **Step 2: Write the test**

Create `tests/e2e/test_agentic_count_enforcement_e2e.py` following the exact fixture/marker pattern found in Step 1. The test must, for each of `count in (1, 2, 3, 4)`:
1. Open the target project's Agent settings panel and set the "Image generation default" count to a DIFFERENT value than the one about to be requested (use the same tune-button-open + count-tab-click sequence pattern as the production `_enforce_image_count_via_settings_panel`, or call it directly with a mismatched value first if that's simpler — read how `test_live_agentic_instructions.py` structures its own live setup step before choosing).
2. Call `client.generate_image(GenerateImageRequest(prompt=..., count=<N>, ...))` (or whatever the actual public entry point is — grep `FlowApiClient` for the real method name, do not guess) against that same project.
3. Assert the response contains exactly `N` images and no `MediaAttributionError` was raised.

Use a distinct, identifiable prompt per iteration (e.g. include the count in the prompt text) so failures are easy to attribute to a specific count in CI logs. Since image generation is credit-free (per project convention — video is the only credit-spending path), this test can safely run all 4 counts in one pytest function or as 4 parametrized cases — prefer `@pytest.mark.parametrize("count", [1, 2, 3, 4])` for isolated pass/fail reporting per count.

- [ ] **Step 3: Run the new test live**

Run (with whatever env vars `test_live_agentic_instructions.py` requires, matching Step 1's findings — likely `GFLOW_CLI_E2E_PROFILE` and `-m e2e`):
`.venv/Scripts/python.exe -m pytest tests/e2e/test_agentic_count_enforcement_e2e.py -v -m e2e`
Expected: all 4 parametrized cases PASS against the real Flow account.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_agentic_count_enforcement_e2e.py
git commit -m "test(e2e): add live regression test for agentic count enforcement (#313)

Sets a deliberately mismatched sticky count default via Flow's Agent
settings panel, then requests each of counts 1-4 through the real agentic
generation path and asserts the correct count comes back with no
MediaAttributionError. Credit-free (image generation, not video)."
```

---

### Task 3: Fix the unrelated `.pre-commit-config.yaml` stage-inheritance bug

**Files:**
- Modify: `.pre-commit-config.yaml`

This is unrelated to the agentic count-enforcement rework (found by the same review pass, in the separately-merged SonarCloud outage resilience PR #324) — fixing it here since it's small and already diagnosed.

**Problem:** `ruff`, `ruff-format`, `repo-hygiene`, and `detect-secrets` have no `stages:` key, so once a contributor follows this file's own header instructions (`pre-commit install --hook-type pre-push`), those hooks also run on every `git push` — contradicting the header comment's claim that only `sonar-outage-fallback` is "Gate enforced on push."

- [ ] **Step 1: Add `default_stages` to scope existing hooks to commit-time only**

Read the current file (`.pre-commit-config.yaml`) to confirm line numbers haven't shifted, then add a top-level `default_stages` key restricting the pre-existing hooks to the `pre-commit` stage, so only `sonar-outage-fallback`'s explicit `stages: [pre-push]` fires on push:

```yaml
default_stages: [pre-commit]

repos:
```

(Insert this immediately before the existing `repos:` line.) `pre-commit`'s `default_stages` sets the fallback for any hook that doesn't declare its own `stages:` — since `sonar-outage-fallback` already declares `stages: [pre-push]` explicitly, it's unaffected by this default and continues to run only on push.

- [ ] **Step 2: Verify the YAML is still valid and pre-commit can parse it**

Run: `.venv/Scripts/python.exe -c "import yaml; yaml.safe_load(open('.pre-commit-config.yaml'))" `
Expected: no error.

If `pre-commit` itself is installed in this environment, additionally run `pre-commit run --all-files` to confirm hook resolution still works; if it's not installed (as was the case earlier this session), the YAML-parse check above is sufficient.

- [ ] **Step 3: Commit**

```bash
git add .pre-commit-config.yaml
git commit -m "fix(hooks): scope existing hooks to commit-time only via default_stages

ruff/ruff-format/repo-hygiene/detect-secrets had no explicit stages: key,
so pre-commit's stage-inheritance rules made them also run on git push once
a contributor installed the pre-push hook type (as this file's own header
instructs) — contradicting the header's claim that only the Sonar hook is
gate-enforced on push. default_stages: [pre-commit] restores that."
```

---

## Post-implementation verification (controller, not a subagent task)

After Task 1 and Task 2 both land and their own tests pass:

1. Re-run the manual live verification from PR #325 (deliberately set a project's sticky count to a mismatched value, request a different count via `gflow image t2i`, confirm exactly the requested count comes back) — this time via the reworked code path, to confirm the rework didn't regress the original fix's live-verified behavior.
2. Confirm Task 2's new e2e test actually exercises the LIVE code path (not a mock) by checking it imports `FlowApiClient` and hits a real project id, not a fixture double.
3. Run `.venv/Scripts/python.exe -m pytest tests/api/transports/drivers/test_agentic.py tests/api/transports/test_selector_symmetry.py -v` one more time on the final merged state of all 3 tasks together, since Task 3 touches a config file the other two don't but all three land in the same PR.
