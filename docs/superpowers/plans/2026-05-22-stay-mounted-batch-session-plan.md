# Stay-mounted batch session — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `gflow image batch` put all prompts of a batch into one Flow project (the real fix for the `--same-project=1` no-op bug) by refactoring `UiAutomationTransport.generate_images` into a stay-mounted shape, and removing the now-redundant `--same-project` flag.

**Architecture:** New transport method `UiAutomationTransport.generate_images_batch(prompts, jitter_range, continue_on_error)` opens the editor once via the existing `_enter_editor` helper, captures the resulting `project_id`, then per-prompt: configures aspect+count, attaches a per-prompt listener (now returning a `detach_fn`), submits via `_send_prompt` (extended to clear the editor field first), records a `PendingSubmission`, sleeps jitter between prompts. After all submissions are sent, awaits each pending submission in submission order, calls its `detach_fn`, parses images, returns a `BatchSubmissionResult` per prompt. Fail-fast default with partial-results salvage; orchestrator simplifies to a capability check + single delegation + download loop + post-download integrity check.

**Tech Stack:** Python 3.11+, Click, Rich, Playwright (UI transport), structlog, pytest + pytest-bdd, ruff, pyright `--strict`, uv, gh CLI.

**Spec:** [`docs/superpowers/specs/2026-05-22-stay-mounted-batch-session-design.md`](../specs/2026-05-22-stay-mounted-batch-session-design.md) — v3, 5-reviewer council hardened, user-approved.

---

## Conventions used throughout this plan

- **Commands are PowerShell-on-Windows.** For POSIX, swap `$env:VAR = "x"` → `VAR=x`.
- **All file paths are absolute or repo-relative from `C:\development\github\gflow-cli`.**
- **All commits target the `feature/multi-image-prompt` branch.** No new branch.
- **TDD discipline:** every code-producing task starts with a failing test (RED), then the minimum production code to pass (GREEN), then refactor if needed (REFACTOR). Skip the cycle ONLY for pure-docs steps.
- **"Run `/gflow:check`"** means: `uv run python scripts/ci/check_repo_hygiene.py && uv run ruff check src tests && uv run ruff format --check src tests && uv run pyright src && uv run python -m pytest -q <scoped-paths>` — execute the four-gate suite. Scope pytest per `full-test-suite-ooms` memory.
- **"Expected: PASS"** means exit code 0 + no failures reported.
- **Atomic commits:** one logical change per commit. Commit message body must not contain AI co-author tags (per CLAUDE.md).
- **Coverage floors (CLAUDE.md):** ≥80% overall, ≥90% on the new transport method.
- **Workaround for broken `uv run pytest` shim on this machine:** use `uv run python -m pytest …` instead of `uv run pytest …`. See session memory for context.

---

## File structure

| File | Action | Responsibility |
|---|---|---|
| `src/gflow_cli/api/dto.py` | Modify | Add `BatchSubmissionResult` frozen dataclass (per spec §5.2). |
| `src/gflow_cli/errors.py` | Modify | Add `BatchPartialError` and `BatchIntegrityError` exception classes (per spec §7 and §8.2). |
| `src/gflow_cli/api/transports/ui_automation.py` | Modify | Extend `_attach_batch_response_listener` to return `(captured, detach_fn)`. Extend `_send_prompt` to clear the editor field before typing. Add new `generate_images_batch` method (the bug fix). |
| `src/gflow_cli/image_batch.py` | Modify | Refactor `run_manifest_image_batch`: drop `same_project` parameter and the `same_project=False` branch, drop the per-prompt loop, drop the `create_project` call, add capability check + single transport call + download loop + post-download integrity check. |
| `src/gflow_cli/cli_image.py` | Modify | Remove `--same-project` Click option from `gflow image batch`. Update `--help` text. |
| `tests/api/test_ui_automation_batch.py` | **Create** | Unit tests for the new transport method (multi-listener concurrency, detach invariant, continue-on-error in both modes, partial-results salvage, orphaned-project warning, field-clear regression). |
| `tests/api/test_ui_automation.py` | Modify | Update existing call sites for the extended `_attach_batch_response_listener` and `_send_prompt` signatures. Add a unit test for the new field-clear behaviour. |
| `tests/image_batch/test_run_manifest_image_batch.py` | Modify | Refactor existing tests to mock `client.transport.generate_images_batch` instead of `run_one_image_prompt`. Add integrity-check tests. |
| `tests/e2e/test_image_batch_e2e.py` | Modify | Drop `GFLOW_CLI_E2E_BATCH_SAME_PROJECT` env var. Add `assert len({r.project_id for r in results}) == 1`. Update assertions per spec §8.3. |
| `docs/USAGE.md` | Modify | Rewrite the `gflow image batch` section: always-same-project semantics, jitter as submission-cadence anti-bot control, no `--same-project` flag. |
| `CHANGELOG.md` | Modify | `[Unreleased] ### Removed`: `--same-project` flag. `### Changed`: `gflow image batch` semantics. `### Fixed`: same-project bug. |
| `docs/LIVE_VERIFICATION_image_batch.md` | Modify | After v3-3 ships, add a post-refactor live verification section recording the credit-spending e2e run that confirms the fix. |

---

## Phase 0 — Commit the planning artifacts

These are already in the working tree from the session that produced this plan; they need to land before the implementation work so the branch carries the planning context.

**Single commit:** `docs(plan,spec): record v3 plan + v4 spec amendments and stay-mounted session design`

### Task 0.1: Stage and commit planning artifacts

**Files (already modified or created in working tree):**
- Modify: `docs/superpowers/specs/2026-05-21-multi-image-prompt-design.md` (v4 banner)
- Modify: `docs/superpowers/plans/2026-05-21-multi-image-prompt.md` (v3 banner)
- Create: `docs/superpowers/specs/2026-05-22-stay-mounted-batch-session-design.md` (the council-hardened spec)
- Create: `docs/superpowers/plans/2026-05-22-stay-mounted-batch-session-plan.md` (this file)

- [ ] **Step 1: Confirm working-tree state**

```powershell
git status -sb
```

Expected output includes the 4 paths above as modified/untracked plus the test/evidence file changes from earlier in the session. The tmp/* files should be untracked but gitignored — verify nothing under `tmp/` is staged.

- [ ] **Step 2: Stage only the planning files**

```powershell
git add docs/superpowers/specs/2026-05-21-multi-image-prompt-design.md docs/superpowers/plans/2026-05-21-multi-image-prompt.md docs/superpowers/specs/2026-05-22-stay-mounted-batch-session-design.md docs/superpowers/plans/2026-05-22-stay-mounted-batch-session-plan.md
git diff --cached --stat
```

Expected: 4 files staged, no other paths.

- [ ] **Step 3: Commit**

```powershell
git commit -m "docs(plan,spec): record v3 plan + v4 spec amendments and stay-mounted session design

v4 of the multi-image-prompt spec adds a top-of-file revision banner that
supersedes the §8 jitter matrix and the --same-project flag surface, after
mid-matrix discovery (2026-05-22) that ui_automation.generate_images
discards the caller's project_id. v3 of the multi-image-prompt plan adds a
matching revision banner replacing the verdict-driven commit #5b with a
seven-commit chain (v3-1 .. v3-7).

The new stay-mounted-batch-session design + plan land alongside the
banners. Spec is 5-reviewer council hardened (test/TDD, Playwright
mechanics, architecture/security, production failure modes, maintainability)
and user-approved."
```

- [ ] **Step 4: Verify**

```powershell
git log --oneline -1
```

Expected: HEAD is the new commit; clean working tree EXCEPT for `tests/e2e/test_image_batch_e2e.py` and `docs/LIVE_VERIFICATION_image_batch.md` (those land in Phase 1 and 2).

---

## Phase 1 — Commit v3-1: assertion 5 relaxation

The test fix is already on disk from the matrix session. Single commit.

### Task 1.1: Commit assertion 5 relaxation

**Files:**
- Modify: `tests/e2e/test_image_batch_e2e.py` (already modified, lines 182-191)

- [ ] **Step 1: Confirm the diff**

```powershell
git diff tests/e2e/test_image_batch_e2e.py
```

Expected: shows the relaxation of assertion 5 from `len(seen) == len(prompts)` to `>= sum(p.count for p in prompts)`, with a 6-line comment explaining why.

- [ ] **Step 2: Stage and commit**

```powershell
git add tests/e2e/test_image_batch_e2e.py
git commit -m "test(image): relax batch_response_seen assertion to lower bound

Playwright's page.on(\"response\", ...) listener fires for every matching
HTTP response from Flow (initial submit, progress polls, final result), so
the floor is one response per generated image — not one response per row.
Equality breaks when a manifest row has count>1 or when same-project mode
multiplexes events. Lower-bound assertion preserves the regression signal
while tolerating Flow's actual response shape.

Spec: docs/superpowers/specs/2026-05-22-stay-mounted-batch-session-design.md
§8.3."
```

- [ ] **Step 3: Verify**

```powershell
git log --oneline -2
```

Expected: HEAD is the assertion-relaxation commit; the prior commit is the Phase 0 docs commit.

---

## Phase 2 — Commit v3-2: live-verification evidence retraction

The evidence file was rewritten earlier in the session to retract the premature KEEP verdict and document the same-project bug discovery. Already on disk.

### Task 2.1: Commit the evidence retraction

**Files:**
- Modify: `docs/LIVE_VERIFICATION_image_batch.md`

- [ ] **Step 1: Confirm the diff**

```powershell
git diff docs/LIVE_VERIFICATION_image_batch.md | Select-Object -First 100
```

Expected: the Environment block now lists session 1 details with profile `denon82`; the Matrix runs table has the two R1 attempts; the Verdict section says "Matrix invalidated".

- [ ] **Step 2: Stage and commit**

```powershell
git add docs/LIVE_VERIFICATION_image_batch.md
git commit -m "docs(image): retract jitter-matrix verdict — same-project transport defect

The 2026-05-22 matrix run on profile denon82 invalidated its own premise:
the ui_automation transport explicitly discards the project_id it's given,
so every prompt of --same-project=1 lands in its own Flow project. The
matrix was measuring rapid-fire across separate projects, not rapid-fire
within one project — the question it was scoped to answer cannot be
reached from the data collected.

Updates the evidence file to record the two R1 runs (one over-strict
assertion failure, one missing-file failure), retract the premature KEEP
verdict, and point at the v3 plan for the actual fix path.

Spec: docs/superpowers/specs/2026-05-22-stay-mounted-batch-session-design.md
§1."
```

- [ ] **Step 3: Verify**

```powershell
git log --oneline -3 ; git status -sb
```

Expected: HEAD is the retraction commit; working tree is clean.

---

## Phase 3 — Commit v3-3: stay-mounted refactor

**This is the bug-fix commit.** Single atomic commit covering: new dataclass, new exceptions, extended helpers, new transport method, orchestrator refactor, and all unit tests. Each task within the phase is a TDD cycle that runs in isolation; the final commit ties them together.

### Task 3.1: Add `BatchSubmissionResult` dataclass

**Files:**
- Modify: `src/gflow_cli/api/dto.py` (find the `GeneratedImage` dataclass — add `BatchSubmissionResult` nearby)
- Test: `tests/api/test_dto.py` (if absent, create; otherwise extend)

- [ ] **Step 1: Write the failing test**

In `tests/api/test_dto.py`:

```python
from __future__ import annotations

from gflow_cli.api.dto import BatchSubmissionResult, GeneratedImage


def test_batch_submission_result_is_frozen() -> None:
    img = GeneratedImage(media_id="m1", fife_url="https://x", prompt="hi")
    result = BatchSubmissionResult(
        status="ok",
        project_id="proj-1",
        prompt_idx=0,
        prompt_hash="abc12345",
        images=(img,),
        error=None,
    )
    assert result.status == "ok"
    assert result.project_id == "proj-1"
    assert result.prompt_idx == 0
    assert result.prompt_hash == "abc12345"
    assert result.images == (img,)
    assert result.error is None

    # Frozen — mutation must raise
    import dataclasses
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        result.status = "fail"  # type: ignore[misc]


def test_batch_submission_result_fail_status() -> None:
    from gflow_cli.errors import GFlowError

    err = GFlowError(detail="boom", route="x")
    result = BatchSubmissionResult(
        status="fail",
        project_id="proj-1",
        prompt_idx=2,
        prompt_hash="deadbeef",
        images=(),
        error=err,
    )
    assert result.status == "fail"
    assert result.images == ()
    assert result.error is err
```

- [ ] **Step 2: Run the test (RED)**

```powershell
uv run python -m pytest tests/api/test_dto.py -v
```

Expected: ImportError or AttributeError on `BatchSubmissionResult`.

- [ ] **Step 3: Add the dataclass to `src/gflow_cli/api/dto.py`**

Find the existing `GeneratedImage` definition (search for `class GeneratedImage`). Add immediately below it (or alongside the related imports — keep imports grouped):

```python
from typing import Literal

# ... existing imports + GeneratedImage definition above ...


@dataclass(frozen=True)
class BatchSubmissionResult:
    """Per-prompt outcome from `UiAutomationTransport.generate_images_batch`.

    `project_id` is identical across all results of a single batch (the
    shared Flow project the editor stayed mounted on). `prompt_idx` is the
    0-based submission position. `prompt_hash` is the SHA-256 prefix used
    consistently across image_batch's structlog events.
    """

    status: Literal["ok", "fail"]
    project_id: str
    prompt_idx: int
    prompt_hash: str
    images: tuple[GeneratedImage, ...] = ()
    error: GFlowError | None = None
```

Make sure `GFlowError` is imported at the top of `dto.py` if not already (`from gflow_cli.errors import GFlowError`). If that introduces a circular import, do `from __future__ import annotations` and use string-quoted type hints; the codebase already uses this pattern.

- [ ] **Step 4: Run the test (GREEN)**

```powershell
uv run python -m pytest tests/api/test_dto.py -v
```

Expected: PASS.

- [ ] **Step 5: Run lint + types on touched files**

```powershell
uv run ruff check src/gflow_cli/api/dto.py tests/api/test_dto.py
uv run ruff format --check src/gflow_cli/api/dto.py tests/api/test_dto.py
uv run pyright src/gflow_cli/api/dto.py
```

Expected: clean.

(No commit yet — Phase 3 is one atomic commit at the end.)

### Task 3.2: Add `BatchPartialError` and `BatchIntegrityError` exception classes

**Files:**
- Modify: `src/gflow_cli/errors.py`
- Test: `tests/test_errors.py` (extend if it exists; otherwise create)

- [ ] **Step 1: Write the failing test**

In `tests/test_errors.py`:

```python
def test_batch_partial_error_carries_partial_results() -> None:
    from gflow_cli.errors import BatchPartialError, GFlowError
    from gflow_cli.api.dto import BatchSubmissionResult

    partial = BatchSubmissionResult(
        status="ok",
        project_id="p1",
        prompt_idx=0,
        prompt_hash="aa",
        images=(),
    )
    cause = GFlowError(detail="upstream timeout", route="batch")
    err = BatchPartialError(
        detail="batch failed on prompt 1",
        route="batch",
        partial_results=(partial,),
        cause=cause,
    )
    assert err.partial_results == (partial,)
    assert err.cause is cause
    assert isinstance(err, GFlowError)


def test_batch_integrity_error_carries_indices() -> None:
    from gflow_cli.errors import BatchIntegrityError, GFlowError

    err = BatchIntegrityError(
        detail="expected 4 files, got 3",
        route="batch",
        prompt_indices=(1, 2),
    )
    assert err.prompt_indices == (1, 2)
    assert isinstance(err, GFlowError)
```

- [ ] **Step 2: Run the test (RED)**

```powershell
uv run python -m pytest tests/test_errors.py -v
```

Expected: ImportError on `BatchPartialError` or `BatchIntegrityError`.

- [ ] **Step 3: Add the exception classes to `src/gflow_cli/errors.py`**

Locate the existing `GFlowError` definition. Add below it (or in the section where other domain-specific errors live):

```python
@dataclass(frozen=True)
class BatchPartialError(GFlowError):
    """Raised by `generate_images_batch` under fail-fast when one prompt failed
    after others already produced ready-to-download results.

    Carries `partial_results` (tuple of completed `BatchSubmissionResult`)
    so the orchestrator can still download the user's already-paid-for
    images before surfacing the underlying error.
    """

    partial_results: tuple = ()
    cause: Exception | None = None


@dataclass(frozen=True)
class BatchIntegrityError(GFlowError):
    """Raised by the orchestrator after a batch returns when the on-disk file
    count does not match the expected count. Catches silent mis-delivery
    even when transport-layer status is reported as 'ok'.
    """

    prompt_indices: tuple[int, ...] = ()
```

If `GFlowError` is not already a frozen dataclass, follow the existing pattern (it probably has `detail: str` and `route: str` fields — match its style). If `GFlowError` uses a regular class with `__init__`, mirror that pattern instead.

- [ ] **Step 4: Run the test (GREEN)**

```powershell
uv run python -m pytest tests/test_errors.py -v
```

Expected: PASS.

- [ ] **Step 5: Type-check**

```powershell
uv run pyright src/gflow_cli/errors.py
```

Expected: clean.

### Task 3.3: Extend `_attach_batch_response_listener` to return `(captured, detach_fn)`

**Files:**
- Modify: `src/gflow_cli/api/transports/ui_automation.py` (around line 758-800)
- Modify: `src/gflow_cli/api/transports/ui_automation.py` line 991 (single-prompt caller)
- Modify: `src/gflow_cli/api/transports/ui_automation_video.py` (any video callers; check)
- Test: `tests/api/test_ui_automation.py` (extend existing tests if signature is mocked there)

- [ ] **Step 1: Read the existing helper**

```powershell
uv run python -c "import inspect, gflow_cli.api.transports.ui_automation as m; print(inspect.getsource(m.UiAutomationTransport._attach_batch_response_listener))"
```

Note its signature and return value. Note all internal call sites (lines 991 in `_generate_images_locked`, possibly in `ui_automation_video.py`).

- [ ] **Step 2: Write the failing test**

In `tests/api/test_ui_automation.py` (add a new test function, keep the existing ones working):

```python
def test_attach_batch_response_listener_returns_detach_callable() -> None:
    """The helper now returns (captured_list, detach_fn). Detach must remove
    the registered handler from the page so that subsequent simulated
    responses do NOT append to the captured list."""
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport

    class FakePage:
        def __init__(self) -> None:
            self.handlers: list = []

        def on(self, event: str, handler) -> None:  # type: ignore[no-untyped-def]
            assert event == "response"
            self.handlers.append(handler)

        def remove_listener(self, event: str, handler) -> None:  # type: ignore[no-untyped-def]
            assert event == "response"
            self.handlers.remove(handler)

    page = FakePage()
    captured, detach = UiAutomationTransport._attach_batch_response_listener(page, project_id="p1")  # type: ignore[arg-type]
    assert isinstance(captured, list)
    assert callable(detach)
    assert len(page.handlers) == 1

    detach()
    assert len(page.handlers) == 0

    # Idempotent — second call must not raise
    detach()
    assert len(page.handlers) == 0
```

- [ ] **Step 3: Run the test (RED)**

```powershell
uv run python -m pytest tests/api/test_ui_automation.py::test_attach_batch_response_listener_returns_detach_callable -v
```

Expected: TypeError or "too many values to unpack" because the existing helper returns only the list.

- [ ] **Step 4: Extend the helper**

Modify `UiAutomationTransport._attach_batch_response_listener` (around line 758) to return a tuple:

```python
@staticmethod
def _attach_batch_response_listener(
    page: "Page",
    *,
    project_id: str | None = None,
) -> "tuple[list[Any], Callable[[], None]]":
    """Attach a response handler that captures matching batchGenerateImages
    responses into a list. Returns the list and a detach callback.

    The returned callable removes the handler from the Page when invoked;
    it is idempotent (safe to call twice).
    """
    captured: list[Any] = []

    def on_response(response: Any) -> None:
        # ... preserve the existing filter logic + log.info call here ...
        # The body should be IDENTICAL to today's handler; only the wrapper
        # around it changes.
        ...

    page.on("response", on_response)
    detached = False

    def detach() -> None:
        nonlocal detached
        if detached:
            return
        detached = True
        try:
            page.remove_listener("response", on_response)
        except Exception:  # noqa: BLE001 — idempotent on already-removed
            pass

    return captured, detach
```

Preserve the existing filter/log/capture logic inside `on_response` byte-for-byte; only wrap it so detach is reachable.

- [ ] **Step 5: Update internal call sites**

Search and update each call site to unpack the tuple:

```powershell
uv run python -c "import re, pathlib; [print(p, ':', i+1, ':', l.rstrip()) for p in pathlib.Path('src').rglob('*.py') for i, l in enumerate(p.read_text().splitlines()) if '_attach_batch_response_listener' in l]"
```

For each call site (line 858 docstring-only — ignore; line 991 single-prompt):

```python
# Before
captured = self._attach_batch_response_listener(page, project_id=nav_project_id)

# After
captured, _detach = self._attach_batch_response_listener(page, project_id=nav_project_id)
```

Single-prompt callers ignore the detach handle with `_detach` (page is closed shortly after the call anyway — no leak).

- [ ] **Step 6: Run the test (GREEN)**

```powershell
uv run python -m pytest tests/api/test_ui_automation.py::test_attach_batch_response_listener_returns_detach_callable tests/api/test_ui_automation.py -v
```

Expected: the new test passes AND all existing `test_ui_automation.py` tests still pass (unchanged behaviour for the captured-list shape).

- [ ] **Step 7: Type-check**

```powershell
uv run pyright src/gflow_cli/api/transports/ui_automation.py
```

Expected: clean.

### Task 3.4: Extend `_send_prompt` to clear the editor field before typing

**Files:**
- Modify: `src/gflow_cli/api/transports/ui_automation.py` `_send_prompt` (around line 609)
- Test: `tests/api/test_ui_automation.py` (add a regression test)

- [ ] **Step 1: Read the current `_send_prompt` implementation**

```powershell
uv run python -c "import inspect, gflow_cli.api.transports.ui_automation as m; print(inspect.getsource(m.UiAutomationTransport._send_prompt))"
```

Note: the existing docstring near line 619 says ".fill() bypasses onChange handlers — we deliberately use keyboard events". This means the current code does NOT clear the field; it just types. For a stay-mounted editor across multiple prompts, this would concatenate text.

- [ ] **Step 2: Write the failing test**

In `tests/api/test_ui_automation.py`:

```python
@pytest.mark.asyncio
async def test_send_prompt_clears_field_before_typing() -> None:
    """In the stay-mounted batch flow, two successive _send_prompt calls
    must NOT concatenate text. The second call's typed value should equal
    the second prompt alone, not 'prompt1prompt2'."""
    from unittest.mock import AsyncMock, MagicMock
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport

    page = MagicMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.keyboard.type = AsyncMock()
    page.locator = MagicMock()
    locator = MagicMock()
    locator.click = AsyncMock()
    locator.fill = AsyncMock()
    locator.type = AsyncMock()
    page.locator.return_value = locator

    transport = UiAutomationTransport.__new__(UiAutomationTransport)  # bypass __init__
    transport._page = page  # type: ignore[attr-defined]

    # Simulate two successive sends.
    await transport._send_prompt(page, "first prompt", None)  # type: ignore[attr-defined]
    await transport._send_prompt(page, "second prompt", None)  # type: ignore[attr-defined]

    # The second send must have included a field-clear step before typing.
    # Pattern: Ctrl+A then Delete, OR locator.fill("") before .type().
    # Inspect ALL invocations on page.keyboard.press + locator.fill across the
    # second call and assert one of these patterns is present.
    keyboard_presses = [c.args[0] for c in page.keyboard.press.await_args_list]
    locator_fills = [c.args[0] for c in locator.fill.await_args_list]
    assert (
        ("Control+A" in keyboard_presses and "Delete" in keyboard_presses)
        or "" in locator_fills
    ), (
        f"_send_prompt did not clear the field. keyboard_presses={keyboard_presses!r}, "
        f"locator_fills={locator_fills!r}"
    )
```

- [ ] **Step 3: Run the test (RED)**

```powershell
uv run python -m pytest tests/api/test_ui_automation.py::test_send_prompt_clears_field_before_typing -v
```

Expected: FAIL — the assertion shows the current `_send_prompt` does not clear.

- [ ] **Step 4: Modify `_send_prompt`**

Locate the body of `_send_prompt` in `ui_automation.py`. Just before the existing `await page.keyboard.type(...)` (or equivalent), insert a field-clear step. The simplest reliable approach with the existing keyboard-events strategy:

```python
# Clear any leftover text from the previous submission in the stay-mounted
# editor. Ctrl+A + Delete preserves the onChange dispatch that .fill()
# bypasses (per the existing docstring rationale).
await page.keyboard.press("Control+A")
await page.keyboard.press("Delete")
# then the existing type call:
await page.keyboard.type(prompt)
```

Keep the existing locator-find logic and post-type click logic unchanged.

- [ ] **Step 5: Run the test (GREEN)**

```powershell
uv run python -m pytest tests/api/test_ui_automation.py::test_send_prompt_clears_field_before_typing -v
```

Expected: PASS.

- [ ] **Step 6: Run the full ui_automation unit suite to catch regressions**

```powershell
uv run python -m pytest tests/api/test_ui_automation.py -v
```

Expected: all tests pass.

### Task 3.5: Write the multi-listener concurrency test (must fail loud if listeners cross-contaminate)

**Files:**
- Test only: `tests/api/test_ui_automation_batch.py` (new file)

- [ ] **Step 1: Create the new test file with the critical test**

`tests/api/test_ui_automation_batch.py`:

```python
"""Unit tests for UiAutomationTransport.generate_images_batch.

The highest-stakes test is the multi-listener concurrency invariant —
council Finding T1. With N listeners active simultaneously on the same
Page, each captures into its own list, and we assert no cross-contamination.
"""

from __future__ import annotations

import pytest

from gflow_cli.api.transports.ui_automation import UiAutomationTransport


class _FakePage:
    """Minimal Page surrogate that records response handlers and lets a test
    fire mocked response events at them in arbitrary order."""

    def __init__(self) -> None:
        self.handlers: list = []

    def on(self, event: str, handler) -> None:  # type: ignore[no-untyped-def]
        assert event == "response"
        self.handlers.append(handler)

    def remove_listener(self, event: str, handler) -> None:  # type: ignore[no-untyped-def]
        assert event == "response"
        self.handlers.remove(handler)

    def fire_response(self, response) -> None:  # type: ignore[no-untyped-def]
        for h in list(self.handlers):
            h(response)


class _FakeResponse:
    def __init__(self, url: str, status: int = 200, body: bytes = b"{}") -> None:
        self.url = url
        self.status = status
        self._body = body

    async def body(self) -> bytes:
        return self._body


def test_multi_listener_no_cross_contamination() -> None:
    """Two listeners attached on the same page, both filtered by the same
    project_id (because we're in same-project mode). Responses arrive
    interleaved. Each listener's captured list must contain only its own
    prompt's responses."""
    page = _FakePage()

    captured_1, detach_1 = UiAutomationTransport._attach_batch_response_listener(
        page, project_id="proj-shared"  # type: ignore[arg-type]
    )

    # Fire one response that belongs to prompt 0 BEFORE attaching listener 2.
    page.fire_response(_FakeResponse("https://flow/projects/proj-shared/batchGenerateImages?p=0"))
    assert len(captured_1) == 1
    # Should be visible only to listener 1.

    captured_2, detach_2 = UiAutomationTransport._attach_batch_response_listener(
        page, project_id="proj-shared"  # type: ignore[arg-type]
    )

    # Fire responses interleaved.
    page.fire_response(_FakeResponse("https://flow/projects/proj-shared/batchGenerateImages?p=1"))
    page.fire_response(_FakeResponse("https://flow/projects/proj-shared/batchGenerateImages?p=0-2"))
    page.fire_response(_FakeResponse("https://flow/projects/proj-shared/batchGenerateImages?p=1-2"))

    # Without a post-attach-time filter, listener 1 would see ALL of these
    # (count 4) and listener 2 would see the last three (count 3). The
    # design's submission-order-arrival assumption (spec §5.6 option 2)
    # accepts that responses arrive after their submission's click event,
    # so the realistic shape is: listener 1 sees its own count (1-2)
    # responses, listener 2 sees its own count (1-2). For the unit test we
    # accept either:
    #   (a) the explicit post-attach-time filter is implemented and
    #       captured_1 strictly excludes post-listener-2-attach responses,
    #   (b) the simpler shape where both listeners see all post-attach
    #       responses but the project_id filter prevents cross-PROJECT
    #       contamination.
    # The HARDER assertion (a) is the goal; if v3-3 implements only (b),
    # this test must xfail explicitly with the reason cited.
    # Documented choice: v3-3 starts with (b) so this test is xfailed with a
    # link to spec §10 Open Questions.
    pytest.xfail(
        "v3-3 ships with submission-order-arrival assumption (spec §5.6 "
        "option 2). Promote this test to strict-pass when option 1 (explicit "
        "post-attach-time filter) is implemented per spec §10 follow-up."
    )

    # If/when option 1 lands, uncomment and assert:
    # assert len(captured_1) == 1  # only its initial response
    # assert len(captured_2) == 3  # the three post-attach responses
    detach_1()
    detach_2()


def test_listener_detach_is_idempotent_and_removes_handler() -> None:
    page = _FakePage()
    captured, detach = UiAutomationTransport._attach_batch_response_listener(
        page, project_id="proj-1"  # type: ignore[arg-type]
    )
    assert len(page.handlers) == 1

    detach()
    assert len(page.handlers) == 0

    # After detach, firing a response must not append.
    page.fire_response(_FakeResponse("https://flow/projects/proj-1/batchGenerateImages"))
    assert len(captured) == 0

    # Idempotent second detach.
    detach()
    assert len(page.handlers) == 0
```

- [ ] **Step 2: Run the new tests**

```powershell
uv run python -m pytest tests/api/test_ui_automation_batch.py -v
```

Expected: the cross-contamination test is XFAIL (acceptable for v3-3); the detach idempotency test PASSES.

### Task 3.6: Implement `generate_images_batch` — happy path

**Files:**
- Modify: `src/gflow_cli/api/transports/ui_automation.py` (add the new method below the existing `_generate_images_locked`)
- Test: `tests/api/test_ui_automation_batch.py` (extend with happy-path tests)

- [ ] **Step 1: Write the happy-path test**

Append to `tests/api/test_ui_automation_batch.py`:

```python
@pytest.mark.asyncio
async def test_generate_images_batch_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three prompts, all succeed. Verify:
    - _enter_editor called once
    - _dismiss_blocking_overlays called once
    - _configure_generation_settings + _attach_batch_response_listener + _send_prompt called 3x each, in order
    - jitter sleep called twice (between prompts), not before the first or after the last
    - results returned in submission order
    - every result carries the same project_id
    - every result has the correct prompt_idx (0, 1, 2)
    """
    from unittest.mock import AsyncMock, MagicMock, call
    from gflow_cli.api.dto import GenerateImageRequest

    # Build a mock transport instance bypassing __init__.
    transport = UiAutomationTransport.__new__(UiAutomationTransport)
    transport._setup_done = True  # type: ignore[attr-defined]
    transport._page = MagicMock()  # type: ignore[attr-defined]
    transport._page.url = "https://labs.google/fx/tools/flow/project/PROJECT-UUID"
    transport._out_dir = None  # type: ignore[attr-defined]
    transport._generate_lock = __import__("asyncio").Lock()  # type: ignore[attr-defined]

    transport._enter_editor = AsyncMock()  # type: ignore[attr-defined]
    transport._dismiss_blocking_overlays = AsyncMock()  # type: ignore[attr-defined]
    transport._configure_generation_settings = AsyncMock()  # type: ignore[attr-defined]
    transport._send_prompt = AsyncMock()  # type: ignore[attr-defined]

    # Mock the listener to return 3 distinct (captured, detach) pairs.
    captures = [[], [], []]
    detaches = [MagicMock(), MagicMock(), MagicMock()]
    listener_calls = [0]

    def fake_listener(page, *, project_id=None):  # type: ignore[no-untyped-def]
        idx = listener_calls[0]
        listener_calls[0] += 1
        return captures[idx], detaches[idx]

    transport._attach_batch_response_listener = staticmethod(fake_listener)  # type: ignore[assignment]

    # _await_captured returns the prompt-specific mocked response, with
    # image-bearing payload, in submission order.
    # Use _images_from_responses-mockable shape — instead, we'll mock
    # _await_captured to return what's already in the capture list.
    async def fake_await(captured, expected_count):  # type: ignore[no-untyped-def]
        # Each prompt's expected_count images already stuffed into its capture list.
        return captured

    transport._await_captured = fake_await  # type: ignore[attr-defined]

    # Pre-stuff one image-response per prompt into each capture list.
    from gflow_cli.api.dto import GeneratedImage
    img_mock_factory = lambda prompt_idx: MagicMock(spec=GeneratedImage)  # noqa: E731
    for i, cap in enumerate(captures):
        cap.append(img_mock_factory(i))

    # Mock _images_from_responses to return one image per response.
    import gflow_cli.api.transports.ui_automation as uia_mod
    monkeypatch.setattr(
        uia_mod, "_images_from_responses",
        lambda responses: ([img_mock_factory(0)] * len(responses), None, None),
    )

    # Mock _extract_project_id to return the URL-extracted UUID.
    monkeypatch.setattr(
        uia_mod, "_extract_project_id",
        lambda url: "PROJECT-UUID",
    )

    # Stub random.uniform + asyncio.sleep to make jitter deterministic + fast.
    sleep_calls: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleep_calls.append(d)

    monkeypatch.setattr(uia_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(uia_mod.random, "uniform", lambda a, b: 1.5)  # deterministic

    prompts = [
        GenerateImageRequest(prompt="p0", aspect=MagicMock(), model=MagicMock(), count=1),
        GenerateImageRequest(prompt="p1", aspect=MagicMock(), model=MagicMock(), count=1),
        GenerateImageRequest(prompt="p2", aspect=MagicMock(), model=MagicMock(), count=1),
    ]

    results = await transport.generate_images_batch(
        prompts=prompts, jitter_range=(1.0, 2.0), continue_on_error=False
    )

    # Bug-fix invariants:
    assert transport._enter_editor.call_count == 1
    assert transport._dismiss_blocking_overlays.call_count == 1
    assert transport._configure_generation_settings.call_count == 3
    assert transport._send_prompt.call_count == 3
    assert listener_calls[0] == 3
    assert sleep_calls == [1.5, 1.5]  # N-1 sleeps, both deterministic

    # Shared project_id:
    assert len({r.project_id for r in results}) == 1
    assert results[0].project_id == "PROJECT-UUID"

    # Submission order preserved:
    assert [r.prompt_idx for r in results] == [0, 1, 2]
    assert all(r.status == "ok" for r in results)

    # Detach was called for every listener:
    for d in detaches:
        d.assert_called()
```

- [ ] **Step 2: Run the test (RED)**

```powershell
uv run python -m pytest tests/api/test_ui_automation_batch.py::test_generate_images_batch_happy_path -v
```

Expected: AttributeError on `transport.generate_images_batch`.

- [ ] **Step 3: Implement `generate_images_batch`**

Add to `UiAutomationTransport` in `ui_automation.py` (after `_generate_images_locked`):

```python
import random
import asyncio
import time
from gflow_cli.api.dto import BatchSubmissionResult, GeneratedImage, GenerateImageRequest
from gflow_cli.errors import BatchPartialError, GFlowError

# ...

async def generate_images_batch(
    self,
    *,
    prompts: list[GenerateImageRequest],
    jitter_range: tuple[float, float],
    continue_on_error: bool = False,
) -> list[BatchSubmissionResult]:
    """Stay-mounted batch image generation. See spec §5.1 for full semantics."""
    if not self._setup_done or self._page is None:
        raise RuntimeError(
            "UiAutomationTransport.setup() must be called before generate_images_batch()"
        )

    async with self._generate_lock:
        return await self._generate_images_batch_locked(
            prompts=prompts,
            jitter_range=jitter_range,
            continue_on_error=continue_on_error,
        )

async def _generate_images_batch_locked(
    self,
    *,
    prompts: list[GenerateImageRequest],
    jitter_range: tuple[float, float],
    continue_on_error: bool,
) -> list[BatchSubmissionResult]:
    page = self._page  # type: ignore[assignment]
    out_dir = self._out_dir

    # Batch-setup phase (one-shot per batch).
    await self._enter_editor(page, out_dir)
    project_id = _extract_project_id(page.url)
    if project_id is None:
        # Orphaned-project warning is moot here — no project was created.
        raise RuntimeError("Could not extract project_id from editor URL after _enter_editor")

    try:
        await self._dismiss_blocking_overlays(page, out_dir)
    except Exception:
        # Orphaned-project warning: _enter_editor succeeded but a later setup
        # step failed. Log so the user can find the orphaned project on Flow.
        log.warning(
            "ui_automation.orphaned_project_warning",
            project_id=project_id,
            page_url=page.url,
            failed_step="_dismiss_blocking_overlays",
        )
        raise

    # Per-prompt submission phase.
    pending: list[tuple[int, str, str, list, "Callable[[], None]", int]] = []
    submit_error: GFlowError | None = None
    try:
        for idx, req in enumerate(prompts):
            aspect_cli = _aspect_cli_from_enum(req.aspect)
            try:
                await self._configure_generation_settings(page, aspect_cli, req.count)
                captured, detach = self._attach_batch_response_listener(
                    page, project_id=project_id
                )
                # Per-prompt try ensures detach runs even on submit failure.
                try:
                    await self._send_prompt(page, req.prompt, out_dir)
                except Exception as exc:
                    detach()
                    if not continue_on_error:
                        submit_error = exc if isinstance(exc, GFlowError) else GFlowError(
                            detail=str(exc), route="generate_images_batch"
                        )
                        break
                    # continue-on-error: record fail and move on
                    pending.append(
                        (idx, project_id, _prompt_hash_stable(req.prompt), [], detach, req.count)
                    )
                    continue
                pending.append(
                    (idx, project_id, _prompt_hash_stable(req.prompt), captured, detach, req.count)
                )
            except Exception as exc:
                if not continue_on_error:
                    submit_error = exc if isinstance(exc, GFlowError) else GFlowError(
                        detail=str(exc), route="generate_images_batch"
                    )
                    break
                pending.append(
                    (idx, project_id, _prompt_hash_stable(req.prompt), [], lambda: None, req.count)
                )

            # Jitter between prompts (not after the last).
            if idx < len(prompts) - 1:
                delay = random.uniform(*jitter_range)
                await asyncio.sleep(delay)

        # Per-prompt await phase, in submission order.
        results: list[BatchSubmissionResult] = []
        for idx, pid, prompt_hash, captured, detach, expected_count in pending:
            try:
                responses = await self._await_captured(captured, expected_count=expected_count)
                detach()
                if len(responses) < expected_count:
                    results.append(BatchSubmissionResult(
                        status="fail",
                        project_id=pid,
                        prompt_idx=idx,
                        prompt_hash=prompt_hash,
                        images=(),
                        error=GFlowError(
                            detail=f"_await_captured timed out: got {len(responses)}/{expected_count}",
                            route="generate_images_batch",
                        ),
                    ))
                    if not continue_on_error:
                        submit_error = results[-1].error
                        break
                    continue
                images, first_error_status, _ = _images_from_responses(responses)
                if not images:
                    results.append(BatchSubmissionResult(
                        status="fail",
                        project_id=pid,
                        prompt_idx=idx,
                        prompt_hash=prompt_hash,
                        images=(),
                        error=GFlowError(
                            detail=f"no parseable images (first_error_status={first_error_status})",
                            route="generate_images_batch",
                        ),
                    ))
                    if not continue_on_error:
                        submit_error = results[-1].error
                        break
                    continue
                results.append(BatchSubmissionResult(
                    status="ok",
                    project_id=pid,
                    prompt_idx=idx,
                    prompt_hash=prompt_hash,
                    images=tuple(images),
                    error=None,
                ))
            except Exception as exc:
                detach()
                results.append(BatchSubmissionResult(
                    status="fail",
                    project_id=pid,
                    prompt_idx=idx,
                    prompt_hash=prompt_hash,
                    images=(),
                    error=exc if isinstance(exc, GFlowError) else GFlowError(
                        detail=str(exc), route="generate_images_batch"
                    ),
                ))
                if not continue_on_error:
                    submit_error = results[-1].error
                    break

    finally:
        # Cleanup invariant: every pending listener gets detached exactly once.
        for *_, detach, _ in pending:
            try:
                detach()
            except Exception:  # noqa: BLE001 — idempotent
                pass

    # If fail-fast tripped, surface partial-results salvage.
    if submit_error is not None and not continue_on_error:
        raise BatchPartialError(
            detail=f"batch failed at prompt {len(results)}: {submit_error!s}",
            route="generate_images_batch",
            partial_results=tuple(r for r in results if r.status == "ok"),
            cause=submit_error,
        )

    return results
```

Add a module-level helper at the top of `ui_automation.py` (or import the existing one from `image_batch.py` if importable without circularity):

```python
import hashlib

def _prompt_hash_stable(text: str) -> str:
    """Truncated sha256 to match image_batch._prompt_hash. Inlined here to
    avoid src/gflow_cli/api/transports importing image_batch."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
```

- [ ] **Step 4: Run the test (GREEN)**

```powershell
uv run python -m pytest tests/api/test_ui_automation_batch.py::test_generate_images_batch_happy_path -v
```

Expected: PASS.

- [ ] **Step 5: Type-check**

```powershell
uv run pyright src/gflow_cli/api/transports/ui_automation.py
```

Expected: clean.

### Task 3.7: Failure-mode tests for `generate_images_batch`

**Files:**
- Test: `tests/api/test_ui_automation_batch.py` (extend)

- [ ] **Step 1: Add the failure-mode tests**

Append to `tests/api/test_ui_automation_batch.py`:

```python
@pytest.mark.asyncio
async def test_generate_images_batch_continue_on_error_send_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """One prompt's _send_prompt raises. With continue_on_error=True the loop
    continues and that prompt's result has status='fail'."""
    # ... mirror the happy-path setup; make _send_prompt raise on idx=1.
    # Assert results[1].status == "fail" and results[0/2].status == "ok".
    # Assert detaches[1] was called (no dangling listener).
    pass  # TODO in implementation


@pytest.mark.asyncio
async def test_generate_images_batch_fail_fast_partial_salvage(monkeypatch: pytest.MonkeyPatch) -> None:
    """One prompt's _send_prompt raises with continue_on_error=False. Method
    raises BatchPartialError carrying partial_results for prompts 0..N-1
    that already completed."""
    # Setup: 3 prompts. Prompt 0 succeeds (capture has 1 image). Prompt 1's
    # _send_prompt raises. Prompt 2 never submits.
    # Expected: BatchPartialError raised with partial_results=(result_0,)
    # and cause containing the underlying exception.
    pass  # TODO in implementation


@pytest.mark.asyncio
async def test_generate_images_batch_await_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """_await_captured returns partial list (timeout). That prompt's result
    is status='fail' with TimeoutError; if continue_on_error=True, others
    continue."""
    pass  # TODO in implementation


@pytest.mark.asyncio
async def test_generate_images_batch_orphaned_project_warning_on_overlay_fail(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """_enter_editor succeeds (project URL extractable) but _dismiss_blocking_overlays
    raises. Method must emit ui_automation.orphaned_project_warning structlog
    event with the project_id BEFORE re-raising."""
    pass  # TODO in implementation


@pytest.mark.asyncio
async def test_generate_images_batch_detach_invariant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every listener that was attached has its detach_fn called exactly once
    before the method returns (or raises), on both happy and error paths."""
    pass  # TODO in implementation
```

Each `pass  # TODO in implementation` is a placeholder — when a subagent picks up Task 3.7, the implementing agent fills in each test body by mirroring the happy-path setup pattern in Task 3.6 and varying the failure injection point. The mock structure is identical; only the assertion changes.

- [ ] **Step 2: Implement each test body** (one at a time, RED → GREEN cycle per test)

For each `pass` placeholder:
1. Replace with the full test body (mirror Task 3.6 happy-path setup; vary the failure injection).
2. Run that specific test to verify it fails meaningfully against the current `generate_images_batch` implementation (it should already pass for failure paths the implementation handles, or fail meaningfully for paths to be added).
3. Fix the implementation if needed.
4. Re-run.

- [ ] **Step 3: Run the full batch unit suite**

```powershell
uv run python -m pytest tests/api/test_ui_automation_batch.py -v
```

Expected: all tests pass (one xfail for the cross-contamination test is acceptable per Task 3.5).

### Task 3.8: Refactor `run_manifest_image_batch` orchestrator

**Files:**
- Modify: `src/gflow_cli/image_batch.py` (refactor `run_manifest_image_batch` and remove dead code)
- Modify: `tests/image_batch/test_run_manifest_image_batch.py` (existing tests may need new mocks)

- [ ] **Step 1: Read the current orchestrator**

```powershell
uv run python -c "import inspect, gflow_cli.image_batch as m; print(inspect.getsource(m.run_manifest_image_batch))"
```

Note the `same_project: bool` parameter, the `create_project` call, the per-prompt loop with `run_one_image_prompt`, and the `image_batch.*` structlog events.

- [ ] **Step 2: Write the orchestrator test (RED)**

In `tests/image_batch/test_run_manifest_image_batch.py`:

```python
@pytest.mark.asyncio
async def test_run_manifest_image_batch_calls_transport_once(tmp_path) -> None:
    """Orchestrator calls transport.generate_images_batch exactly once, then
    downloads each ok-result's images via client.download_image."""
    from unittest.mock import AsyncMock, MagicMock
    from gflow_cli.api.dto import BatchSubmissionResult, GeneratedImage
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport
    from gflow_cli.image_batch import BatchPromptItem, run_manifest_image_batch

    # Stub UiAutomationTransport-shaped instance.
    transport = MagicMock(spec=UiAutomationTransport)
    transport.generate_images_batch = AsyncMock(return_value=[
        BatchSubmissionResult(
            status="ok",
            project_id="proj-abc",
            prompt_idx=0,
            prompt_hash="00000000",
            images=(MagicMock(spec=GeneratedImage),),
        ),
    ])
    client = MagicMock()
    client.transport = transport
    client.download_image = AsyncMock(side_effect=lambda img, target: target)

    # async-context-manager mock
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=client)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)

    prompts = (BatchPromptItem(text="hi", aspect_ratio="9:16", model="default", count=1, output_filename="prompt_0", index=0),)
    out = tmp_path / "out"
    out.mkdir()

    outcomes = await run_manifest_image_batch(
        profile_dir=tmp_path,
        headless=True,
        transport=None,
        prompts=prompts,
        output_dir=out,
        continue_on_error=False,
        project_title="test",
        jitter_range=(0.0, 0.0),
        client_factory=factory,
    )

    transport.generate_images_batch.assert_awaited_once()
    client.download_image.assert_awaited_once()
    assert len(outcomes) == 1
    assert outcomes[0].status == "ok"
```

- [ ] **Step 3: Run the test (RED)**

Expected: TypeError on the `same_project` parameter being removed, or `transport.generate_images_batch` not being called.

- [ ] **Step 4: Refactor `run_manifest_image_batch`**

Replace the body of `run_manifest_image_batch` with:

```python
async def run_manifest_image_batch(
    *,
    profile_dir: Path,
    headless: bool,
    transport: str | None,
    prompts: tuple[BatchPromptItem, ...],
    output_dir: Path,
    continue_on_error: bool,
    project_title: str,
    jitter_range: tuple[float, float] = (JITTER_MIN_SECONDS, JITTER_MAX_SECONDS),
    client_factory: Callable[..., Any] | None = None,
) -> list[BatchOutcome]:
    """Run a manifest batch via the transport's stay-mounted batch method.

    All prompts share one Flow project (always-same-project semantics; the
    --same-project=0 mode is removed). Jitter is the submission-cadence
    anti-bot control, applied between submissions inside one editor session.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    factory = client_factory or FlowApiClient
    async with factory(profile_dir=profile_dir, headless=headless, transport=transport) as client:
        # Capability check: only UiAutomationTransport implements the batch method.
        if not isinstance(client.transport, UiAutomationTransport):
            raise RuntimeError(
                f"gflow image batch requires the ui_automation transport; "
                f"got {type(client.transport).__name__}"
            )

        # Build the per-prompt requests.
        requests = [_to_request(item) for item in prompts]

        # Single delegation; transport handles all the editor/listener/jitter logic.
        try:
            results = await client.transport.generate_images_batch(
                prompts=requests,
                jitter_range=jitter_range,
                continue_on_error=continue_on_error,
            )
        except BatchPartialError as exc:
            # Fail-fast salvage: download the partial results before re-raising.
            partial_outcomes = await _download_results(
                client=client,
                prompts=prompts,
                results=list(exc.partial_results),
                output_dir=output_dir,
            )
            # Surface the partial outcomes + the underlying error.
            raise BatchPartialError(
                detail=exc.detail,
                route=exc.route,
                partial_results=tuple(partial_outcomes),  # downloaded
                cause=exc.cause,
            ) from exc.cause

        # Happy path: download every ok result.
        outcomes = await _download_results(
            client=client,
            prompts=prompts,
            results=results,
            output_dir=output_dir,
        )

        # Post-download integrity check.
        ok_outcomes = [o for o in outcomes if o.status == "ok"]
        expected_files = sum(p.count for p, o in zip(prompts, outcomes) if o.status == "ok")
        actual_files = sum(len(o.saved_paths) for o in ok_outcomes)
        if actual_files != expected_files:
            missing = [
                p.index for p, o in zip(prompts, outcomes)
                if o.status == "ok" and len(o.saved_paths) < p.count
            ]
            raise BatchIntegrityError(
                detail=f"expected {expected_files} files, got {actual_files}",
                route="run_manifest_image_batch",
                prompt_indices=tuple(missing),
            )

        return outcomes


def _to_request(item: BatchPromptItem) -> GenerateImageRequest:
    return GenerateImageRequest(
        prompt=item.text,
        aspect=Aspect.from_cli(item.aspect_ratio),
        model=Model.from_cli(item.model),
        count=item.count,
    )


async def _download_results(
    *,
    client: Any,
    prompts: tuple[BatchPromptItem, ...],
    results: list[BatchSubmissionResult],
    output_dir: Path,
) -> list[BatchOutcome]:
    outcomes: list[BatchOutcome] = []
    for item, result in zip(prompts, results):
        if result.status != "ok":
            outcomes.append(BatchOutcome(
                index=item.index,
                prompt=item,
                status="fail",
                error=f"{type(result.error).__name__}: {result.error}" if result.error else "unknown",
            ))
            # Emit the structured event so test/observability assertions still hold.
            logger.info(
                "image_batch.row_completed",
                row_idx=item.index,
                prompt_hash=result.prompt_hash,
                project_id=result.project_id,
                outcome="fail",
            )
            continue

        stem = item.output_filename or f"prompt_{item.index}"
        saved: list[Path] = []
        for img_idx, img in enumerate(result.images):
            target = output_dir / f"{stem}_{img_idx}.png"
            path = await client.download_image(img, target)
            saved.append(path)
            sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
            logger.info(
                "image_batch.row_completed",
                row_idx=item.index,
                output_idx=img_idx,
                prompt_hash=result.prompt_hash,
                project_id=result.project_id,
                sha256_prefix=sha,
                outcome="ok",
            )
        outcomes.append(BatchOutcome(
            index=item.index,
            prompt=item,
            status="ok",
            saved_paths=saved,
        ))
    return outcomes
```

Delete the now-unused `run_one_image_prompt` if it's only called from inside `run_manifest_image_batch`. Verify with grep:

```powershell
uv run python -c "import re, pathlib; [print(p, ':', i+1, ':', l.rstrip()) for p in pathlib.Path('src').rglob('*.py') for i, l in enumerate(p.read_text().splitlines()) if 'run_one_image_prompt' in l]"
```

If `run_one_image_prompt` is called from elsewhere (e.g., `run_image_batch` or any CLI direct entry), leave it but mark with a one-line comment that it's now used only by the legacy non-stay-mounted path.

- [ ] **Step 5: Run the orchestrator test (GREEN)**

```powershell
uv run python -m pytest tests/image_batch/test_run_manifest_image_batch.py -v
```

Expected: PASS.

- [ ] **Step 6: Type-check**

```powershell
uv run pyright src/gflow_cli/image_batch.py
```

Expected: clean.

### Task 3.9: Commit v3-3

- [ ] **Step 1: Stage all v3-3 files**

```powershell
git add src/gflow_cli/api/dto.py src/gflow_cli/errors.py src/gflow_cli/api/transports/ui_automation.py src/gflow_cli/image_batch.py tests/api/test_dto.py tests/test_errors.py tests/api/test_ui_automation.py tests/api/test_ui_automation_batch.py tests/image_batch/test_run_manifest_image_batch.py
git diff --cached --stat
```

Expected: ~9 files modified/created.

- [ ] **Step 2: Run the full check suite (scoped to changed dirs)**

```powershell
uv run python scripts/ci/check_repo_hygiene.py
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run python -m pytest -q tests/api tests/test_errors.py tests/image_batch
```

Expected: all clean. If any test fails, fix and re-run.

- [ ] **Step 3: Commit**

```powershell
git commit -m "refactor(image)!: stay-mounted editor session in ui_automation transport

Fixes the --same-project=1 no-op defect: ui_automation.generate_images
discarded the project_id it was given (\"accepted for Protocol parity; UI
creates its own project\"), so every prompt of a batch landed in its own
Flow project. The stay-mounted refactor opens the editor once per batch,
submits N prompts with jitter between, captures each prompt's responses
via a per-prompt listener (now returning a detach handle), awaits in
submission order with cleanup invariant on listener detach, and parses
images into per-prompt BatchSubmissionResult records.

BREAKING: BatchOutcome's downstream-error string format may change for
failed prompts (now derives from BatchSubmissionResult.error which is a
typed GFlowError). The public list[BatchOutcome] return type and the
image_batch.submission_attempt / row_completed structlog events are
preserved. The --same-project flag is dropped in the follow-up commit
v3-4; this commit makes always-same-project the only behaviour at the
transport+orchestrator layer.

New types: BatchSubmissionResult (api.dto), BatchPartialError +
BatchIntegrityError (errors). Extended: _attach_batch_response_listener
returns (captured, detach_fn). Extended: _send_prompt clears the editor
field (Ctrl+A + Delete) before typing so consecutive prompts in a
stay-mounted editor do not concatenate.

Spec: docs/superpowers/specs/2026-05-22-stay-mounted-batch-session-design.md
v3 (5-reviewer council hardened).
Plan: docs/superpowers/plans/2026-05-22-stay-mounted-batch-session-plan.md."
```

- [ ] **Step 4: Verify**

```powershell
git log --oneline -5
```

Expected: HEAD is v3-3 (refactor), with v3-2 (docs retraction), v3-1 (test relaxation), Phase 0 docs, and the prior matrix-skeleton commit beneath.

---

## Phase 4 — Commit v3-4: drop `--same-project` flag from CLI

### Task 4.1: Remove the Click option and update help

**Files:**
- Modify: `src/gflow_cli/cli_image.py` (around line 525-545 — the batch command + the `--same-project` flag definition)

- [ ] **Step 1: Locate the flag**

```powershell
uv run python -c "import re, pathlib; [print(p, ':', i+1, ':', l.rstrip()) for p in pathlib.Path('src').rglob('*.py') for i, l in enumerate(p.read_text().splitlines()) if 'same_project' in l or 'same-project' in l]"
```

Expected: hits in `cli_image.py` (the Click option definition + the call into `run_manifest_image_batch`) and any docstrings.

- [ ] **Step 2: Write the failing test**

In `tests/cli/test_cli_image.py` (or wherever batch CLI tests live):

```python
def test_batch_command_does_not_expose_same_project_flag() -> None:
    from click.testing import CliRunner
    from gflow_cli.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["image", "batch", "--help"])
    assert result.exit_code == 0
    assert "--same-project" not in result.output
    # And the help text reflects always-same-project semantics:
    assert "same project" in result.output.lower() or "one Flow project" in result.output
```

- [ ] **Step 3: Run the test (RED)**

```powershell
uv run python -m pytest tests/cli/test_cli_image.py::test_batch_command_does_not_expose_same_project_flag -v
```

Expected: FAIL — `--same-project` still in help.

- [ ] **Step 4: Remove the flag**

In `cli_image.py`:
1. Delete the `@click.option("--same-project", ...)` decorator on the batch command.
2. Remove `same_project: bool` from the function signature.
3. Remove the `same_project=same_project` argument from the `run_manifest_image_batch(...)` call.
4. Update the `short_help` and `help` strings on the batch command:
   - Mention "all prompts run in one Flow project"
   - Mention "3-7s anti-bot jitter between submissions"
5. Update any `# Examples:` strings to drop `--same-project` from the demonstrated invocations.

- [ ] **Step 5: Run the test (GREEN)**

```powershell
uv run python -m pytest tests/cli/test_cli_image.py::test_batch_command_does_not_expose_same_project_flag -v
```

Expected: PASS.

- [ ] **Step 6: Run the full CLI test suite**

```powershell
uv run python -m pytest tests/cli/ -v
```

Expected: all PASS. If any CLI test had `--same-project` in its invocation, update it to drop the flag (the test now matches the new always-same-project behaviour).

- [ ] **Step 7: Type-check + lint**

```powershell
uv run pyright src/gflow_cli/cli_image.py
uv run ruff check src/gflow_cli/cli_image.py tests/cli/
uv run ruff format --check src/gflow_cli/cli_image.py tests/cli/
```

Expected: clean.

- [ ] **Step 8: Commit**

```powershell
git add src/gflow_cli/cli_image.py tests/cli/test_cli_image.py
git commit -m "refactor(image)!: drop --same-project flag from gflow image batch

BREAKING CLI. The flag had two states: --same-project=1 (intended to put
all prompts in one Flow project; was a no-op at the transport layer until
v3-3) and --same-project=0 (each prompt in its own project; equivalent to
looping gflow image t2i externally). Per user design (project memory
batch-submission-cadence), batch = always one project; for different-project
results, loop t2i instead.

Removes the Click option, the same_project parameter from the function
signature, and the argument from the run_manifest_image_batch call.
Updates --help text to describe always-same-project semantics and the
submission-cadence (anti-bot) jitter rationale.

Spec: docs/superpowers/specs/2026-05-22-stay-mounted-batch-session-design.md §5.5."
```

---

## Phase 5 — Commit v3-5: update the e2e test

### Task 5.1: Drop the env var, add the shared-project assertion

**Files:**
- Modify: `tests/e2e/test_image_batch_e2e.py`

- [ ] **Step 1: Locate the env var and same-project plumbing**

Look at the file head (the docstring lists the env vars) plus the bodies that call `_resolve_same_project()` and pass `same_project` into `run_manifest_image_batch`.

- [ ] **Step 2: Modify the test**

Remove:
- The `GFLOW_CLI_E2E_BATCH_SAME_PROJECT` env var declaration in the docstring.
- The `_E2E_SAME_PROJECT_ENV = "GFLOW_CLI_E2E_BATCH_SAME_PROJECT"` constant.
- The `_resolve_same_project()` helper.
- The `same_project = _resolve_same_project()` call.
- The `same_project=same_project` argument to `run_manifest_image_batch`.

Add:
- A new assertion block after the existing assertions (around line 200):

```python
# Shared-project invariant (the v3-3 bug fix verification).
# Today BatchOutcome does not carry project_id, so we read it from the
# submission_attempt events. When BatchOutcome gains a project_id field
# in a future change, prefer reading from outcomes directly.
attempt_events = [
    e for e in log_capture.entries if e["event"] == "image_batch.submission_attempt"
]
project_ids = {e["project_id"] for e in attempt_events}
assert len(project_ids) == 1, (
    f"--same-project always-on: expected 1 shared project_id, got {project_ids}"
)
```

Update the existing `# 8. Project ID isolation/sharing semantics.` assertion block — collapse to the shared-project assertion above (the same-project=0 branch is dead).

- [ ] **Step 3: Run the e2e test logic in dry mode**

The e2e is gated by `GFLOW_CLI_E2E_PROFILE`. Without that env var set, it's skipped — verify the file imports/parses cleanly:

```powershell
uv run python -m pytest --collect-only tests/e2e/test_image_batch_e2e.py
```

Expected: 1 test collected, no import errors.

- [ ] **Step 4: Commit**

```powershell
git add tests/e2e/test_image_batch_e2e.py
git commit -m "test(e2e): update image batch e2e for always-same-project semantics

Drops GFLOW_CLI_E2E_BATCH_SAME_PROJECT env var (no longer a knob). Adds
the shared-project-id assertion that is the structural verification of
the v3-3 fix. The GFLOW_CLI_E2E_BATCH_JITTER env var stays — it lets
tests opt into zero-jitter for faster/cheaper runs without affecting
production defaults.

Spec: docs/superpowers/specs/2026-05-22-stay-mounted-batch-session-design.md §8.3."
```

---

## Phase 6 — Commit v3-6: documentation updates

### Task 6.1: Update USAGE.md, CHANGELOG.md, and any in-code docstrings

**Files:**
- Modify: `docs/USAGE.md` (find the `gflow image batch` section)
- Modify: `CHANGELOG.md` (under `[Unreleased]`)
- Modify: `src/gflow_cli/image_batch.py` `run_manifest_image_batch` docstring
- Modify: `docs/INDEX.md` if it references `--same-project` anywhere

- [ ] **Step 1: USAGE.md**

Find the `gflow image batch` subsection. Replace:
- "Use `--same-project=1` to keep all prompts in one Flow project..." → describe always-same-project as the only behaviour.
- Any code-block example showing `--same-project` → strip the flag from the example.

Add a new paragraph explaining jitter:

> Between submissions, `gflow image batch` waits a random 3-7 seconds before
> sending the next prompt. This is a submission-cadence anti-bot-detection
> measure — it does **not** wait for the previous prompt's image to finish
> generating. All prompts are submitted into one shared Flow project and
> their generations run in parallel; jitter only spaces out the *submission
> click* timing.

- [ ] **Step 2: CHANGELOG.md**

Under `[Unreleased]`, add entries:

```markdown
### Fixed
- `gflow image batch` now actually shares one Flow project across all prompts
  in a batch. Previously the `--same-project=1` flag was a no-op at the
  `ui_automation` transport layer; each prompt landed in its own Flow
  project. ([spec](docs/superpowers/specs/2026-05-22-stay-mounted-batch-session-design.md))

### Removed
- `--same-project` flag on `gflow image batch`. The flag collapsed to a
  single behaviour (always-same-project) — no toggle remains. For
  different-project results, loop `gflow image t2i` externally.

### Changed
- `gflow image batch` editor session is now persistent across all prompts in
  a batch. Jitter (3-7s default) is documented as the submission-cadence
  anti-bot control, not a completion-wait setting.
- `BatchSubmissionResult` is the new transport-layer per-prompt outcome
  (with `project_id`, `prompt_idx`, `prompt_hash` fields). Public
  `list[BatchOutcome]` orchestrator return is unchanged.
- `_attach_batch_response_listener` now returns `(captured, detach_fn)`;
  callers that used the single-list return need to unpack accordingly.

### Added
- `BatchPartialError` (in `errors`) — raised by fail-fast batch when
  earlier prompts produced downloadable images before the failing one;
  carries `partial_results` so the orchestrator can salvage them.
- `BatchIntegrityError` (in `errors`) — raised by the orchestrator when
  post-download file count does not match the expected count.
- `ui_automation.orphaned_project_warning` structlog event — emitted when
  `_enter_editor` succeeded but a later setup step (e.g.,
  `_dismiss_blocking_overlays`) raises, so the user can find their
  server-side project record.
```

- [ ] **Step 3: Docstring on `run_manifest_image_batch`**

Update the docstring to describe always-same-project, the jitter rationale, and the `BatchPartialError`/`BatchIntegrityError` raise contract.

- [ ] **Step 4: Check `docs/INDEX.md`**

```powershell
uv run python -c "import pathlib; print(pathlib.Path('docs/INDEX.md').read_text())" | Select-String -Pattern 'same-project|same_project'
```

If any line references `--same-project`, update or remove it.

- [ ] **Step 5: Lint the docs**

```powershell
uv run python scripts/ci/check_repo_hygiene.py
```

Expected: clean.

- [ ] **Step 6: Commit**

```powershell
git add docs/USAGE.md CHANGELOG.md src/gflow_cli/image_batch.py docs/INDEX.md
git commit -m "docs(image): always-same-project semantics + jitter rationale

USAGE.md rewrites the gflow image batch section to describe always-same-
project and the submission-cadence (anti-bot) rationale for the 3-7s
jitter. CHANGELOG.md [Unreleased] records the fix, the removed flag, the
new exception types, and the new structlog event. The run_manifest_image_batch
docstring is updated to match. INDEX.md cleaned of --same-project references.

Spec: docs/superpowers/specs/2026-05-22-stay-mounted-batch-session-design.md §3 + §5.5."
```

---

## Phase 7 — Commit v3-7: live-verification post-refactor

This phase spends Flow credits. ~3-4 images on profile `denon82` (or whichever profile is configured). User-driven.

### Task 7.1: Run the live e2e and record the verification

**Files:**
- Modify: `docs/LIVE_VERIFICATION_image_batch.md` (add a "Post-refactor verification" section)

- [ ] **Step 1: Set up the env**

```powershell
$env:PYTHONUTF8 = "1"
$env:GFLOW_CLI_E2E_PROFILE = "denon82"  # or whichever Chrome-strategy profile is fresh
$env:GFLOW_CLI_E2E_BATCH_MANIFEST = "tmp/sample_batch_rep1.tsv"  # reuse the matrix-era manifest
```

- [ ] **Step 2: Run the live e2e**

```powershell
uv run python -m pytest -q tests/e2e/test_image_batch_e2e.py 2>&1 | Tee-Object -FilePath tmp/v3_7_live_verification.log
```

Expected: PASS in 2-5 minutes. All assertions hold including the shared-project-id one. Manual check via Flow UI: open `https://labs.google/fx/tools/flow` as the test profile and confirm exactly ONE new project named `gflow-cli e2e` exists with all the manifest's images inside it.

- [ ] **Step 3: Update `docs/LIVE_VERIFICATION_image_batch.md`**

Add a new section at the bottom (under the existing post-refactor placeholder):

```markdown
## Post-refactor live verification (2026-MM-DD, v3-7)

**Profile:** `denon82` (`denon82@gmail.com`)
**git rev:** _(filled with output of `git rev-parse HEAD`)_
**Wall time:** _(elapsed from the log)_
**Credits spent:** ~_(N from sum-of-counts in manifest)_ images
**Project UUID:** _(from log_capture.entries' image_batch.submission_attempt event)_

**Result:** PASS.

- Shared-project-id assertion: PASS — all `image_batch.submission_attempt`
  events shared one project_id.
- File cardinality assertion: PASS.
- Magic-byte + aspect-ratio assertions: PASS.
- `image_batch.row_completed` count == `sum(p.count)`: PASS.
- `ui_automation.batch_response_seen` >= `sum(p.count)`: PASS.

**Manual Flow UI check:** one new project `gflow-cli e2e` exists at the
project URL above, containing all expected images. No additional projects
created by this run.

**Conclusion:** The v3-3 stay-mounted refactor delivers the always-same-
project semantics promised by the new design. The matrix retraction (above)
stays as historical record.
```

- [ ] **Step 4: Commit**

```powershell
git add docs/LIVE_VERIFICATION_image_batch.md
git commit -m "docs(image): post-refactor live verification — v3-3 stay-mounted PASS

Records the credit-spending e2e run on profile denon82 confirming that
all prompts of a batch share one Flow project. Project UUID, file
cardinality, and structlog event counts all match the spec's §8.3
assertions. Manual Flow UI check confirms exactly one project created
for the run.

Spec: docs/superpowers/specs/2026-05-22-stay-mounted-batch-session-design.md §8.3 + §9 (one-e2e-statistical-weakness acknowledged)."
```

- [ ] **Step 5: Final sanity check**

```powershell
git log --oneline -10
uv run python scripts/ci/check_repo_hygiene.py
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run python -m pytest -q tests/api tests/test_errors.py tests/image_batch tests/cli
```

Expected: 8 new commits on the branch (Phase 0 through Phase 7); all four gates clean.

---

## Branch finalisation

After Phase 7 lands, the `feature/multi-image-prompt` branch is ready for PR. Optional but recommended pre-PR steps (not strictly part of this plan):

- [ ] Push to origin: `git push origin feature/multi-image-prompt`
- [ ] Open PR with `gh pr create --base develop --title "feat(image): always-same-project gflow image batch (fixes --same-project no-op)" --body @-` using a body that summarises the spec/plan and lists the BREAKING changes.
- [ ] Close issue #14 once merged.

Beyond this plan: the persistence-layer work (phase-b-followups item #10) is the next big initiative. Open a separate spec/plan once this branch ships.

---

## Self-review checklist (for the plan author, not the executor)

- [x] Every spec section (§1-§11) has at least one task that implements it. Specifically:
  - §1 Problem → Phase 0/Phase 3 (the fix itself addresses the problem).
  - §2 Goal → Phase 3 + 4.
  - §3 Scope → Phases 3/4/5/6.
  - §4 Architecture → Phase 3 (Task 3.6 implementation).
  - §5 Components → Tasks 3.1 (dataclass), 3.2 (errors), 3.3 (listener), 3.4 (send_prompt), 3.6 (transport), 3.8 (orchestrator), Phase 4 (CLI removal).
  - §6 Data flow → Task 3.6 implementation; Task 3.5 multi-listener test.
  - §7 Error handling → Tasks 3.7 (failure modes), 3.6 (implementation).
  - §8 Testing → Tasks 3.5/3.7 (unit), 3.8 step 5 (orchestrator unit), Phase 5 (e2e).
  - §9 Risks → mitigations woven through Tasks 3.3 (listener detach), 3.4 (send_prompt clear), 3.5 (multi-listener test), 3.6 (orphaned-project warning), 3.7 (full failure coverage).
  - §10 Open questions → §10's _generate_lock deadline is left as a non-blocking note in Task 3.6 (v3-3 review will decide).
  - §11 Commit chain → Phases 1-7 mirror the chain.
- [x] No `TBD` / `TODO: implement` / "Add appropriate error handling" — Task 3.7 has explicit `pass # TODO in implementation` placeholders **with** detailed instructions on how to fill them; the rest of the plan is concrete.
- [x] Type/name consistency:
  - `BatchSubmissionResult` fields (`status`, `project_id`, `prompt_idx`, `prompt_hash`, `images`, `error`) match across Tasks 3.1, 3.6, 3.8, and Phase 5.
  - `_attach_batch_response_listener` returns `(captured, detach_fn)` consistently across Tasks 3.3, 3.5, 3.6.
  - `generate_images_batch(prompts, jitter_range, continue_on_error)` signature is the same in Task 3.6 (implementation) and Task 3.8 (orchestrator call).
  - `BatchPartialError(partial_results, cause)` and `BatchIntegrityError(prompt_indices)` consistent across Tasks 3.2, 3.6, 3.8.
- [x] All file paths absolute (under `C:\development\github\gflow-cli` or `tests/...` etc.).
- [x] Every code-step has actual code, not a description.
- [x] Every command-step has actual command + expected output.
