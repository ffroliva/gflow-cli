# Agentic Image Count Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop Flow's Agent-mode composer from silently overriding gflow-cli's requested image count, which currently causes issue #313 (the `MediaAttributionError` ambiguity guard fires *after* a full generation attempt, wasting the run).

**Architecture:** `AgenticFlowUiDriver.configure_image_settings` currently only encodes count/aspect into a natural-language prompt directive (`_compose_directive`). Live investigation (2026-07-16, issue #313 comment) found that Agent mode also has a `tune` "Agent settings" panel with a **sticky** "Image generation default" count control (`1x`/`x2`/`x3`/`x4`) that is never touched by gflow-cli — a stale value there can override the natural-language phrasing. This plan adds a best-effort step that drives that panel to match the requested count, keeping the natural-language phrasing as reinforcement and the existing `MediaAttributionError` fail-fast guard as a backstop.

This is the fallback path `docs/AGENT_UI_RECON.md` § "Settings via prompt, not the `tune` popover" already anticipated: *"treat popover automation as a fallback only if prompt-steering proves unreliable"* — issue #313 is that proof.

**Tech Stack:** Python 3.11+, Playwright async API, pytest + pytest-asyncio, existing `AgenticFlowUiDriver` mock-page test conventions.

## Global Constraints

- Selectors must be **locale-invariant**: Material Symbol ligatures + DOM structure only, never text content, never hashed CSS-module class names (`sc-xxxxxxx-N`) as the sole anchor — see memory `flow-locale-leak-icon-ligatures` and this repo's existing `MODE_SWITCH_TRIGGER_SELECTORS` pattern.
- The new behavior must be **best-effort and never raise**: if any selector step fails (panel not found, button not found, different UI cohort), log a warning and fall through to the existing natural-language-only behavior. This must never turn a working generation into a hard failure — the whole point is to *reduce* failures, not add a new failure mode.
- Count is already validated to the range 1–4 by `GenerateImageRequest.__post_init__` (`src/gflow_cli/api/image.py:443-444`) — the four settings-panel count buttons (`1x`/`x2`/`x3`/`x4`) cover the full range; no clamping/fallback-for-out-of-range logic is needed.
- All existing tests in `tests/api/transports/drivers/test_agentic.py` that call `configure_image_settings` with a `MagicMock()` page must keep passing — the new page-interaction code path must be safely no-op-able against a bare `MagicMock()` (a `MagicMock().locator(...).count()` returns a `MagicMock`, which is truthy in a boolean context in the WRONG way for `await ... > 0` comparisons unless tests are updated to configure the mock explicitly; the task must either update those 3 existing tests to configure the new mock behavior, or ensure the code path degrades safely and add `AsyncMock`-based assertions).
- Do not touch aspect-ratio or model automation via this panel — stay scoped to count only, matching issue #313 as filed.

---

### Task 1: Drive Agent settings panel's Image count control from `configure_image_settings`

**Files:**
- Modify: `src/gflow_cli/api/transports/drivers/agentic.py`
- Modify: `tests/api/transports/drivers/test_agentic.py`
- Modify: `docs/AGENT_UI_RECON.md` (mark the fallback as implemented)

**Interfaces:**
- Consumes: `GenerateImageRequest.count` (already validated 1–4, `src/gflow_cli/api/image.py:443-444`); `AgenticFlowUiDriver.configure_image_settings(page, request, *, out_dir=None, prompt_idx=None)` (existing signature, unchanged).
- Produces: `AgenticFlowUiDriver._enforce_image_count_via_settings_panel(page: Page, count: int) -> None` — new private async method, best-effort, never raises. Called from `configure_image_settings`.

- [ ] **Step 1: Read the current `configure_image_settings` and module header for context**

Read `src/gflow_cli/api/transports/drivers/agentic.py` lines 1-120 (module docstring + constants) and lines 222-260 (`configure_image_settings`) before editing, so the new constants and method land in the same style as the existing code (structlog `log.debug`/`log.warning` with snake_case event names prefixed `agentic_driver.`, late imports only where the module docstring requires them).

- [ ] **Step 2: Add the new module-level selector constants**

Add these near the existing `_SLATE_COMPOSER_SELECTOR` constant (around line 60), after it:

```python
# Agent settings panel (issue #313) — driven as a best-effort FALLBACK only,
# because prompt-encoding is the primary mechanism and this popover is the
# most drift-prone surface on Flow's UI (docs/AGENT_UI_RECON.md § "Settings
# via prompt, not the tune popover"). A sticky prior value in this panel can
# silently override the natural-language directive's requested count — that
# mismatch is issue #313's root cause. This enforcement makes the panel match
# the request so the natural-language phrasing and the UI setting agree.
_TUNE_BUTTON_SELECTOR = "button:has(i.google-symbols:text-is('tune'))"

# The image count control is the FIRST [role='tablist'] in DOM order that
# contains both '1x' and 'x2' buttons — Flow always renders the "Image
# generation default" section before "Video generation default" (verified
# live 2026-07-16, both en and pt-BR locales; count labels are numeric
# multipliers, not translated text, so this is locale-safe).
_IMAGE_COUNT_TABLIST_SELECTOR = (
    "[role='tablist']:has(button:text-is('1x')):has(button:text-is('x2'))"
)

# request.count is validated to 1-4 by GenerateImageRequest.__post_init__
# (api/image.py) — this covers the settings panel's full button range.
_COUNT_BUTTON_TEXT: dict[int, str] = {1: "1x", 2: "x2", 3: "x3", 4: "x4"}

# The panel's "Save" button has no icon ligature, no data-* attribute, no
# type="submit", and its text ("Salvar"/"Save"/etc.) is locale-dependent —
# per memory flow-locale-leak-icon-ligatures this cannot be text-matched.
# Locate it structurally instead: walk up from the panel's arrow_back header
# icon to the nearest ancestor that ALSO contains the count tablist (i.e.
# the panel root), then take the last visible <button> in that scope — that
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
    const hasCountTablist = [...node.querySelectorAll("[role='tablist']")].some((t) => {
      const texts = [...t.querySelectorAll('button')].map((b) => (b.textContent || '').trim());
      return texts.includes('1x') && texts.includes('x2');
    });
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
```

- [ ] **Step 2 verify: run ruff on the file so far**

Run: `.venv/Scripts/python.exe -m ruff check src/gflow_cli/api/transports/drivers/agentic.py`
Expected: `All checks passed!` (constants only, no logic yet — this just catches typos before the method lands)

- [ ] **Step 3: Add `_enforce_image_count_via_settings_panel` to `AgenticFlowUiDriver`**

Add this method immediately after `configure_image_settings` (after its closing, before `_reconcile_instructions` — or after `_reconcile_instructions`, whichever keeps `configure_image_settings` and its direct helpers adjacent; place it directly below `configure_image_settings`):

```python
    async def _enforce_image_count_via_settings_panel(self, page: Page, count: int) -> None:
        """Best-effort: set Agent mode's sticky "Image generation default"
        count to match ``count`` via the ``tune`` Settings panel.

        Fallback mechanism for issue #313 — see the module-level selector
        comments for why this is scoped to count-only and driven only as a
        secondary signal alongside the natural-language directive.

        **Never raises.** Any selector miss (older UI cohort with no ``tune``
        panel at all, a future Flow redesign, a transient render delay) is
        logged and swallowed — the natural-language directive alone is the
        pre-existing behavior, so failing to enforce here must never turn a
        previously-working generation into a hard failure.
        """
        try:
            tune_btn = page.locator(_TUNE_BUTTON_SELECTOR).first
            if await tune_btn.count() == 0:
                log.debug("agentic_driver.settings_panel.tune_not_found")
                return
            await tune_btn.click(timeout=5_000)

            count_tablist = page.locator(_IMAGE_COUNT_TABLIST_SELECTOR).first
            target_label = _COUNT_BUTTON_TEXT[count]
            target_btn = count_tablist.locator(f"button:text-is('{target_label}')").first
            if await target_btn.count() == 0:
                log.warning(
                    "agentic_driver.settings_panel.count_button_not_found",
                    count=count,
                )
                return
            await target_btn.click(timeout=5_000)

            found_save = await page.evaluate(_FIND_SAVE_BUTTON_JS)
            if not found_save:
                log.warning("agentic_driver.settings_panel.save_button_not_found")
                return
            await page.locator("[data-gflow-save-target='1']").first.click(timeout=5_000)
            log.debug("agentic_driver.settings_panel.count_enforced", count=count)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "agentic_driver.settings_panel.enforce_count_failed",
                error=str(e)[:120],
            )
```

- [ ] **Step 4: Call the new method from `configure_image_settings`**

In `configure_image_settings` (around line 222-251 currently), add the call after the existing instructions-reconciliation block. The method becomes:

```python
    async def configure_image_settings(  # NOSONAR
        self,
        page: Page,  # NOSONAR
        request: GenerateImageRequest,
        *,
        out_dir: Path | None = None,  # NOSONAR
        prompt_idx: int | None = None,
    ) -> None:
        """Encode settings on the instance for prompt-directive composition,
        and best-effort enforce the count via the Agent settings panel.

        Does NOT drive the ``tune`` popover for aspect/model — only count,
        as a fallback for issue #313 (a stale sticky default there can
        override the natural-language directive). See
        ``_enforce_image_count_via_settings_panel``.
        """
        # request.model / request.aspect are non-optional StrEnums — str() yields
        # the wire value ("NARWHAL", "IMAGE_ASPECT_RATIO_PORTRAIT", …).
        self._pending_count = request.count
        self._pending_model = str(request.model)
        self._pending_aspect = _ASPECT_PROMPT_LABEL.get(str(request.aspect))
        log.debug(
            "agentic_driver.configure_image_settings.stored",
            count=self._pending_count,
            aspect=self._pending_aspect,
            model=self._pending_model,
            prompt_idx=prompt_idx,
        )

        if request.instructions is not None:
            await self._reconcile_instructions(page, request.instructions)

        await self._enforce_image_count_via_settings_panel(page, request.count)
```

(Only the new final line and the docstring differ from the current implementation — the stored-value logic and instructions reconciliation call are unchanged.)

- [ ] **Step 5: Update the 3 existing `configure_image_settings` tests that pass a bare `MagicMock()` page**

These three existing tests in `tests/api/transports/drivers/test_agentic.py` currently pass `MagicMock()` or `page` (also a bare `MagicMock()`) as the page argument: `test_configure_image_settings_stores_count_and_aspect` (line 163), `test_configure_image_settings_portrait_aspect` (line 172), `test_configure_image_settings_square_aspect` (line 180). A bare `MagicMock()`'s `.locator(...).count()` returns a `MagicMock` (not an int), and `await mock_result` on a non-awaitable `MagicMock` raises `TypeError` — so these will now fail with `TypeError: object MagicMock can't be used in 'await' expression` once Step 4 lands, because `_enforce_image_count_via_settings_panel` calls `await tune_btn.count()`.

Add a shared helper at the top of the file (near `_make_image_request`, after it) and use it in all three tests:

```python
def _mock_page_no_settings_panel() -> MagicMock:
    """Page where the Agent settings panel's tune button is absent (count 0) —
    exercises the graceful-skip path so configure_image_settings tests don't
    need to model the full settings-panel click sequence."""
    page = MagicMock()
    locator_mock = MagicMock()
    locator_mock.first = locator_mock
    locator_mock.count = AsyncMock(return_value=0)
    page.locator = MagicMock(return_value=locator_mock)
    return page
```

Replace the page argument in all three tests:

```python
@pytest.mark.asyncio
async def test_configure_image_settings_stores_count_and_aspect() -> None:
    driver = AgenticFlowUiDriver()
    page = _mock_page_no_settings_panel()
    req = _make_image_request(count=4, aspect=Aspect.LANDSCAPE)
    await driver.configure_image_settings(page, req)
    assert driver._pending_count == 4  # noqa: SLF001
    assert driver._pending_aspect == "16:9"  # noqa: SLF001


@pytest.mark.asyncio
async def test_configure_image_settings_portrait_aspect() -> None:
    driver = AgenticFlowUiDriver()
    req = _make_image_request(count=1, aspect=Aspect.PORTRAIT)
    await driver.configure_image_settings(_mock_page_no_settings_panel(), req)
    assert driver._pending_aspect == "9:16"  # noqa: SLF001


@pytest.mark.asyncio
async def test_configure_image_settings_square_aspect() -> None:
    driver = AgenticFlowUiDriver()
    req = _make_image_request(count=2, aspect=Aspect.SQUARE)
    await driver.configure_image_settings(_mock_page_no_settings_panel(), req)
    assert driver._pending_aspect == "1:1"  # noqa: SLF001
```

Two more call sites pass `configure_image_settings` a page at lines 641 and 761 of the same test file (both already read and checked against the new code path):

**`test_reconcile_instructions_no_op_when_none`** (line 636-642) — **must be edited**, its `page.locator.assert_not_called()` assertion breaks because the new count-enforcement step calls `page.locator(_TUNE_BUTTON_SELECTOR)` unconditionally, even though `instructions=None` correctly skips `_reconcile_instructions` itself. Replace the whole test body:

```python
@pytest.mark.asyncio
async def test_reconcile_instructions_no_op_when_none() -> None:
    driver = AgenticFlowUiDriver()
    page = _mock_page_no_settings_panel()
    req = GenerateImageRequest(prompt="a cat", instructions=None)
    await driver.configure_image_settings(page, req)
    # instructions=None means _reconcile_instructions's REST PATCH path must
    # not run. The settings-panel count-enforcement step (unrelated) DOES
    # call page.locator now, so the original "locator never called"
    # assertion no longer holds — assert the instructions-specific behavior
    # instead.
    assert not page.request.patch.called
```

**`test_driver_reconcile_dispatches_patch_payload`** (line 735-773) — **no change needed**. Its `page` is a bare `MagicMock()` with only `page.request.get`/`page.request.patch` configured, `page.locator` is left auto-mocked. The new count-enforcement step will call `page.locator(...).first` (auto-mocked, no error) then `await tune_btn.count()`, which raises `TypeError` because a bare `MagicMock` (not `AsyncMock`) isn't awaitable — but `_enforce_image_count_via_settings_panel`'s `except Exception` (Step 3) catches and logs that `TypeError` before it can propagate, and it happens *after* `_reconcile_instructions` already ran and called `page.request.patch`. So `mock_patch.assert_called_once()` and every other assertion in this test still pass unmodified. Run it after Step 4 lands to confirm — do not edit it speculatively.

- [ ] **Step 6: Run the existing test file to confirm nothing regressed**

Run: `.venv/Scripts/python.exe -m pytest tests/api/transports/drivers/test_agentic.py -v`
Expected: all tests PASS (the 3+ updated tests, plus everything else unaffected)

- [ ] **Step 7: Write new unit tests for `_enforce_image_count_via_settings_panel`**

Add a new test section (after the `configure_image_settings` section, before `switch_to_image_mode`) in `tests/api/transports/drivers/test_agentic.py`:

```python
# ---------------------------------------------------------------------------
# _enforce_image_count_via_settings_panel — issue #313 fallback
# ---------------------------------------------------------------------------


def _mock_page_with_settings_panel(*, already_selected: str = "x2") -> tuple[MagicMock, MagicMock, MagicMock]:
    """Page where the tune button, count tablist, and Save button are all
    present. Returns (page, target_btn_mock, tune_btn_mock) so tests can
    assert on click() call counts for specific elements.

    ``already_selected`` is unused by the mock itself (the production code
    always clicks the target button unconditionally — see Step 3) but is
    kept as a documented parameter for readability at call sites that care
    about the pre-click state being irrelevant to the assertion.
    """
    del already_selected  # documented no-op — see docstring

    tune_btn = MagicMock()
    tune_btn.count = AsyncMock(return_value=1)
    tune_btn.click = AsyncMock()

    target_btn = MagicMock()
    target_btn.count = AsyncMock(return_value=1)
    target_btn.click = AsyncMock()

    count_tablist = MagicMock()
    count_tablist.locator = MagicMock(return_value=target_btn)

    save_btn = MagicMock()
    save_btn.click = AsyncMock()

    def _locator(selector: str) -> MagicMock:
        result = MagicMock()
        if selector == _TUNE_BUTTON_SELECTOR:
            result.first = tune_btn
        elif selector == _IMAGE_COUNT_TABLIST_SELECTOR:
            result.first = count_tablist
        elif selector == "[data-gflow-save-target='1']":
            result.first = save_btn
        else:
            result.first = MagicMock()
        return result

    page = MagicMock()
    page.locator = MagicMock(side_effect=_locator)
    page.evaluate = AsyncMock(return_value=True)

    return page, target_btn, tune_btn


@pytest.mark.asyncio
async def test_enforce_count_clicks_matching_button_and_saves() -> None:
    driver = AgenticFlowUiDriver()
    page, target_btn, tune_btn = _mock_page_with_settings_panel()
    await driver._enforce_image_count_via_settings_panel(page, 3)  # noqa: SLF001
    tune_btn.click.assert_awaited_once()
    target_btn.click.assert_awaited_once()
    page.evaluate.assert_awaited_once()


@pytest.mark.asyncio
async def test_enforce_count_maps_all_valid_counts_to_button_labels() -> None:
    """1..4 must each resolve to a locator call for the matching '<n>x' label
    (or '1x' for count=1) — a regression here means a future count silently
    clicks the wrong tab."""
    for count, expected_label in agentic_mod._COUNT_BUTTON_TEXT.items():
        driver = AgenticFlowUiDriver()
        page, target_btn, _ = _mock_page_with_settings_panel()
        await driver._enforce_image_count_via_settings_panel(page, count)  # noqa: SLF001
        count_tablist_locator = page.locator(agentic_mod._IMAGE_COUNT_TABLIST_SELECTOR).first
        count_tablist_locator.locator.assert_any_call(f"button:text-is('{expected_label}')")


@pytest.mark.asyncio
async def test_enforce_count_skips_gracefully_when_tune_button_absent() -> None:
    """Older UI cohort / no settings panel at all — must return quietly, not raise."""
    driver = AgenticFlowUiDriver()
    page = MagicMock()
    absent = MagicMock()
    absent.count = AsyncMock(return_value=0)
    page.locator = MagicMock(return_value=absent)
    await driver._enforce_image_count_via_settings_panel(page, 1)  # noqa: SLF001
    # No exception raised is the assertion; nothing else to check.


@pytest.mark.asyncio
async def test_enforce_count_skips_gracefully_when_count_button_absent() -> None:
    driver = AgenticFlowUiDriver()
    tune_btn = MagicMock()
    tune_btn.count = AsyncMock(return_value=1)
    tune_btn.click = AsyncMock()

    absent_target = MagicMock()
    absent_target.count = AsyncMock(return_value=0)

    count_tablist = MagicMock()
    count_tablist.locator = MagicMock(return_value=absent_target)

    def _locator(selector: str) -> MagicMock:
        result = MagicMock()
        if selector == _TUNE_BUTTON_SELECTOR:
            result.first = tune_btn
        elif selector == _IMAGE_COUNT_TABLIST_SELECTOR:
            result.first = count_tablist
        else:
            result.first = MagicMock()
        return result

    page = MagicMock()
    page.locator = MagicMock(side_effect=_locator)
    await driver._enforce_image_count_via_settings_panel(page, 1)  # noqa: SLF001
    # No exception — graceful skip.


@pytest.mark.asyncio
async def test_enforce_count_skips_save_click_when_save_button_not_found() -> None:
    driver = AgenticFlowUiDriver()
    page, target_btn, tune_btn = _mock_page_with_settings_panel()
    page.evaluate = AsyncMock(return_value=False)  # _FIND_SAVE_BUTTON_JS found nothing
    await driver._enforce_image_count_via_settings_panel(page, 1)  # noqa: SLF001
    tune_btn.click.assert_awaited_once()
    target_btn.click.assert_awaited_once()
    # No exception, and no attempt to click a nonexistent save button.


@pytest.mark.asyncio
async def test_enforce_count_swallows_exceptions_and_never_raises() -> None:
    driver = AgenticFlowUiDriver()
    page = MagicMock()
    page.locator = MagicMock(side_effect=RuntimeError("boom"))
    # Must not raise.
    await driver._enforce_image_count_via_settings_panel(page, 1)  # noqa: SLF001
```

Add the two new selector-constant imports to the existing import block at the top of the file:

```python
from gflow_cli.api.transports.drivers.agentic import (
    _IMAGE_COUNT_TABLIST_SELECTOR,
    _MEDIA_REDIRECT_BASE,
    _TUNE_BUTTON_SELECTOR,
    AgenticFlowUiDriver,
    _extract_uuids,
)
```

- [ ] **Step 8: Run the full test file again**

Run: `.venv/Scripts/python.exe -m pytest tests/api/transports/drivers/test_agentic.py -v`
Expected: all tests PASS, including the 6 new tests from Step 7

- [ ] **Step 9: Update `docs/AGENT_UI_RECON.md` to reflect the now-implemented fallback**

In the "Settings via prompt, not the `tune` popover (agentic acts MCP-like)" section (starts around line 131), the bullet:

```
- **`configure_settings` becomes optional.** Encoding settings as a directive prompt
  avoids driving the fragile `tune` → Radix-popover dropdowns — the most drift-prone
  surface on a volatile A/B UI. Prefer prompt-encoding; treat popover automation as a
  fallback only if prompt-steering proves unreliable.
```

becomes:

```
- **`configure_settings` drives count as a fallback (2026-07-16, issue #313).**
  Prompt-encoding alone proved unreliable: Agent mode's `tune` panel has a
  STICKY "Image generation default" count that silently overrode the
  natural-language directive when stale. `AgenticFlowUiDriver` now sets that
  control to match the request (best-effort, never raises — falls through to
  prompt-only on any selector miss) in addition to the natural-language
  phrasing. Aspect/model are NOT automated via this panel — count only, to
  keep the newly-added surface area minimal. See
  `flow-agent-settings-panel-sticky-defaults` project memory for the full
  selector write-up.
```

- [ ] **Step 10: Run ruff + pyright on the changed files**

Run: `.venv/Scripts/python.exe -m ruff check src/gflow_cli/api/transports/drivers/agentic.py tests/api/transports/drivers/test_agentic.py`
Run: `.venv/Scripts/python.exe -m ruff format --check src/gflow_cli/api/transports/drivers/agentic.py tests/api/transports/drivers/test_agentic.py`
Run: `.venv/Scripts/python.exe -m pyright src/gflow_cli/api/transports/drivers/agentic.py`
Expected: all clean. Fix any issues before committing.

- [ ] **Step 11: Commit**

```bash
git add src/gflow_cli/api/transports/drivers/agentic.py tests/api/transports/drivers/test_agentic.py docs/AGENT_UI_RECON.md
git commit -m "fix(agentic): enforce image count via Agent settings panel (#313)

Agent mode's tune Settings panel has a sticky 'Image generation default'
count that can silently override the natural-language directive when
stale, causing the MediaAttributionError ambiguity guard to fire after a
wasted generation attempt. configure_image_settings now best-effort drives
that panel's count control to match the request, keeping the existing
natural-language phrasing and ambiguity guard as reinforcement/backstop."
```

---

## Post-implementation verification (controller, not a subagent task)

Image generation is credit-free (memory `flow-credits-videos-only`), so after this task's review passes, the controller should run a real live generation via `gflow image` in Agent mode against a project with a known-stale count default (e.g. the `denon82` profile / project `580a6bbf-d433-4153-80b9-1842b5a560ea` used during the spike, after manually setting its Agent settings count back to something other than the requested count) and confirm the generation returns the requested count without raising `MediaAttributionError`. This is the actual DoD signal (memory `feature-dod-full-e2e`, `done-means-e2e-verified`) — the unit tests above only prove the selector logic in isolation against mocks.
