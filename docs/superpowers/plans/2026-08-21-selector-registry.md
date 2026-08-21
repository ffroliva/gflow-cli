# Selector Registry + Drift Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn gflow's scattered Flow DOM selectors into a structured, enumerable registry that a $0 CI probe walks nightly, so drift is *named* rather than inferred from a failing test.

**Architecture:** A pure-data registry (`Surface` + `Selector`) and a pure grader. Capture (navigate at a pinned viewport → `page.content()` + a mode sidecar) is separate from check (`html + entries + observed → grade`), so the probe's evidence and its verdict are independently inspectable.

**Tech Stack:** Python 3.11+, Playwright (already a dep), pytest, ruff, GitHub Actions.

**Spec:** [`docs/superpowers/specs/2026-08-21-selector-registry-design.md`](../specs/2026-08-21-selector-registry-design.md)

## Global Constraints

- **Windows dev:** `.venv/Scripts/python.exe -m pytest`, never `uv run pytest` (broken here). Never pipe pytest to `tail` — it masks the exit code.
- **$0 invariant:** reach steps MUST be non-mutating and credit-free. Never submit.
- **Pin the viewport to 1920×1080.** `ui_automation.py:117-124`: smaller *"would cross the responsive breakpoint and drift the selectors"*. `FlowApiClient` (1280×720) and Playwright's default (1280×720) are both **below** it.
- **`observed_mode` is a capture-time sidecar, never inferred at check time** — inferring it is circular, because the mode detector IS the crop probe.
- **Every DOM probe creates-or-verifies its project.** A stale id renders an error shell (~441KB, 3 buttons, 0 icons) indistinguishable from an auth wall.
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
- Produces: `Surface`, `Selector`, `Grade`, `Outcome`, `grade(...)`, `SURFACES`, `SELECTORS`, `by_key()`, `for_surface()`.

- [ ] **Step 1: Write the failing model + grading tests**

```python
# tests/flow_selectors/test_model.py
from __future__ import annotations

import pytest

from gflow_cli.config import UiMode
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
        Surface(key="editor", url_template="/x", viewport=(1280, 720),
                modes=(UiMode.CLASSIC,))


def test_surface_accepts_the_production_viewport() -> None:
    s = Surface(key="editor", url_template="/x", viewport=(1920, 1080),
                modes=(UiMode.CLASSIC, UiMode.AGENTIC))
    assert s.viewport == (1920, 1080)
```

```python
# tests/flow_selectors/test_grading.py
from __future__ import annotations

from gflow_cli.config import UiMode
from gflow_cli.flow_selectors.grading import Grade, grade
from gflow_cli.flow_selectors.model import Selector

SEL = Selector(key="editor.count_tabs", surface="editor",
               candidates=("[role=tab]:text-is('x2')", "[role=tab]:text-is('2x')"))


def test_first_candidate_is_a_hit() -> None:
    assert grade(SEL, resolved_index=0, match_count=1).grade is Grade.HIT


def test_later_candidate_is_fallback_not_failure() -> None:
    out = grade(SEL, resolved_index=1, match_count=1)
    assert out.grade is Grade.FALLBACK
    assert out.is_failure is False


def test_multiple_matches_on_a_unique_selector_is_ambiguous() -> None:
    """Drivers call .first, so a second match means gflow clicks the WRONG
    element while a count-based check reports success. SIDEBAR_CLOSE_FALLBACK
    is deliberately unscoped and is the standing candidate for this."""
    unique = Selector(key="editor.sidebar.close", surface="editor",
                      candidates=("button",), expect_unique=True)
    out = grade(unique, resolved_index=0, match_count=2)
    assert out.grade is Grade.AMBIGUOUS
    assert out.is_failure is True


def test_nothing_resolving_is_drift() -> None:
    assert grade(SEL, resolved_index=None, match_count=0).is_failure is True


def test_wrong_mode_makes_a_miss_expected() -> None:
    classic = Selector(key="editor.crop_control", surface="editor",
                       candidates=("button",), mode=UiMode.CLASSIC)
    out = grade(classic, resolved_index=None, match_count=0,
                observed_mode=UiMode.AGENTIC)
    assert out.grade is Grade.EXPECTED_ABSENT
    assert out.is_failure is False


def test_missing_sidecar_mode_does_not_silently_pass_a_mode_scoped_miss() -> None:
    """Without observed_mode the grader CANNOT know whether an absent
    classic-only control is drift or the wrong arm. It must say so rather than
    guess — guessing MISS is what produced a guaranteed false DRIFT in the
    first draft of this plan."""
    classic = Selector(key="editor.crop_control", surface="editor",
                       candidates=("button",), mode=UiMode.CLASSIC)
    out = grade(classic, resolved_index=None, match_count=0, observed_mode=None)
    assert out.grade is Grade.UNKNOWN_CONTEXT
    assert out.is_failure is False
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
    modes: tuple[UiMode, ...] = ()

    def __post_init__(self) -> None:
        if not _SURFACE_KEY_RE.match(self.key):
            msg = f"surface key must be lower_snake, optionally dotted: {self.key!r}"
            raise ValueError(msg)
        if self.viewport < MIN_VIEWPORT:
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
    UNKNOWN_CONTEXT = "unknown_context"  # mode-scoped, but no sidecar mode to judge against


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
    observed_mode: UiMode | None = None,
) -> Outcome:
    if resolved_index is not None:
        if selector.expect_unique and match_count > 1:
            return Outcome(selector.key, Grade.AMBIGUOUS, resolved_index)
        g = Grade.HIT if resolved_index == 0 else Grade.FALLBACK
        return Outcome(selector.key, g, resolved_index)

    if selector.mode is not None:
        if observed_mode is None:
            # Never guess: guessing MISS here is a guaranteed false DRIFT.
            return Outcome(selector.key, Grade.UNKNOWN_CONTEXT, None)
        if selector.mode != observed_mode:
            return Outcome(selector.key, Grade.EXPECTED_ABSENT, None)
    return Outcome(selector.key, Grade.MISS, None)
```

- [ ] **Step 5: Run model + grading tests**

Run: `.venv/Scripts/python.exe -m pytest tests/flow_selectors/test_model.py tests/flow_selectors/test_grading.py -v`
Expected: PASS (11 tests)

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


def test_the_404_family_is_registered() -> None:
    """#404 (1x -> x2 rename) is the most-cited incident in the spec; the first
    draft of this plan tested editor.count_tabs nine times without defining it."""
    sel = registry.by_key("editor.count_tabs")
    assert len(sel.candidates) >= 2, "must match BOTH cohorts, picking by digit"


def test_incident_families_are_registered() -> None:
    for key in ("editor.composer.input", "editor.composer.submit",
                "editor.agent_toggle", "editor.crop_control"):
        registry.by_key(key)  # raises KeyError if missing


def test_by_key_raises_for_unknown() -> None:
    with pytest.raises(KeyError):
        registry.by_key("editor.nope")


def test_for_surface_filters_by_mode() -> None:
    agentic = {s.key for s in registry.for_surface("editor", mode=UiMode.AGENTIC)}
    assert "editor.crop_control" not in agentic


def test_no_state_gated_selector_is_registered_yet() -> None:
    """R4: SIDEBAR_CLOSE only exists while the sidebar is EXPANDED. On a
    freshly-loaded editor it is legitimately absent, so registering it now
    would grade MISS on every clean capture. It waits for Reach."""
    assert "editor.sidebar.close" not in {s.key for s in registry.SELECTORS}
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
        modes=(UiMode.CLASSIC, UiMode.AGENTIC),
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
        expect_unique=True,
    ),
    Selector(
        key="editor.agent_toggle",
        surface="editor",
        candidates=(mode_control.AGENT_TOGGLE_SELECTOR,),
        expect_unique=True,
        note="#313 — agent settings panel became sticky.",
    ),
    Selector(
        key="editor.count_tabs",
        surface="editor",
        candidates=("[role=tab]:text-is('x1')", "[role=tab]:text-is('1x')"),
        note="#404 — Google renamed 1x -> x1. BOTH cohorts must match, and the "
        "driver picks by DIGIT, never by position.",
    ),
    Selector(
        key="editor.crop_control",
        surface="editor",
        candidates=tuple(mode_control.CROP_SELECTORS),
        mode=UiMode.CLASSIC,
        note="Classic-mode INDICATOR (factory.py:116). Absent on the agentic arm "
        "by design — grading that MISS as drift is the error this registry exists "
        "to prevent, which is why an absent sidecar mode yields UNKNOWN_CONTEXT.",
    ),
)

_BY_KEY = {s.key: s for s in SELECTORS}


def by_key(key: str) -> Selector:
    return _BY_KEY[key]


def for_surface(surface_key: str, mode: UiMode | None = None) -> tuple[Selector, ...]:
    return tuple(
        s
        for s in SELECTORS
        if s.surface == surface_key and (mode is None or s.mode is None or s.mode == mode)
    )
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

A mode-scoped selector with no sidecar mode grades UNKNOWN_CONTEXT, never MISS.
Guessing MISS there is a guaranteed false DRIFT on every agentic capture.

Refs #404 #493 #313"
```

---

### Task 2: Capture with sidecar

**Files:**
- Create: `scripts/probe/capture.py`
- Test: `tests/scripts/test_capture.py`

**Interfaces:**
- Consumes: `registry.SURFACES`, `FlowApiClient`.
- Produces: `detect_mode(page) -> UiMode`; `async capture(profile, surface_key) -> tuple[str, dict]`.

- [ ] **Step 1: Write the failing sidecar test**

```python
# tests/scripts/test_capture.py
from __future__ import annotations

from gflow_cli.config import UiMode
from scripts.probe.capture import build_sidecar


def test_sidecar_records_the_context_the_grader_needs() -> None:
    car = build_sidecar(mode=UiMode.AGENTIC, viewport=(1920, 1080),
                        captured_at="2026-08-21T12:00:00Z", surface="editor")
    assert car["mode"] == "agentic"
    assert car["viewport"] == [1920, 1080]
    assert car["surface"] == "editor"


def test_sidecar_never_carries_identifiers() -> None:
    """The sidecar may be published alongside a drift report; keep it to
    context, never project ids, accounts or URLs."""
    car = build_sidecar(mode=UiMode.CLASSIC, viewport=(1920, 1080),
                        captured_at="2026-08-21T12:00:00Z", surface="editor")
    assert set(car) == {"mode", "viewport", "captured_at", "surface"}
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/scripts/test_capture.py -v`
Expected: FAIL — `No module named 'scripts.probe.capture'`

- [ ] **Step 3: Implement capture**

```python
# scripts/probe/capture.py
"""Capture a Flow surface's DOM plus the context needed to grade it. $0.

Creates its own project: a stale id renders an error shell (~441KB, 3 buttons,
0 icons) indistinguishable from an auth wall, a confound that voided three
spike runs before a positive control caught it.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports.drivers import factory
from gflow_cli.auth import profile_dir as resolve_profile
from gflow_cli.config import UiMode
from gflow_cli.flow_selectors import registry


def build_sidecar(
    mode: UiMode, viewport: tuple[int, int], captured_at: str, surface: str
) -> dict[str, object]:
    """Context the grader needs. Deliberately carries no identifiers."""
    return {
        "mode": mode.value,
        "viewport": [viewport[0], viewport[1]],
        "captured_at": captured_at,
        "surface": surface,
    }


async def detect_mode(page: object) -> UiMode:
    """Which arm is rendered. Uses the SAME probe the drivers use, so the
    sidecar cannot disagree with production (factory.py:116)."""
    return (
        UiMode.CLASSIC
        if await factory._any_present(page, factory._CLASSIC_CROP_SELECTORS)  # noqa: SLF001
        else UiMode.AGENTIC
    )


async def capture(profile: str, surface_key: str) -> tuple[str, dict[str, object]]:
    surface = registry.SURFACES[surface_key]
    async with FlowApiClient(
        profile_dir=resolve_profile(profile), headless=True
    ) as client:
        project = await client.create_project(title="selector-probe capture")
        page = client._page or client._context.pages[0]  # noqa: SLF001
        await page.set_viewport_size(
            {"width": surface.viewport[0], "height": surface.viewport[1]}
        )
        await page.goto(
            surface.url_template.format(locale="en", project_id=project.project_id),
            wait_until="domcontentloaded",
            timeout=90_000,
        )
        for _ in range(25):
            if await page.locator("i.google-symbols").count():
                break
            await page.wait_for_timeout(1000)
        else:
            msg = "surface never hydrated — refusing to save an error shell"
            raise SystemExit(msg)
        mode = await detect_mode(page)
        html = await page.content()

    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return html, build_sidecar(mode, surface.viewport, stamp, surface_key)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", required=True)
    p.add_argument("--surface", default="editor")
    args = p.parse_args()
    html, sidecar = asyncio.run(capture(args.profile, args.surface))
    print(f"captured {len(html):,} bytes; context={sidecar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Verify `_any_present` and `_CLASSIC_CROP_SELECTORS` exist**

Run: `grep -n "_any_present\|_CLASSIC_CROP_SELECTORS" src/gflow_cli/api/transports/drivers/factory.py`
Expected: both present (seen at `factory.py:52,116`). If either is renamed, use the current name — do not reimplement mode detection, or the sidecar can disagree with production.

- [ ] **Step 5: Run tests and commit**

```bash
.venv/Scripts/python.exe -m pytest tests/scripts/test_capture.py -v
git add scripts/probe/capture.py tests/scripts/test_capture.py
git commit -m "feat(probe): DOM capture with a mode sidecar at the pinned viewport

Mode is detected with the drivers' own probe, so the sidecar cannot disagree
with production. Inferring it at check time would be circular."
```

---

### Task 3: CI probe

**Files:**
- Create: `src/gflow_cli/flow_selectors/check.py`
- Create: `scripts/probe/run_probe.py`
- Create: `.github/workflows/selector-probe.yml`
- Test: `tests/flow_selectors/test_report.py`

**Interfaces:**
- Consumes: `registry`, `grade`, `Outcome`.
- Produces: `async check_html(html, entries, observed_mode) -> list[Outcome]`; `render_report(outcomes) -> str`.

- [ ] **Step 1: Write the failing report test** (pure — no browser, so it is safe in the default suite)

```python
# tests/flow_selectors/test_report.py
from __future__ import annotations

from gflow_cli.flow_selectors.grading import Grade, Outcome
from scripts.probe.run_probe import render_report


def test_report_names_the_drifted_selector() -> None:
    body = render_report([Outcome("editor.count_tabs", Grade.MISS, None)])
    assert "editor.count_tabs" in body
    assert "DRIFT" in body


def test_fallback_is_a_warning_not_drift() -> None:
    body = render_report([Outcome("editor.sidebar.close", Grade.FALLBACK, 1)])
    assert "FALLBACK" in body
    assert "DRIFT" not in body


def test_ambiguous_is_reported_as_a_failure() -> None:
    body = render_report([Outcome("editor.agent_toggle", Grade.AMBIGUOUS, 0)])
    assert "AMBIGUOUS" in body
    assert "1 need" in body


def test_unknown_context_is_visible_but_not_a_failure() -> None:
    """Silence here would hide that the probe could not judge a mode-scoped
    entry at all."""
    body = render_report([Outcome("editor.crop_control", Grade.UNKNOWN_CONTEXT, None)])
    assert "UNKNOWN" in body
    assert "0 need" in body
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/flow_selectors/test_report.py -v`
Expected: FAIL — `No module named 'scripts.probe.run_probe'`

- [ ] **Step 3: Implement check + probe**

```python
# src/gflow_cli/flow_selectors/check.py
"""Resolve registry candidates against DOM text and grade them.

Needs a headless chromium only for Playwright's selector engines; no network,
auth or credits. Asserts STRUCTURAL PRESENCE only — set_content() drops
external CSS/JS and page.content() omits shadow roots, so visible/enabled is
out of scope for both callers.
"""

from __future__ import annotations

from collections.abc import Sequence

from playwright.async_api import async_playwright

from gflow_cli.config import UiMode
from gflow_cli.flow_selectors.grading import Outcome, grade
from gflow_cli.flow_selectors.model import Selector


async def check_html(
    html: str, entries: Sequence[Selector], observed_mode: UiMode | None = None
) -> list[Outcome]:
    results: list[Outcome] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(html)
            for entry in entries:
                index: int | None = None
                count = 0
                for i, candidate in enumerate(entry.candidates):
                    count = await page.locator(candidate).count()
                    if count:
                        index = i
                        break
                results.append(grade(entry, index, count, observed_mode))
        finally:
            await browser.close()
    return results
```

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

from playwright.async_api import async_playwright

from gflow_cli.config import UiMode
from gflow_cli.flow_selectors import registry
from gflow_cli.flow_selectors.check import check_html
from gflow_cli.flow_selectors.grading import Grade, Outcome

SESSION_COOKIE = "__Secure-next-auth.session-token"
_LABEL = {
    Grade.HIT: "ok",
    Grade.FALLBACK: "FALLBACK",
    Grade.AMBIGUOUS: "AMBIGUOUS",
    Grade.MISS: "DRIFT",
    Grade.EXPECTED_ABSENT: "n/a",
    Grade.UNKNOWN_CONTEXT: "UNKNOWN",
}


def render_report(outcomes: list[Outcome]) -> str:
    """Publication-safe: keys and grades only, never selectors or DOM."""
    lines = ["| selector | result |", "| --- | --- |"]
    lines += [f"| `{o.selector_key}` | {_LABEL[o.grade]} |" for o in outcomes]
    bad = [o.selector_key for o in outcomes if o.is_failure]
    lines += ["", f"**{len(bad)} need attention**" if bad else "**0 need attention.**"]
    return "\n".join(lines)


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
            await page.goto(
                surface.url_template.format(locale="en", project_id=project_id),
                wait_until="domcontentloaded", timeout=90_000,
            )
            for _ in range(25):
                if await page.locator("i.google-symbols").count():
                    break
                await page.wait_for_timeout(1000)
            else:
                # Expired token or missing project — NOT drift. Never conflate them.
                print("::error::surface never hydrated (expired token or missing project)")
                return 2
            mode = (
                UiMode.CLASSIC
                if await page.locator(registry.by_key("editor.crop_control").candidates[0]).count()
                else UiMode.AGENTIC
            )
            html = await page.content()
        finally:
            await browser.close()

    outcomes = await check_html(html, registry.for_surface(surface_key), mode)
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
Expected: PASS (4 tests)

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
git add src/gflow_cli/flow_selectors/check.py scripts/probe .github/workflows/selector-probe.yml tests/
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
