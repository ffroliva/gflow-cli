# Selector Registry + Drift Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn gflow's scattered Flow DOM selectors into a structured, enumerable registry that a $0 probe can walk, so drift is *named* rather than inferred from a failing test.

**Architecture:** A pure-data registry (`Surface` + `Selector`) with a pure grading function. Capture (navigate → `page.content()`) is separated from check (`html + entries → HIT/FALLBACK/MISS`), so the same grader runs against a committed snapshot in CI and a fresh capture in the nightly probe.

**Tech Stack:** Python 3.11+, Playwright (already a dep), pytest, ruff, GitHub Actions.

**Spec:** [`docs/superpowers/specs/2026-08-21-selector-registry-design.md`](../specs/2026-08-21-selector-registry-design.md)

## Global Constraints

- **Windows dev:** use `.venv/Scripts/python.exe -m pytest`, never `uv run pytest` (broken here).
- **Never `-m pytest ... | tail`** — piping masks the exit code.
- **$0 invariant:** reach steps MUST be non-mutating and credit-free. Navigation and panel-opening qualify; submitting does not.
- **Locale-invariant selectors only** — Material Symbols ligatures, never display text. Locale is account-driven: `project_editor_url("en", …)` still served `/fx/pt/`.
- **Every DOM probe carries a positive control in the same run** and creates-or-verifies its own project. A stale project id renders an error shell (~441KB, 3 buttons, 0 icons) indistinguishable from an auth wall.
- **Secrets:** `__Secure-next-auth.session-token` only. Never log or publish a cookie value.
- **`Plan` is a NEW enum** (Free/Pro/Ultra). Do NOT name it `Tier` — `api/video.py:35` already defines `Tier` as video quality.
- CI lints `src` and `tests` only; `scripts/` is not in `ruff check src tests` scope but keep it clean anyway.

---

### Task 1: Registry types and the pure grader

**Files:**
- Create: `src/gflow_cli/ui/selectors/__init__.py`
- Create: `src/gflow_cli/ui/selectors/model.py`
- Create: `src/gflow_cli/ui/selectors/grading.py`
- Test: `tests/ui/selectors/test_model.py`, `tests/ui/selectors/test_grading.py`

**Interfaces:**
- Consumes: `gflow_cli.config.UiMode` (AUTO/CLASSIC/AGENTIC).
- Produces: `Surface`, `Selector`, `Plan`, `Grade`, `Outcome`, `grade(entry, resolved_index) -> Outcome`.

> **Deliberate narrowing of the spec.** §3.1 specifies `reach: Reach` — a URL builder
> *or* a parent surface plus a non-mutating action, because panels like the media
> picker are reached by clicking. This plan ships only URL-reachable surfaces, so
> `Surface` carries a plain `url_template`. `Reach` arrives with the first panel
> surface in the follow-up plan. Do not build the union type speculatively.

- [ ] **Step 1: Write the failing test for the model**

```python
# tests/ui/selectors/test_model.py
from __future__ import annotations

import pytest

from gflow_cli.config import UiMode
from gflow_cli.ui.selectors.model import Plan, Selector, Surface


def test_selector_requires_at_least_one_candidate() -> None:
    with pytest.raises(ValueError, match="at least one candidate"):
        Selector(key="editor.x", surface="editor", candidates=())


def test_selector_key_must_be_dotted_lowercase() -> None:
    """Keys are a public promise: they appear in exit-23 messages, canary
    reports and the env override. Enforce one shape so they stay scriptable."""
    with pytest.raises(ValueError, match="dotted lower_snake"):
        Selector(key="Editor Composer", surface="editor", candidates=("div",))


def test_selector_defaults_are_the_permissive_ones() -> None:
    sel = Selector(key="editor.composer.input", surface="editor", candidates=("div",))
    assert sel.mode is None          # present in every arm
    assert sel.min_plan is None      # present on every plan
    assert sel.required is True      # drift is exit 23 unless stated otherwise
    assert sel.features == ()


def test_surface_declares_the_modes_it_can_present() -> None:
    s = Surface(key="editor", url_template="/project/{project_id}",
                modes=(UiMode.CLASSIC, UiMode.AGENTIC))
    assert UiMode.CLASSIC in s.modes


def test_plan_is_ordered_so_min_plan_comparisons_work() -> None:
    assert Plan.FREE < Plan.PRO < Plan.ULTRA
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/selectors/test_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gflow_cli.ui.selectors'`

- [ ] **Step 3: Implement the model**

```python
# src/gflow_cli/ui/selectors/model.py
"""Structured inventory of the Flow DOM elements gflow depends on.

A selector without its page is unfindable, and a MISS without its mode is
unattributable — CROP_SELECTORS[0] legitimately misses on the agentic arm
because it IS the classic-mode indicator (factory.py:116). Context is data here,
not a naming convention.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum

from gflow_cli.config import UiMode

_SEG = r"[a-z0-9]+(?:_[a-z0-9]+)*"
_KEY_RE = re.compile(rf"^{_SEG}(?:\.{_SEG})+$")          # selectors: always dotted
_SURFACE_KEY_RE = re.compile(rf"^{_SEG}(?:\.{_SEG})*$")   # surfaces: dot optional


class Plan(IntEnum):
    """Flow subscription plan. Ordered so ``min_plan`` comparisons work.

    Deliberately NOT named ``Tier``: ``api/video.py`` already defines ``Tier``
    as a video-quality enum. #171 (UpscaleUnavailableError, exit 22) is the
    reason this exists — 4K upscale needs Ultra, so on a lesser plan the
    control is legitimately absent and must not read as drift.
    """

    FREE = 0
    PRO = 1
    ULTRA = 2


@dataclass(frozen=True)
class Surface:
    """A navigable UI context that selectors belong to."""

    key: str
    url_template: str
    modes: tuple[UiMode, ...] = ()

    def __post_init__(self) -> None:
        # Surface keys may be single-segment ("editor") or dotted
        # ("editor.media_picker"); selector keys must always be dotted.
        if not _SURFACE_KEY_RE.match(self.key):
            msg = f"surface key must be lower_snake, optionally dotted: {self.key!r}"
            raise ValueError(msg)


@dataclass(frozen=True)
class Selector:
    """One DOM dependency, with the context that makes a MISS interpretable."""

    key: str
    surface: str
    candidates: tuple[str, ...]
    mode: UiMode | None = None
    min_plan: Plan | None = None
    features: tuple[str, ...] = field(default_factory=tuple)
    required: bool = True
    note: str = ""

    def __post_init__(self) -> None:
        if not self.candidates:
            msg = f"{self.key}: needs at least one candidate"
            raise ValueError(msg)
        if not _KEY_RE.match(self.key):
            msg = f"selector key must be dotted lower_snake: {self.key!r}"
            raise ValueError(msg)
```

- [ ] **Step 4: Run the model tests**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/selectors/test_model.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Write the failing grader test**

```python
# tests/ui/selectors/test_grading.py
from __future__ import annotations

from gflow_cli.config import UiMode
from gflow_cli.ui.selectors.grading import Grade, grade
from gflow_cli.ui.selectors.model import Plan, Selector

SEL = Selector(key="editor.count_tabs", surface="editor",
               candidates=("[role=tab]:text-is('x2')", "[role=tab]:text-is('2x')"))


def test_first_candidate_resolving_is_a_hit() -> None:
    assert grade(SEL, resolved_index=0).grade is Grade.HIT


def test_later_candidate_resolving_is_fallback_not_failure() -> None:
    """#493's actual outcome: the scoped close missed, the unscoped fallback
    held. Reporting that as failure is how a canary trains red-blindness."""
    out = grade(SEL, resolved_index=1)
    assert out.grade is Grade.FALLBACK
    assert out.is_failure is False


def test_no_candidate_resolving_on_a_required_selector_is_drift() -> None:
    out = grade(SEL, resolved_index=None)
    assert out.grade is Grade.MISS
    assert out.is_failure is True


def test_optional_selector_missing_is_not_a_failure() -> None:
    optional = Selector(key="editor.hint", surface="editor",
                        candidates=("div",), required=False)
    assert grade(optional, resolved_index=None).is_failure is False


def test_wrong_mode_makes_a_miss_expected_not_drift() -> None:
    """The CROP_SELECTORS lesson: absent on the agentic arm means 'classic
    indicator', not 'Google moved it'."""
    classic_only = Selector(key="editor.crop", surface="editor",
                            candidates=("button",), mode=UiMode.CLASSIC)
    out = grade(classic_only, resolved_index=None, observed_mode=UiMode.AGENTIC)
    assert out.grade is Grade.EXPECTED_ABSENT
    assert out.is_failure is False


def test_plan_gated_selector_missing_below_min_plan_is_expected() -> None:
    ultra = Selector(key="editor.upscale_4k", surface="editor",
                     candidates=("button",), min_plan=Plan.ULTRA)
    out = grade(ultra, resolved_index=None, observed_plan=Plan.FREE)
    assert out.grade is Grade.EXPECTED_ABSENT


def test_plan_gated_selector_missing_at_or_above_min_plan_is_drift() -> None:
    ultra = Selector(key="editor.upscale_4k", surface="editor",
                     candidates=("button",), min_plan=Plan.ULTRA)
    assert grade(ultra, resolved_index=None, observed_plan=Plan.ULTRA).is_failure is True
```

- [ ] **Step 6: Run it to confirm it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/selectors/test_grading.py -v`
Expected: FAIL — `No module named 'gflow_cli.ui.selectors.grading'`

- [ ] **Step 7: Implement the grader**

```python
# src/gflow_cli/ui/selectors/grading.py
"""Pure grading of a probe result. No browser, no IO — the same function grades
a live capture and a committed snapshot, so the two layers cannot disagree."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from gflow_cli.config import UiMode
from gflow_cli.ui.selectors.model import Plan, Selector


class Grade(Enum):
    HIT = "hit"                        # candidates[0] resolved
    FALLBACK = "fallback"              # a later candidate resolved — warn, do not fail
    MISS = "miss"                      # nothing resolved and it should have
    EXPECTED_ABSENT = "expected_absent"  # mode/plan says it should not be here


@dataclass(frozen=True)
class Outcome:
    selector_key: str
    grade: Grade
    resolved_index: int | None

    @property
    def is_failure(self) -> bool:
        return self.grade is Grade.MISS


def grade(
    selector: Selector,
    resolved_index: int | None,
    observed_mode: UiMode | None = None,
    observed_plan: Plan | None = None,
) -> Outcome:
    """Grade one selector against which candidate (if any) resolved."""
    if resolved_index is not None:
        g = Grade.HIT if resolved_index == 0 else Grade.FALLBACK
        return Outcome(selector.key, g, resolved_index)

    if selector.mode is not None and observed_mode is not None and selector.mode != observed_mode:
        return Outcome(selector.key, Grade.EXPECTED_ABSENT, None)
    if (
        selector.min_plan is not None
        and observed_plan is not None
        and observed_plan < selector.min_plan
    ):
        return Outcome(selector.key, Grade.EXPECTED_ABSENT, None)
    if not selector.required:
        return Outcome(selector.key, Grade.EXPECTED_ABSENT, None)
    return Outcome(selector.key, Grade.MISS, None)
```

- [ ] **Step 8: Run both test files**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/selectors/ -v`
Expected: PASS (12 tests)

- [ ] **Step 9: Lint and commit**

```bash
.venv/Scripts/python.exe -m ruff check src/gflow_cli/ui/selectors tests/ui/selectors
.venv/Scripts/python.exe -m ruff format src/gflow_cli/ui/selectors tests/ui/selectors
git add src/gflow_cli/ui/selectors tests/ui/selectors
git commit -m "feat(selectors): registry model and pure grading function

A MISS is only interpretable with its context: CROP_SELECTORS[0] legitimately
misses on the agentic arm because it IS the classic-mode indicator. mode,
min_plan and ordered candidates make that attributable instead of ambiguous.

Refs #502"
```

---

### Task 2: Populate the registry with the drift-prone families

**Files:**
- Create: `src/gflow_cli/ui/selectors/registry.py`
- Test: `tests/ui/selectors/test_registry.py`

**Interfaces:**
- Consumes: `Surface`, `Selector`, `Plan` from Task 1.
- Produces: `SURFACES: dict[str, Surface]`, `SELECTORS: tuple[Selector, ...]`, `by_key(key) -> Selector`, `for_surface(surface_key, mode) -> tuple[Selector, ...]`.

Only the families with incident history land here. Everything else migrates in Task 6.

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/selectors/test_registry.py
from __future__ import annotations

import pytest

from gflow_cli.config import UiMode
from gflow_cli.ui.selectors import registry


def test_every_selector_points_at_a_declared_surface() -> None:
    for sel in registry.SELECTORS:
        assert sel.surface in registry.SURFACES, f"{sel.key} -> unknown surface {sel.surface}"


def test_keys_are_unique() -> None:
    keys = [s.key for s in registry.SELECTORS]
    assert len(keys) == len(set(keys))


def test_no_candidate_depends_on_display_text() -> None:
    """Locale is ACCOUNT-driven: project_editor_url("en", ...) still served
    /fx/pt/ in Portuguese. Text-matching selectors break for any non-English
    account, so candidates must key on Material Symbols ligatures or structure.
    ``:text-is('crop_16_9')`` is a LIGATURE (icon name), not display copy."""
    banned = ("Generate", "New project", "Sign in", "Create")
    for sel in registry.SELECTORS:
        for cand in sel.candidates:
            for word in banned:
                assert word not in cand, f"{sel.key} matches display text: {word!r}"


def test_incident_families_are_present() -> None:
    for key in (
        "editor.composer.input",
        "editor.composer.submit",
        "editor.agent_toggle",
        "editor.sidebar.close",
        "editor.crop_control",
    ):
        assert registry.by_key(key) is not None


def test_by_key_raises_for_unknown() -> None:
    with pytest.raises(KeyError):
        registry.by_key("editor.nope")


def test_for_surface_filters_by_mode() -> None:
    classic = registry.for_surface("editor", mode=UiMode.CLASSIC)
    keys = {s.key for s in classic}
    assert "editor.crop_control" in keys          # classic-only indicator
    agentic = {s.key for s in registry.for_surface("editor", mode=UiMode.AGENTIC)}
    assert "editor.crop_control" not in agentic


def test_sidebar_close_keeps_its_fallback_ordered() -> None:
    """#493: the edit_square-scoped close was the single point of failure; the
    unscoped fallback is what recovered it. Order encodes preference."""
    sel = registry.by_key("editor.sidebar.close")
    assert len(sel.candidates) >= 2
    assert "edit_square" in sel.candidates[0]
    assert "edit_square" not in sel.candidates[1]
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/selectors/test_registry.py -v`
Expected: FAIL — `No module named 'gflow_cli.ui.selectors.registry'`

- [ ] **Step 3: Implement the registry**

Copy the candidate strings verbatim from their current homes — do not retype them. Read `src/gflow_cli/api/transports/mode_control.py:43,49,61,79` and `ui_automation.py:198,209` first.

```python
# src/gflow_cli/ui/selectors/registry.py
"""The selectors gflow depends on, with the context that makes drift readable.

Scope: the families with incident history (#404, #493, #174, #313). The rest
migrate later — a partial registry that is USED beats a complete one that is not.
"""

from __future__ import annotations

from gflow_cli.api.transports import mode_control, ui_automation
from gflow_cli.config import UiMode
from gflow_cli.ui.selectors.model import Selector, Surface

SURFACES: dict[str, Surface] = {
    "editor": Surface(
        key="editor",
        url_template="https://labs.google/fx/{locale}/tools/flow/project/{project_id}",
        modes=(UiMode.CLASSIC, UiMode.AGENTIC),
    ),
}

SELECTORS: tuple[Selector, ...] = (
    Selector(
        key="editor.composer.input",
        surface="editor",
        candidates=tuple(ui_automation.PROMPT_INPUT_SELECTORS),
        features=("image", "video"),
        note="#493 — the expanded chat sidebar hid this entirely.",
    ),
    Selector(
        key="editor.composer.submit",
        surface="editor",
        candidates=tuple(ui_automation.SUBMIT_BUTTON_SELECTORS),
        features=("image", "video"),
    ),
    Selector(
        key="editor.agent_toggle",
        surface="editor",
        candidates=(mode_control.AGENT_TOGGLE_SELECTOR,),
        features=("image", "video"),
        note="#313 — agent settings panel became sticky.",
    ),
    Selector(
        key="editor.sidebar.close",
        surface="editor",
        candidates=(
            mode_control.SIDEBAR_CLOSE_SELECTOR,
            mode_control.SIDEBAR_CLOSE_FALLBACK_SELECTOR,
        ),
        note="#493 — the edit_square-scoped close was the SPOF; the unscoped "
        "fallback is the recovery. Order is preference, not decoration.",
    ),
    Selector(
        key="editor.crop_control",
        surface="editor",
        candidates=tuple(mode_control.CROP_SELECTORS),
        mode=UiMode.CLASSIC,
        note="Classic-mode INDICATOR (factory.py:116). Absent on the agentic "
        "arm by design — probing one of its six ratio variants and calling the "
        "miss 'drift' is the exact error this registry prevents.",
    ),
)

_BY_KEY = {s.key: s for s in SELECTORS}


def by_key(key: str) -> Selector:
    """Look up one selector. Raises KeyError for an unknown key."""
    return _BY_KEY[key]


def for_surface(surface_key: str, mode: UiMode | None = None) -> tuple[Selector, ...]:
    """Selectors expected on a surface, optionally narrowed to one arm."""
    return tuple(
        s
        for s in SELECTORS
        if s.surface == surface_key and (mode is None or s.mode is None or s.mode == mode)
    )
```

- [ ] **Step 4: Run the registry tests**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/selectors/test_registry.py -v`
Expected: PASS (7 tests).

If `test_no_candidate_depends_on_display_text` fails, check whether the match is a Material Symbols **ligature** or real display copy. `:text('arrow_forward')` in `SUBMIT_BUTTON_SELECTORS` is a ligature — the icon's name, identical in every locale — and is fine. Actual UI copy is not. If you hit real copy, that is a pre-existing locale bug: raise it rather than weakening the test.

- [ ] **Step 5: Commit**

```bash
.venv/Scripts/python.exe -m ruff check src/gflow_cli/ui/selectors tests/ui/selectors
git add src/gflow_cli/ui/selectors/registry.py tests/ui/selectors/test_registry.py
git commit -m "feat(selectors): register the drift-prone families

Imports the existing constants rather than copying their values, so the
registry cannot silently diverge from the code that uses them today.

Refs #404 #493 #313"
```

---

### Task 3: Offline check against a committed DOM snapshot

**Files:**
- Create: `src/gflow_cli/ui/selectors/check.py`
- Create: `tests/ui/selectors/fixtures/editor_classic.html` (generated in Task 4; use a hand-written stub until then)
- Test: `tests/ui/selectors/test_check.py`

**Interfaces:**
- Consumes: `registry`, `grade`, `Outcome`.
- Produces: `async check_html(html: str, entries: Sequence[Selector], **ctx) -> list[Outcome]`.

Verified mechanism: Playwright's `set_content()` resolves `:has()` / `:text-is()` offline — no network, no auth, no credits.

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/selectors/test_check.py
from __future__ import annotations

import pytest

from gflow_cli.ui.selectors.check import check_html
from gflow_cli.ui.selectors.grading import Grade
from gflow_cli.ui.selectors.model import Selector

HTML = """<html><body>
  <div role="textbox" data-slate-editor="true">prompt</div>
  <button><i class="google-symbols">close</i></button>
</body></html>"""


async def test_resolving_first_candidate_reports_hit() -> None:
    sel = Selector(key="editor.composer.input", surface="editor",
                   candidates=('div[role="textbox"][data-slate-editor="true"]',))
    (out,) = await check_html(HTML, [sel])
    assert out.grade is Grade.HIT


async def test_falls_through_to_a_later_candidate() -> None:
    sel = Selector(key="editor.sidebar.close", surface="editor",
                   candidates=("button.does-not-exist",
                               "button:has(i.google-symbols:text-is('close'))"))
    (out,) = await check_html(HTML, [sel])
    assert out.grade is Grade.FALLBACK
    assert out.resolved_index == 1


async def test_absent_required_selector_reports_miss() -> None:
    sel = Selector(key="editor.gone", surface="editor", candidates=("#nope",))
    (out,) = await check_html(HTML, [sel])
    assert out.grade is Grade.MISS
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/selectors/test_check.py -v`
Expected: FAIL — `No module named 'gflow_cli.ui.selectors.check'`

- [ ] **Step 3: Implement the checker**

```python
# src/gflow_cli/ui/selectors/check.py
"""Grade selectors against DOM text. No network, no auth, no credits.

A headless chromium is needed only to run Playwright's selector engines
(``:has()``, ``:text-is()``) — verified working against static HTML via
``set_content()``. This is the same function the live probe uses, so an
offline snapshot check and a live capture check can never disagree.

Limitation: ``set_content()`` drops external CSS/JS, so visibility and
enabled-state are NOT reliable here. This layer asserts structural presence;
the live probe additionally asserts visible/enabled.
"""

from __future__ import annotations

from collections.abc import Sequence

from playwright.async_api import async_playwright

from gflow_cli.config import UiMode
from gflow_cli.ui.selectors.grading import Outcome, grade
from gflow_cli.ui.selectors.model import Plan, Selector


async def check_html(
    html: str,
    entries: Sequence[Selector],
    observed_mode: UiMode | None = None,
    observed_plan: Plan | None = None,
) -> list[Outcome]:
    """Resolve each entry's candidates against ``html`` and grade the result."""
    results: list[Outcome] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(html)
            for entry in entries:
                index: int | None = None
                for i, candidate in enumerate(entry.candidates):
                    if await page.locator(candidate).count():
                        index = i
                        break
                results.append(grade(entry, index, observed_mode, observed_plan))
        finally:
            await browser.close()
    return results
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/selectors/test_check.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/ui/selectors/check.py tests/ui/selectors/test_check.py
git commit -m "feat(selectors): offline DOM check, no network or credentials

Same grader for snapshots and live captures, so the layers cannot drift apart.
set_content() loses CSS/JS, so this asserts structural presence only."
```

---

### Task 4: Capture command

**Files:**
- Create: `scripts/probe/capture_surface.py`
- Test: `tests/scripts/test_capture_surface.py`

**Interfaces:**
- Consumes: `registry.SURFACES`, `FlowApiClient`.
- Produces: `scrub(html) -> str`; CLI writing `tests/ui/selectors/fixtures/<surface>_<mode>.html`.

- [ ] **Step 1: Write the failing scrub test**

```python
# tests/scripts/test_capture_surface.py
from __future__ import annotations

from scripts.probe.capture_surface import scrub


def test_scrub_removes_uuids() -> None:
    out = scrub('<a href="/project/edf7fefa-c143-436b-b224-b19ec238e753">x</a>')
    assert "edf7fefa" not in out
    assert "REDACTED-UUID" in out


def test_scrub_removes_emails() -> None:
    assert "@" not in scrub("<span>someone@example.com</span>")


def test_scrub_removes_signed_urls() -> None:
    html = '<img src="https://cdn.example/x.png?Expires=1&Signature=abc">'
    assert "Signature=abc" not in scrub(html)


def test_scrub_is_idempotent() -> None:
    once = scrub('<a href="/project/edf7fefa-c143-436b-b224-b19ec238e753">x</a>')
    assert scrub(once) == once
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/scripts/test_capture_surface.py -v`
Expected: FAIL — `No module named 'scripts.probe'`

- [ ] **Step 3: Implement capture + scrub**

```python
# scripts/probe/capture_surface.py
"""Capture a Flow surface's DOM for offline selector checking. $0 — navigation
and DOM read only; never submits.

Carries a positive control by construction: it CREATES its project rather than
trusting a stored id. A stale project renders an error shell (~441KB, 3 buttons,
0 icons) that is indistinguishable from an auth wall, and that confound voided
three spike runs before it was caught.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

from gflow_cli.api.client import FlowApiClient
from gflow_cli.auth import profile_dir as resolve_profile
from gflow_cli.ui.selectors import registry

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "ui" / "selectors" / "fixtures"

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_SIGNED = re.compile(r"(Signature|X-Goog-Signature|Expires)=[^&\"'\s]+", re.I)


def scrub(html: str) -> str:
    """Strip identifiers before a DOM snapshot enters a public repo."""
    html = _UUID.sub("REDACTED-UUID", html)
    html = _EMAIL.sub("REDACTED-EMAIL", html)
    return _SIGNED.sub(r"\1=REDACTED", html)


async def capture(profile: str, surface_key: str) -> Path:
    surface = registry.SURFACES[surface_key]
    async with FlowApiClient(profile_dir=resolve_profile(profile), headless=True) as client:
        project = await client.create_project(title="selector-probe capture")
        page = client._page or client._context.pages[0]  # noqa: SLF001
        url = surface.url_template.format(locale="en", project_id=project.project_id)
        await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        for _ in range(25):
            if await page.locator("i.google-symbols").count():
                break
            await page.wait_for_timeout(1000)
        else:
            msg = "surface never hydrated — capture aborted rather than saving an error shell"
            raise SystemExit(msg)
        html = scrub(await page.content())

    FIXTURES.mkdir(parents=True, exist_ok=True)
    out = FIXTURES / f"{surface_key}.html"
    out.write_text(html, encoding="utf-8")
    print(f"captured {len(html):,} bytes -> {out}")
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", required=True)
    p.add_argument("--surface", default="editor")
    args = p.parse_args()
    asyncio.run(capture(args.profile, args.surface))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the scrub tests**

Run: `.venv/Scripts/python.exe -m pytest tests/scripts/test_capture_surface.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Capture the real fixture and add the snapshot test**

```bash
.venv/Scripts/python.exe scripts/probe/capture_surface.py --profile denon82 --surface editor
```

```python
# append to tests/ui/selectors/test_check.py
from pathlib import Path
from gflow_cli.ui.selectors import registry

FIXTURE = Path(__file__).parent / "fixtures" / "editor.html"


async def test_committed_snapshot_still_satisfies_every_editor_selector() -> None:
    """Catches OUR regressions on every PR. It can never detect Google changing
    the page — the snapshot is frozen. That is the live probe's job."""
    html = FIXTURE.read_text(encoding="utf-8")
    outcomes = await check_html(html, registry.for_surface("editor"))
    failures = [o.selector_key for o in outcomes if o.is_failure]
    assert not failures, f"snapshot no longer satisfies: {failures}"
```

- [ ] **Step 6: Verify the fixture is clean before committing it**

Run: `grep -ciE "[0-9a-f]{8}-[0-9a-f]{4}|@[a-z-]+\.[a-z]" tests/ui/selectors/fixtures/editor.html`
Expected: `0`. Non-zero means `scrub()` missed a pattern — fix `scrub`, re-capture, re-check.

- [ ] **Step 7: Commit**

```bash
git add scripts/probe tests/scripts/test_capture_surface.py tests/ui/selectors/
git commit -m "feat(probe): DOM capture with scrubbing, plus the snapshot check

Capture creates its own project: a stale id renders an error shell that reads
exactly like an auth wall, which voided three spike runs before it was caught."
```

---

### Task 5: CI probe workflow

**Files:**
- Create: `scripts/probe/run_probe.py`
- Create: `.github/workflows/selector-probe.yml`
- Test: `tests/scripts/test_run_probe.py`

**Interfaces:**
- Consumes: `check_html`, `registry`, `scrub`.
- Produces: `render_report(outcomes) -> str`; exit 0 clean / 1 drift / 2 infrastructure.

Evidence: a bundled headless chromium carrying only `__Secure-next-auth.session-token` (1088 chars, 30-day expiry) renders the editor identically to a full real-Chrome profile — six-arm matrix, all identical.

- [ ] **Step 1: Write the failing report test**

```python
# tests/scripts/test_run_probe.py
from __future__ import annotations

from gflow_cli.ui.selectors.grading import Grade, Outcome
from scripts.probe.run_probe import render_report


def test_report_names_the_drifted_selector() -> None:
    body = render_report([Outcome("editor.count_tabs", Grade.MISS, None)])
    assert "editor.count_tabs" in body
    assert "DRIFT" in body


def test_fallback_is_reported_as_a_warning_not_drift() -> None:
    body = render_report([Outcome("editor.sidebar.close", Grade.FALLBACK, 1)])
    assert "FALLBACK" in body
    assert "DRIFT" not in body


def test_report_never_contains_a_raw_selector() -> None:
    """Keys are publication-safe; raw selectors and paths are not."""
    body = render_report([Outcome("editor.composer.input", Grade.MISS, None)])
    assert "div[role=" not in body
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/scripts/test_run_probe.py -v`
Expected: FAIL — `No module named 'scripts.probe.run_probe'`

- [ ] **Step 3: Implement the probe runner**

```python
# scripts/probe/run_probe.py
"""Probe live Flow for selector drift. $0 — navigate and read only.

Auth is ONE cookie: __Secure-next-auth.session-token (30-day life, scoped to
labs.google). Measured: a bundled headless chromium with only that cookie
renders the editor identically to a full real-Chrome profile.

Exit: 0 no drift · 1 drift found · 2 infrastructure failure (never confuse them).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from playwright.async_api import async_playwright

from gflow_cli.ui.selectors import registry
from gflow_cli.ui.selectors.check import check_html
from gflow_cli.ui.selectors.grading import Grade, Outcome

SESSION_COOKIE = "__Secure-next-auth.session-token"
_LABEL = {
    Grade.HIT: "ok",
    Grade.FALLBACK: "FALLBACK",
    Grade.MISS: "DRIFT",
    Grade.EXPECTED_ABSENT: "n/a",
}


def render_report(outcomes: list[Outcome]) -> str:
    """Publication-safe: keys only, never raw selectors or paths."""
    lines = ["| selector | result |", "| --- | --- |"]
    lines += [f"| `{o.selector_key}` | {_LABEL[o.grade]} |" for o in outcomes]
    drift = [o.selector_key for o in outcomes if o.is_failure]
    lines += ["", f"**{len(drift)} drifted**" if drift else "**No drift.**"]
    return "\n".join(lines)


async def run(token: str, project_id: str, surface_key: str) -> int:
    surface = registry.SURFACES[surface_key]
    url = surface.url_template.format(locale="en", project_id=project_id)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context()
            await ctx.add_cookies([{
                "name": SESSION_COOKIE, "value": token,
                "domain": "labs.google", "path": "/",
                "httpOnly": True, "secure": True, "sameSite": "Lax",
            }])
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            for _ in range(25):
                if await page.locator("i.google-symbols").count():
                    break
                await page.wait_for_timeout(1000)
            else:
                # Positive control: an un-hydrated surface means the token expired
                # or the project is gone. That is NOT drift — never report it as such.
                print("::error::surface never hydrated (expired token or missing project)")
                return 2
            html = await page.content()
        finally:
            await browser.close()

    outcomes = await check_html(html, registry.for_surface(surface_key))
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

Run: `.venv/Scripts/python.exe -m pytest tests/scripts/test_run_probe.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Add the workflow**

```yaml
# .github/workflows/selector-probe.yml
name: Selector drift probe

on:
  schedule:
    - cron: "0 5 * * *"      # 05:00 UTC, before the maintainer's local canary
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

Start on `schedule` + `workflow_dispatch` only. Do **not** add `pull_request` until R1 (datacenter-IP behaviour) is answered by a first real run.

- [ ] **Step 6: Commit**

```bash
git add scripts/probe/run_probe.py .github/workflows/selector-probe.yml tests/scripts/test_run_probe.py
git commit -m "feat(probe): CI selector-drift probe on a single session cookie

Exit 2 (infrastructure) is kept distinct from exit 1 (drift): an expired token
or a deleted project must never be reported as Google changing the page."
```

---

### Task 6: AST guardrail, error key, and env override

**Files:**
- Create: `tests/api/transports/test_selectors_only_in_registry.py`
- Modify: `src/gflow_cli/errors.py` (`UiSelectorDriftError`)
- Create: `src/gflow_cli/ui/selectors/overrides.py`
- Test: `tests/ui/selectors/test_overrides.py`

**Interfaces:**
- Consumes: `registry`, `Selector`.
- Produces: `apply_overrides(entries) -> tuple[Selector, ...]`; `UiSelectorDriftError(selector_key=...)`.

- [ ] **Step 1: Write the failing override test**

```python
# tests/ui/selectors/test_overrides.py
from __future__ import annotations

import pytest

from gflow_cli.ui.selectors.model import Selector
from gflow_cli.ui.selectors.overrides import apply_overrides

BASE = (Selector(key="editor.count_tabs", surface="editor", candidates=("[role=tab]",)),)


def test_no_env_leaves_entries_untouched(monkeypatch) -> None:
    monkeypatch.delenv("GFLOW_CLI_SELECTOR_OVERRIDE", raising=False)
    assert apply_overrides(BASE) == BASE


def test_override_replaces_the_candidate_list(monkeypatch) -> None:
    """A user hit by a #404-class rename patches it and keeps working, instead
    of waiting for a release."""
    monkeypatch.setenv("GFLOW_CLI_SELECTOR_OVERRIDE", "editor.count_tabs=[role=tab].new")
    (sel,) = apply_overrides(BASE)
    assert sel.candidates == ("[role=tab].new",)


def test_unknown_key_is_rejected_loudly(monkeypatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_SELECTOR_OVERRIDE", "editor.nope=div")
    with pytest.raises(ValueError, match="unknown selector key"):
        apply_overrides(BASE)
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/selectors/test_overrides.py -v`
Expected: FAIL — `No module named 'gflow_cli.ui.selectors.overrides'`

- [ ] **Step 3: Implement overrides**

```python
# src/gflow_cli/ui/selectors/overrides.py
"""Operator escape hatch: patch a drifted selector without waiting for a release.

    GFLOW_CLI_SELECTOR_OVERRIDE='editor.count_tabs=<css>;editor.composer.input=<css>'

Keys are a public promise — renaming one is a breaking change.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Sequence

from gflow_cli.ui.selectors.model import Selector

ENV_VAR = "GFLOW_CLI_SELECTOR_OVERRIDE"


def _parse(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in raw.split(";"):
        if not chunk.strip():
            continue
        key, sep, value = chunk.partition("=")
        if not sep or not value.strip():
            msg = f"malformed {ENV_VAR} entry: {chunk!r} (expected key=selector)"
            raise ValueError(msg)
        out[key.strip()] = value.strip()
    return out


def apply_overrides(entries: Sequence[Selector]) -> tuple[Selector, ...]:
    """Return entries with any env-supplied candidates substituted."""
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        return tuple(entries)
    wanted = _parse(raw)
    known = {e.key for e in entries}
    unknown = sorted(set(wanted) - known)
    if unknown:
        msg = f"{ENV_VAR}: unknown selector key(s) {unknown}"
        raise ValueError(msg)
    return tuple(
        dataclasses.replace(e, candidates=(wanted[e.key],)) if e.key in wanted else e
        for e in entries
    )
```

- [ ] **Step 4: Run the override tests**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/selectors/test_overrides.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Add `selector_key` to the drift error**

`UiSelectorDriftError` is an RFC 9457 Problem Details class (`errors.py:70`): class-level identity, instance-level extensions. Add `selector_key` as an extension alongside `route`, so exit 23 names what moved.

```python
# in src/gflow_cli/errors.py, inside UiSelectorDriftError
    selector_key: str | None = None
```

Add a test in `tests/test_error_contract.py`:

```python
def test_ui_selector_drift_carries_a_registry_key() -> None:
    err = UiSelectorDriftError(detail="probe missed", selector_key="editor.count_tabs")
    assert err.selector_key == "editor.count_tabs"
    assert "editor.count_tabs" not in str(err.to_problem_details().get("detail", ""))
```

- [ ] **Step 6: Add the AST guardrail**

```python
# tests/api/transports/test_selectors_only_in_registry.py
"""Guard: registered selector strings live only in the registry.

Sibling of test_selector_symmetry.py, which already locks the agentic
indicators to one source. This widens the same principle: once a selector is in
the registry, no transport may carry its literal, or the two silently diverge —
which is exactly what happened to the agentic probes before #183.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from gflow_cli.ui.selectors import registry

SRC = Path(__file__).resolve().parents[3] / "src" / "gflow_cli"
REGISTRY_DIR = SRC / "ui" / "selectors"
# mode_control/ui_automation still DEFINE the constants the registry imports;
# they are the migration source and are exempt until Task 7 inverts the flow.
EXEMPT = {
    SRC / "api" / "transports" / "mode_control.py",
    SRC / "api" / "transports" / "ui_automation.py",
}

pytestmark = pytest.mark.repo_lint


def _registered_literals() -> set[str]:
    return {c for s in registry.SELECTORS for c in s.candidates}


def test_no_module_outside_the_registry_hardcodes_a_registered_selector() -> None:
    registered = _registered_literals()
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if path in EXEMPT or REGISTRY_DIR in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in registered:
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert not offenders, (
        "registered selectors must come from the registry, not literals: " + ", ".join(offenders)
    )
```

- [ ] **Step 7: Run the whole selector suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/selectors tests/scripts tests/api/transports/test_selectors_only_in_registry.py -v`
Expected: PASS

- [ ] **Step 8: Full gate and commit**

```bash
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m ruff format --check src tests
.venv/Scripts/python.exe scripts/ci/check_repo_hygiene.py
.venv/Scripts/python.exe scripts/ci/check_doc_links.py
git add -A
git commit -m "feat(selectors): AST guardrail, drift-error key, and env override

Users hit by a rename can now patch it via GFLOW_CLI_SELECTOR_OVERRIDE instead
of waiting for a release, and exit 23 names the registry key that moved."
```

---

## Deferred to a follow-up plan

- Migrating the remaining ~35 selectors and **inverting** the import direction so `mode_control`/`ui_automation` read *from* the registry (removes the `EXEMPT` set in Task 6).
- Additional surfaces: `editor.media_picker`, `character_editor`, timeline.
- Splitting `ui_automation.py` (3,557 lines) and `ui_automation_video.py` (3,814).
- Wiring the nightly canary to publish CI-token expiry (spec R2).

## Open question for the first real run

**R1 — datacenter IP.** Every measurement came from a residential connection. No local test can predict how Google treats a GitHub runner. Run the workflow once via `workflow_dispatch` and read the result before considering a `pull_request` trigger.
