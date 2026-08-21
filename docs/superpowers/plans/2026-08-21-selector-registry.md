# Selector Registry + Drift Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn gflow's scattered Flow DOM selectors into a structured, enumerable registry that a $0 CI probe walks nightly, so drift is *named* rather than inferred from a failing test.

**Architecture:** A pure-data registry (`Surface` + `Selector`) and a pure grader, driven by one CI probe: navigate at a pinned viewport, detect the UI arm with production's own detector, read the DOM once, grade.

**Tech Stack:** Python 3.11+, Playwright (already a dep), pytest, ruff, GitHub Actions.

**Spec:** [`docs/superpowers/specs/2026-08-21-selector-registry-design.md`](../specs/2026-08-21-selector-registry-design.md)

## Global Constraints

- **Windows dev:** `.venv/Scripts/python.exe -m pytest`, never `uv run pytest` (broken here). Never pipe pytest to `tail` — it masks the exit code.
- **$0 invariant:** reach steps MUST be non-mutating and credit-free. Never submit.
- **Pin the viewport to 1920×1080.** `ui_automation.py:117-124`: smaller *"would cross the responsive breakpoint and drift the selectors"*. `FlowApiClient` (1280×720) and Playwright's default (1280×720) are both **below** it.
- **`observed_mode` is REQUIRED by the grader**, and must come from production's own detector: `factory._any_present(page, _CLASSIC_CROP_SELECTORS)` over **all six** ratio variants. Checking only `candidates[0]` is a silent-pass hole — a classic editor on a 9:16 project reads AGENTIC, so a drifted `crop_control` grades EXPECTED_ABSENT and hides permanently.
- **A stale project renders an error shell** (~441KB, 3 buttons, 0 icons) indistinguishable from an auth wall. The probe must therefore be able to say *"the surface did not load"* (exit 2) rather than *"a selector drifted"* (exit 1). It does **not** create projects — that would be a mutating operation against the $0 invariant.
- **No default-suite test may launch a real browser.** `ci.yml:165` runs plain pytest and **no workflow installs Playwright**. Browser-touching tests live in the probe workflow, which installs chromium itself.
- **Secrets:** `__Secure-next-auth.session-token` only, from a dedicated throwaway account. Never log a cookie value. For any published output reuse `src/gflow_cli/data/redaction.py` — it already covers `Bearer`, `SAPISIDHASH`, `__Secure-next-auth.session-token` and signed queries.
- **Package name:** `gflow_cli.flow_selectors`, NOT `gflow_cli.ui` — `src/gflow_cli/ui/` already means gflow's own UI (`app.py`, `server.py`).

---

### Task 1: Registry model, grader, and entries

**Files:**
- Create: `src/gflow_cli/flow_selectors/__init__.py`, `model.py`, `grading.py`, `registry.py`
- Test: `tests/flow_selectors/__init__.py`, `test_model.py`, `test_grading.py`, `test_registry.py`

**Interfaces:**
- Consumes: `gflow_cli.config.UiMode`; the existing constants in `mode_control` / `ui_automation`.
- Produces: `Surface`, `Selector`, `Grade`, `Outcome`, `grade(...)`, `SURFACES`, `SELECTORS`, `for_surface()`.

- [ ] **Step 1: Write the failing model + grading tests**

```python
# tests/flow_selectors/test_model.py
from __future__ import annotations

import pytest

from gflow_cli.flow_selectors.model import Selector, Surface


def test_selector_requires_at_least_one_candidate() -> None:
    with pytest.raises(ValueError, match="at least one candidate"):
        Selector(key="editor.x", surface="editor", candidates=())


def test_selector_key_must_be_dotted_lower_snake() -> None:
    with pytest.raises(ValueError, match="dotted lower_snake"):
        Selector(key="Editor Composer", surface="editor", candidates=("div",))


def test_surface_pins_a_viewport_at_or_above_the_breakpoint() -> None:
    """ui_automation.py:117-124 — below 1920x1080 crosses Flow's responsive
    breakpoint and drifts the selectors. A probe that forgets this reports
    false drift, so the model refuses to let it be forgotten."""
    with pytest.raises(ValueError, match="breakpoint"):
        Surface(key="editor", url_template="/x", viewport=(1280, 720))


def test_surface_accepts_the_production_viewport() -> None:
    assert Surface(key="editor", url_template="/x", viewport=(1920, 1080)).viewport == (
        1920,
        1080,
    )


def test_a_wide_but_short_viewport_is_still_rejected() -> None:
    """Tuple comparison would ACCEPT (2560, 720): lexicographically 2560 > 1920
    ends the comparison before height is considered. Verified by execution."""
    with pytest.raises(ValueError, match="breakpoint"):
        Surface(key="editor", url_template="/x", viewport=(2560, 720))
```

```python
# tests/flow_selectors/test_grading.py
from __future__ import annotations

import pytest

from gflow_cli.config import UiMode
from gflow_cli.flow_selectors.grading import Grade, grade
from gflow_cli.flow_selectors.model import Selector

SEL = Selector(key="editor.composer.input", surface="editor",
               candidates=("div[data-slate-editor]", "div[role=textbox]"))


def test_first_candidate_is_a_hit() -> None:
    assert grade(SEL, 0, match_count=1, observed_mode=UiMode.CLASSIC).grade is Grade.HIT


def test_later_candidate_is_fallback_not_failure() -> None:
    out = grade(SEL, 1, match_count=1, observed_mode=UiMode.CLASSIC)
    assert out.grade is Grade.FALLBACK
    assert out.is_failure is False


def test_multiple_matches_on_a_unique_selector_is_ambiguous() -> None:
    """Drivers call .first, so a second match means gflow clicks the WRONG
    element while a count-based check reports success. SIDEBAR_CLOSE_FALLBACK
    is deliberately unscoped and is the standing candidate for this."""
    unique = Selector(key="editor.sidebar.close", surface="editor",
                      candidates=("button",), expect_unique=True)
    out = grade(unique, 0, match_count=2, observed_mode=UiMode.CLASSIC)
    assert out.grade is Grade.AMBIGUOUS
    assert out.is_failure is True


def test_nothing_resolving_is_drift() -> None:
    assert grade(SEL, None, match_count=0, observed_mode=UiMode.CLASSIC).is_failure is True


def test_wrong_mode_makes_a_miss_expected() -> None:
    classic = Selector(key="editor.crop_control", surface="editor",
                       candidates=("button",), mode=UiMode.CLASSIC)
    out = grade(classic, None, match_count=0, observed_mode=UiMode.AGENTIC)
    assert out.grade is Grade.EXPECTED_ABSENT
    assert out.is_failure is False


def test_grade_cannot_be_called_without_a_mode() -> None:
    """The false-DRIFT guarantee is enforced by the signature, not a sentinel
    grade: omitting observed_mode is a TypeError, not a silent misgrade."""
    classic = Selector(key="editor.crop_control", surface="editor",
                       candidates=("button",), mode=UiMode.CLASSIC)
    with pytest.raises(TypeError):
        grade(classic, None, match_count=0)  # type: ignore[call-arg]
```

- [ ] **Step 2: Run both to confirm they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/flow_selectors -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gflow_cli.flow_selectors'`

- [ ] **Step 3: Implement the model**

```python
# src/gflow_cli/flow_selectors/model.py
"""Structured inventory of the Flow DOM elements gflow depends on.

A selector without its page is unfindable; a MISS without its mode is
unattributable. CROP_SELECTORS[0] legitimately misses on the agentic arm
because it IS the classic-mode indicator (factory.py:116). Context is data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from gflow_cli.config import UiMode

_SEG = r"[a-z0-9]+(?:_[a-z0-9]+)*"
_KEY_RE = re.compile(rf"^{_SEG}(?:\.{_SEG})+$")
_SURFACE_KEY_RE = re.compile(rf"^{_SEG}(?:\.{_SEG})*$")

# ui_automation.py:117-124 — below this, Flow crosses its responsive breakpoint
# and the selectors drift. A probe rendering smaller reports drift that is not there.
MIN_VIEWPORT = (1920, 1080)


@dataclass(frozen=True)
class Surface:
    key: str
    url_template: str
    viewport: tuple[int, int]

    def __post_init__(self) -> None:
        if not _SURFACE_KEY_RE.match(self.key):
            msg = f"surface key must be lower_snake, optionally dotted: {self.key!r}"
            raise ValueError(msg)
        # Per-AXIS, not tuple comparison: (2560, 720) < (1920, 1080) is False
        # lexicographically, so a 720px-tall surface would slip the guard.
        if self.viewport[0] < MIN_VIEWPORT[0] or self.viewport[1] < MIN_VIEWPORT[1]:
            msg = (
                f"{self.key}: viewport {self.viewport} is below Flow's responsive "
                f"breakpoint {MIN_VIEWPORT}; selectors drift below it"
            )
            raise ValueError(msg)


@dataclass(frozen=True)
class Selector:
    key: str
    surface: str
    candidates: tuple[str, ...]
    mode: UiMode | None = None
    expect_unique: bool = False
    note: str = ""

    def __post_init__(self) -> None:
        if not self.candidates:
            msg = f"{self.key}: needs at least one candidate"
            raise ValueError(msg)
        if not _KEY_RE.match(self.key):
            msg = f"selector key must be dotted lower_snake: {self.key!r}"
            raise ValueError(msg)
```

- [ ] **Step 4: Implement the grader**

```python
# src/gflow_cli/flow_selectors/grading.py
"""Pure grading. No browser, no IO."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from gflow_cli.config import UiMode
from gflow_cli.flow_selectors.model import Selector


class Grade(Enum):
    HIT = "hit"
    FALLBACK = "fallback"              # a later candidate held — warn, do not fail
    AMBIGUOUS = "ambiguous"            # >1 match; drivers use .first, so this misclicks
    MISS = "miss"
    EXPECTED_ABSENT = "expected_absent"


@dataclass(frozen=True)
class Outcome:
    selector_key: str
    grade: Grade
    resolved_index: int | None

    @property
    def is_failure(self) -> bool:
        return self.grade in (Grade.MISS, Grade.AMBIGUOUS)


def grade(
    selector: Selector,
    resolved_index: int | None,
    match_count: int,
    observed_mode: UiMode,
) -> Outcome:
    """``observed_mode`` is REQUIRED, deliberately.

    Defaulting it to None let a mode-scoped selector be graded with no context,
    which produced a guaranteed false DRIFT on every agentic capture. Requiring
    it moves that guarantee to the type level — the caller cannot forget.
    """
    if resolved_index is not None:
        if selector.expect_unique and match_count > 1:
            return Outcome(selector.key, Grade.AMBIGUOUS, resolved_index)
        g = Grade.HIT if resolved_index == 0 else Grade.FALLBACK
        return Outcome(selector.key, g, resolved_index)

    if selector.mode is not None and selector.mode != observed_mode:
        return Outcome(selector.key, Grade.EXPECTED_ABSENT, None)
    return Outcome(selector.key, Grade.MISS, None)
```

- [ ] **Step 5: Run model + grading tests**

Run: `.venv/Scripts/python.exe -m pytest tests/flow_selectors/test_model.py tests/flow_selectors/test_grading.py -v`
Expected: PASS (11 tests: 5 model + 6 grading)

- [ ] **Step 6: Write the failing registry test**

```python
# tests/flow_selectors/test_registry.py
from __future__ import annotations

import pytest

from gflow_cli.config import UiMode
from gflow_cli.flow_selectors import registry


def test_every_selector_points_at_a_declared_surface() -> None:
    for sel in registry.SELECTORS:
        assert sel.surface in registry.SURFACES


def test_keys_are_unique() -> None:
    keys = [s.key for s in registry.SELECTORS]
    assert len(keys) == len(set(keys))


def test_state_gated_families_are_deliberately_absent() -> None:
    """Two incident families CANNOT live on a URL-only surface:

    - sidebar close needs the sidebar EXPANDED
    - #404's count tabs sit inside the generation-settings panel, which must be
      clicked open (`_open_gen_settings_panel`; `_is_settings_panel_open` exists
      because it is normally closed)

    Registering either grades MISS on every clean capture. That #404 — the
    incident this design leans on hardest — needs `Reach` is the argument FOR
    prioritising Reach, not for registering a selector that reds nightly.
    """
    keys = {s.key for s in registry.SELECTORS}
    assert "editor.sidebar.close" not in keys
    assert "editor.count_tabs" not in keys


def test_incident_families_are_registered() -> None:
    keys = {s.key for s in registry.SELECTORS}
    assert keys >= {"editor.composer.input", "editor.composer.submit",
                    "editor.agent_toggle", "editor.crop_control"}


def test_for_surface_returns_every_entry_including_mode_scoped_ones() -> None:
    """Mode belongs to grading, not selection — a mode-scoped entry must still
    reach the report as EXPECTED_ABSENT rather than vanishing from it."""
    keys = {s.key for s in registry.for_surface("editor")}
    assert "editor.crop_control" in keys


```

- [ ] **Step 7: Implement the registry**

Read `mode_control.py:43,49` and `ui_automation.py:198,209` first; import the constants, never retype their values.

```python
# src/gflow_cli/flow_selectors/registry.py
"""The Flow DOM elements gflow depends on, with the context that makes drift readable.

Scope: families with incident history, present on a FRESHLY LOADED editor.
State-gated entries (sidebar close) wait for `Reach` — see spec R4.
"""

from __future__ import annotations

from gflow_cli.api.transports import mode_control, ui_automation
from gflow_cli.config import UiMode
from gflow_cli.flow_selectors.model import Selector, Surface

SURFACES: dict[str, Surface] = {
    "editor": Surface(
        key="editor",
        url_template="https://labs.google/fx/{locale}/tools/flow/project/{project_id}",
        viewport=(1920, 1080),
    ),
}

SELECTORS: tuple[Selector, ...] = (
    Selector(
        key="editor.composer.input",
        surface="editor",
        candidates=tuple(ui_automation.PROMPT_INPUT_SELECTORS),
        note="#493 — the expanded chat sidebar hid this entirely.",
    ),
    Selector(
        key="editor.composer.submit",
        surface="editor",
        candidates=tuple(ui_automation.SUBMIT_BUTTON_SELECTORS),
        note="NOT expect_unique: scripts/dev/capture_i2v_post_bind_state.py "
        "exists because this can legitimately match a top-level submit, an "
        "in-panel submit and a Send-to-Agent submit at once.",
    ),
    Selector(
        key="editor.agent_toggle",
        surface="editor",
        candidates=(mode_control.AGENT_TOGGLE_SELECTOR,),
        expect_unique=True,
        note="#313 — agent settings panel became sticky.",
    ),
    Selector(
        key="editor.crop_control",
        surface="editor",
        candidates=tuple(mode_control.CROP_SELECTORS),
        mode=UiMode.CLASSIC,
        note="Classic-mode INDICATOR (factory.py:116). Absent on the agentic arm "
        "by design — grading that MISS as drift is the error this registry exists "
        "to prevent — which is why observed_mode is a REQUIRED grader argument.",
    ),
)

def for_surface(surface_key: str) -> tuple[Selector, ...]:
    """Every entry on a surface, in registry order.

    Deliberately does NOT filter by mode. Mode belongs to grading, not
    selection: a mode-scoped entry absent on the other arm must still appear in
    the report as EXPECTED_ABSENT, or the report silently shrinks and nobody
    notices coverage was skipped.
    """
    return tuple(s for s in SELECTORS if s.surface == surface_key)
```

- [ ] **Step 8: Run the whole suite, lint, commit**

```bash
.venv/Scripts/python.exe -m pytest tests/flow_selectors -v
.venv/Scripts/python.exe -m ruff check src/gflow_cli/flow_selectors tests/flow_selectors
.venv/Scripts/python.exe -m ruff format src/gflow_cli/flow_selectors tests/flow_selectors
git add src/gflow_cli/flow_selectors tests/flow_selectors
git commit -m "feat(selectors): registry model, grader, and the incident families

Surface pins 1920x1080: below Flow's responsive breakpoint the selectors drift,
so a probe that forgets the viewport reports drift that is not there.

observed_mode is a REQUIRED grader argument: defaulting it let a mode-scoped
selector be graded with no context, a guaranteed false DRIFT on every agentic
capture. The signature now enforces it.

Refs #404 #493 #313"
```

---

### Task 2: CI probe

**Files:**
- Create: `scripts/probe/run_probe.py`
- Create: `.github/workflows/selector-probe.yml`
- Test: `tests/flow_selectors/test_report.py`

**Interfaces:**
- Consumes: `registry`, `grade`, `Outcome`.
- Produces: `render_report(outcomes) -> str`; `async resolve(page, entries, mode) -> list[Outcome]`.

> **No `check_html` / no static re-parse.** An earlier draft closed the live page,
> launched a *second* chromium and re-parsed `page.content()` through
> `set_content()` — which drops external CSS/JS and shadow roots, making it
> strictly lower fidelity than the live page it had just discarded. It was
> residue from the cut snapshot layer. Resolve against the live `page`.

- [ ] **Step 1: Write the failing report test** (pure — no browser, so it is safe in the default suite)

```python
# tests/flow_selectors/test_report.py
from __future__ import annotations

from gflow_cli.flow_selectors.grading import Grade, Outcome
from scripts.probe.run_probe import render_report


def test_report_names_the_drifted_selector() -> None:
    body = render_report([Outcome("editor.composer.input", Grade.MISS, None)])
    assert "editor.composer.input" in body
    assert "DRIFT" in body


def test_fallback_is_a_warning_not_drift() -> None:
    body = render_report([Outcome("editor.sidebar.close", Grade.FALLBACK, 1)])
    assert "FALLBACK" in body
    assert "DRIFT" not in body


def test_ambiguous_is_reported_as_a_failure() -> None:
    body = render_report([Outcome("editor.agent_toggle", Grade.AMBIGUOUS, 0)])
    assert "AMBIGUOUS" in body
    assert "1 need" in body


def test_alternate_state_gate_has_a_scoped_and_an_unscoped_candidate() -> None:
    """R8. Two guards in one test:

    - the gate must not rely on the edit_square scoping alone — that was #493's
      single point of failure, and a drifted scope would silently disable it
    - the candidates must be DISTINCT. SIDEBAR_CLOSE_SELECTOR and
      AGENT_CHAT_PANEL_CLOSE_SELECTOR are byte-equal aliases, so an earlier
      draft listed one selector twice and certified a label it could not emit.
    """
    from scripts.probe.run_probe import _ALTERNATE_STATE_CANDIDATES

    assert len(set(_ALTERNATE_STATE_CANDIDATES)) == len(_ALTERNATE_STATE_CANDIDATES)
    assert any("edit_square" in c for c in _ALTERNATE_STATE_CANDIDATES)
    assert any("edit_square" not in c for c in _ALTERNATE_STATE_CANDIDATES)


def test_expected_absent_is_visible_but_not_a_failure() -> None:
    """A mode-scoped entry absent on the other arm must still appear, or the
    report silently shrinks and nobody notices coverage was skipped."""
    body = render_report([Outcome("editor.crop_control", Grade.EXPECTED_ABSENT, None)])
    assert "editor.crop_control" in body
    assert "0 need" in body
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/flow_selectors/test_report.py -v`
Expected: FAIL — `No module named 'scripts.probe.run_probe'`

- [ ] **Step 3: Implement the probe**

```python
# scripts/probe/run_probe.py
"""Probe live Flow for selector drift. $0 — navigate and read only.

Auth is ONE cookie: __Secure-next-auth.session-token. Measured sufficient AT
MINT TIME; an aged token is unverified (spec §2.2), so exit 2 exists to keep an
expired credential from ever being reported as drift.

Exit: 0 clean · 1 drift · 2 infrastructure.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from gflow_cli.api.transports import mode_control
from gflow_cli.api.transports.drivers import factory
from gflow_cli.config import UiMode
from gflow_cli.flow_selectors import registry
from gflow_cli.flow_selectors.grading import Grade, Outcome, grade
from gflow_cli.flow_selectors.model import Selector

SESSION_COOKIE = "__Secure-next-auth.session-token"
_LABEL = {
    Grade.HIT: "ok",
    Grade.FALLBACK: "FALLBACK",
    Grade.AMBIGUOUS: "AMBIGUOUS",
    Grade.MISS: "DRIFT",
    Grade.EXPECTED_ABSENT: "n/a",
}


def render_report(outcomes: list[Outcome]) -> str:
    """Publication-safe: keys and grades only, never selectors or DOM."""
    lines = ["| selector | result |", "| --- | --- |"]
    lines += [f"| `{o.selector_key}` | {_LABEL[o.grade]} |" for o in outcomes]
    bad = [o.selector_key for o in outcomes if o.is_failure]
    lines += ["", f"**{len(bad)} need attention**" if bad else "**0 need attention.**"]
    return "\n".join(lines)


# Known ZERO-CLICK alternate states. Neither is drift, and both hide the
# composer, so grading through them would report drift that did not happen.
#   mode_control.py:66-84  — an expanded chat sidebar "removes the classic
#                            composer entirely... no crop_* trigger AND no Agent pill"
#   ui_automation_video.py:149-153 — a chat panel "appears on some project opens
#                            and not others"; while up "the in-composer pill is
#                            NOT in the DOM at all"
# NOTE: mode_control.SIDEBAR_CLOSE_SELECTOR and
# ui_automation_video.AGENT_CHAT_PANEL_CLOSE_SELECTOR are BYTE-EQUAL — two names
# for one selector. Listing both would make the second entry unreachable. One
# scoped candidate plus the genuinely-different unscoped fallback, mirroring
# production's two-tier close: the edit_square scoping was #493's single point
# of failure, so relying on it alone would let a drifted scope disable this gate.
_ALTERNATE_STATE_CANDIDATES: tuple[str, ...] = (
    mode_control.SIDEBAR_CLOSE_SELECTOR,
    mode_control.SIDEBAR_CLOSE_FALLBACK_SELECTOR,
)
_ALTERNATE_STATE_LABEL = "expanded chat sidebar / agent chat panel"


async def alternate_state(page: object) -> str | None:
    """Name the known alternate state the editor is in, if any.

    This is the difference between "the composer is gone because Google moved
    it" (drift, exit 1) and "the composer is gone because a panel is covering
    it" (inconclusive, exit 2). Three of the four registered selectors are
    absent in these states, so without this gate a cohort flap reads as drift.
    """
    for selector in _ALTERNATE_STATE_CANDIDATES:
        if await page.locator(selector).count():  # type: ignore[attr-defined]
            return _ALTERNATE_STATE_LABEL
    return None


async def resolve(page: object, entries: Sequence[Selector], mode: UiMode) -> list[Outcome]:
    """Resolve each entry against the LIVE page and grade it.

    Against the live page, not a re-parsed copy: set_content() drops external
    CSS/JS and page.content() omits shadow roots, so a static round-trip is
    strictly lower fidelity than the page already open here.
    """
    results: list[Outcome] = []
    for entry in entries:
        index: int | None = None
        count = 0
        for i, candidate in enumerate(entry.candidates):
            count = await page.locator(candidate).count()  # type: ignore[attr-defined]
            if count:
                index = i
                break
        results.append(grade(entry, index, count, mode))
    return results


async def run(token: str, project_id: str, surface_key: str) -> int:
    surface = registry.SURFACES[surface_key]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(
                viewport={"width": surface.viewport[0], "height": surface.viewport[1]}
            )
            await ctx.add_cookies([{
                "name": SESSION_COOKIE, "value": token,
                "domain": "labs.google", "path": "/",
                "httpOnly": True, "secure": True, "sameSite": "Lax",
            }])
            page = await ctx.new_page()
            try:
                await page.goto(
                    surface.url_template.format(locale="en", project_id=project_id),
                    wait_until="domcontentloaded", timeout=90_000,
                )
            except PlaywrightError as exc:
                # A raw traceback is neither exit 1 nor exit 2, and its message
                # embeds the project id. Keep the contract intact.
                print(f"::error::navigation failed: {type(exc).__name__}")
                return 2
            for _ in range(25):
                if await page.locator("i.google-symbols").count():
                    break
                await page.wait_for_timeout(1000)
            else:
                # Expired token or missing project — NOT drift. Never conflate them.
                print("::error::surface never hydrated (expired token or missing project)")
                return 2
            # Production's OWN detector, over all six ratio variants. Checking
            # candidates[0] alone misreads a 9:16 classic editor as agentic, and a
            # drifted crop_control then grades EXPECTED_ABSENT — hidden forever.
            mode = (
                UiMode.CLASSIC
                if await factory._any_present(page, factory._CLASSIC_CROP_SELECTORS)  # noqa: SLF001
                else UiMode.AGENTIC
            )
            blocked = await alternate_state(page)
            if blocked is not None:
                # NOT drift: a known cohort/load state hides the composer.
                print(f"::warning::editor is in a known alternate state ({blocked})")
                return 2
            outcomes = await resolve(page, registry.for_surface(surface_key), mode)
        finally:
            await browser.close()

    print(f"observed mode: {mode.value}")
    print(render_report(outcomes))
    return 1 if any(o.is_failure for o in outcomes) else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--surface", default="editor")
    args = p.parse_args()
    token = os.environ.get("GFLOW_CI_SESSION_TOKEN", "")
    project = os.environ.get("GFLOW_CI_PROJECT_ID", "")
    if not token or not project:
        print("::error::GFLOW_CI_SESSION_TOKEN and GFLOW_CI_PROJECT_ID are required")
        return 2
    return asyncio.run(run(token, project, args.surface))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the report tests**

Run: `.venv/Scripts/python.exe -m pytest tests/flow_selectors/test_report.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Add the workflow**

```yaml
# .github/workflows/selector-probe.yml
name: Selector drift probe

on:
  schedule:
    - cron: "0 5 * * *"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  probe:
    runs-on: ubuntu-latest
    if: github.repository == 'ffroliva/gflow-cli'
    steps:
      - uses: actions/checkout@v4
        with: { persist-credentials: false }
      - uses: astral-sh/setup-uv@v7
      - run: uv sync --frozen
      - run: uv run playwright install chromium --with-deps
      - name: Probe
        env:
          GFLOW_CI_SESSION_TOKEN: ${{ secrets.GFLOW_CI_SESSION_TOKEN }}
          GFLOW_CI_PROJECT_ID: ${{ secrets.GFLOW_CI_PROJECT_ID }}
        run: uv run python scripts/probe/run_probe.py --surface editor
```

**Never add `pull_request`.** Fork PRs are safe (GitHub withholds secrets), but
**same-repo branch PRs do receive them** while checking out attacker-editable probe
code. Never use `pull_request_target`.

- [ ] **Step 6: Full gate and commit**

```bash
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m ruff format --check src tests
.venv/Scripts/python.exe scripts/ci/check_repo_hygiene.py
.venv/Scripts/python.exe -m pytest tests/flow_selectors tests/scripts -v
git add scripts/probe/run_probe.py .github/workflows/selector-probe.yml tests/flow_selectors/test_report.py
git commit -m "feat(probe): CI selector-drift probe on a single session cookie

Exit 2 (infrastructure) stays distinct from exit 1 (drift): an expired token or
a deleted project must never be published as Google changing the page.

schedule + workflow_dispatch only. pull_request would hand the secret to
same-repo branch PRs alongside attacker-editable probe code."
```

---

## First run checklist (R1)

1. Create the throwaway account, sign in once, capture its session token.
2. `gh secret set GFLOW_CI_SESSION_TOKEN` and `GFLOW_CI_PROJECT_ID`.
3. `workflow_dispatch` once. **Datacenter-IP behaviour is unmeasured** — all evidence is from a residential IP.
4. **Re-run on day 7.** §2.2 establishes the cookie only at mint time; an aged token is unverified, and §2.7 says it hard-dies at day 30 with no rotation and therefore no warning.

## Deferred to a follow-up plan

- **AST guardrail.** It flags **9 real offenders today** (`ui_automation_video.py:91-96,162`, `agentic.py:62`, `diagnostics.py:1738`), so it reds on arrival while its own migration is deferred.
- **`selector_key` on `UiSelectorDriftError` and `GFLOW_CLI_SELECTOR_OVERRIDE`** — blocked on a scope decision (spec §3.5). Both need the drivers to read from the registry; `GFlowError.__init__` also takes a fixed kwarg list, so `selector_key` is not a one-line addition.
- Remaining ~35 selectors, inverting the import direction, `Reach` for state-gated surfaces, additional surfaces.
