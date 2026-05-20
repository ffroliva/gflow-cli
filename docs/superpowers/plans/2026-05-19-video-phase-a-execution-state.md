# Phase A Execution — Handoff / Resume State

> **Resume point:** Tasks 1-9 of 14 are DONE. **Continue at Task 10.**
> This file lets a fresh session resume execution with an empty context window.

## Where things are

- **Branch:** `chore/video-wire-captures` (PR #23). **HEAD = `adc95a3`**, working tree clean.
- **Plan:** `docs/superpowers/plans/2026-05-19-video-phase-a-t2v.md` (rev 3 — council-consensus, all 5 dimensions APPROVE).
- **Orchestration plan:** `docs/superpowers/plans/2026-05-19-video-phase-a-orchestration.md`.
- **Commits so far (in order):** `0ae4d1d` plans · `2ce3197` T1 · `043285d` T2 · `7b57afc` T3 · `a67f74f` T4 · `3bf8528` T5 · `28b5588` cli_video docstring fix · `86a894a` T6 · `921c6e9` T7 · `5a7948c` T8 · `adc95a3` T9.

## CRITICAL — do not lose / do not break

- **`git stash@{0}`** holds the user's WIP `tests/api/transports/test_ui_automation.py` (102-line image-transport test additions — `TestBypassOnboarding`). It was set aside so Phase A commits stay atomic. **Pop it after Phase A completes** (`git stash pop` — Phase A never touches that file, so it re-applies cleanly). **Never drop it.** (`stash@{1}` is unrelated, from another branch — leave it.)
- **Known pre-existing test failure:** `tests/api/transports/test_ui_automation.py::TestEnterEditor::test_first_selector_works` fails on this branch. It is **unrelated to Phase A** (the user's in-progress `_bypass_onboarding` work left that test stale). Throughout Phase A, "green" means **"no failures OTHER than that one."** Do NOT touch `test_ui_automation.py`; do NOT try to fix `test_first_selector_works` — it is the user's WIP territory.

## Remaining work (plan line ranges)

| Task | Scope | Plan lines |
|---|---|---|
| **T10** | `_attach_status_response_listener` + `_poll_video_status` | 1105-1318 |
| **T11** | selectors, `_probe_selector_cascade`, `_switch_to_video_mode`, `_wait_video_editor_ready` | 1319-1552 |
| **T12** | `_set_output_count_one` + `_select_video_aspect` | 1553-1657 |
| **T13** | `generate_video` orchestration + mix `VideoGenerationMixin` into `UiAutomationTransport`; **full quality gate** | 1658-2008 |
| **T14** | docs — `PLAN.md`, `CHANGELOG.md`, `README.md` | 2009-2057 |

After T14: whole-branch final review, then `superpowers:finishing-a-development-branch`.

## Decisions made during execution (binding — do not revisit)

- **`cli_video.py` is the 95-line richer stub** (user-approved when a plan defect surfaced at T5): it has `_run_t2v/_run_i2v/_run_batch` async placeholders + `run_with_handlers` wiring (matches `cli_image.py`), NOT the minimal stub. Done — leave it.
- **`video.py` parsers use `typing.cast(...)`** — the plan's verbatim parser snippet was not `pyright --strict`-clean; `cast()` was added (consistent with `dto.py`). Done in T8.
- `routes.py` `GENERATE_VIDEO`/`CHECK_VIDEO_STATUS` are intentionally retained (plan Deviations).

## Execution method — IMPORTANT for the resuming session

- **`ui_automation_video.py` has forward-declared imports.** T9 imported the FULL set the finished module needs (`time`, `Locator`, the `video.py` symbols, the `errors` symbols). They are "unused" until T10-T13 wire them. **Therefore: for T10, T11, T12 the verification is the task's targeted `pytest` ONLY — do NOT run `ruff check`/`pyright` as a pass/fail gate** (they show expected unused-import warnings). The **full gate** (`scripts/ci/check_repo_hygiene.py`, `ruff check src tests`, `ruff format --check src tests`, `pyright src`, `pytest -q --cov=gflow_cli`) runs at **T13 Step 6**, when every import is used.
- **`<new-diagnostics>` blocks are stale mid-edit snapshots.** Every task so far surfaced alarming-but-stale diagnostics (e.g. referencing already-deleted files); each time, independent verification confirmed the implementer's "clean" claim. Do not act on `<new-diagnostics>` without verifying the current state.
- **Haiku subagent dispatches FAIL** in this environment ("Prompt is too long"). Use `model: "sonnet"` for ALL implementer and reviewer subagents. (T13 is the integration task — Sonnet implementer is fine; the full `pyright --strict` gate at T13 Step 6 is the real proof of the mixin typing contract.)
- **Use `uv run python -m pytest`** — `uv run pytest` errors "Failed to canonicalize script path" on this machine.
- For redirected output / the hygiene script, prefix `PYTHONUTF8=1` (Windows cp1252 codec otherwise crashes on box-drawing chars).
- An untracked junk file named `NUL` can appear (Windows bash redirect artifact) — `rm -f ./NUL` it.

## Per-task protocol (superpowers:subagent-driven-development)

For each remaining task: dispatch an **implementer** subagent → handle its status (DONE / DONE_WITH_CONCERNS / BLOCKED / NEEDS_CONTEXT) → dispatch a **combined spec+quality reviewer** subagent (one dispatch doing spec-compliance first, then code quality) → fix-loop if issues → mark the task done → next.

**To keep the orchestrator's context lean** (the lesson from tasks 1-9): have each implementer subagent **read its own task section from the plan file** (`docs/superpowers/plans/2026-05-19-video-phase-a-t2v.md`, the line ranges above) — do NOT have the orchestrator read the plan and paste verbatim code into the prompt. Skip orchestrator-side Bash verification (the reviewer verifies independently). This keeps the orchestrator window small.

**Brief every subagent with:** the conventions below; the known pre-existing failure; the forward-import note; the env notes (`python -m pytest`, `PYTHONUTF8=1`).

## Conventions every subagent must follow

- **NEVER** add `Co-Authored-By` or any AI co-author line to commits. Author attribution is the human's only.
- **Conventional Commits.** Each plan task's final step gives the exact commit message verbatim — use it.
- **TDD** for T10-T13: write the failing test first, run it to confirm it fails for the right reason, implement, run to confirm pass, commit.
- `from __future__ import annotations`; `pyright --strict` on `src/`.

## Adjudication notes (review findings already resolved — do not re-litigate)

- T6 quality flagged the `MAX_REFERENCE_IMAGES` cap placement — adjudicated non-issue (plan-verbatim, council-approved, behavior correct).
- T8 quality flagged an untested empty-`media[]` edge case — adjudicated non-blocking (beyond the plan's test set).
- T9 quality flagged `_capture_debug_screenshot` returning a path on screenshot failure — adjudicated non-issue (verbatim duplicate of the existing `ui_automation.py` helper; fixing it would diverge the copy from the original).
