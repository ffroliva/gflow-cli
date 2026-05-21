# Multi-image prompt — Orchestration Plan

> **For agentic workers:** REQUIRED SUB-SKILLS: `superpowers:dispatching-parallel-agents` (parallel council/review work) and `superpowers:subagent-driven-development` (per-task Claude implementer dispatch). The Coordinator owns task progress, review gates, escalation, and user go/no-go decisions.

**Companion docs:**

- [`2026-05-21-multi-image-prompt-design.md`](../specs/2026-05-21-multi-image-prompt-design.md) — v3 design spec (council-hardened, full `--seed` cleanup).
- [`2026-05-21-multi-image-prompt.md`](2026-05-21-multi-image-prompt.md) — v2 implementation plan (council-hardened, 25 findings applied).
- Prior canonical orchestration template: [`docs/superpowers/plans/2026-05-14-shell-multi-prompt/2026-05-14-shell-multi-prompt-orchestration.md`](2026-05-14-shell-multi-prompt/2026-05-14-shell-multi-prompt-orchestration.md).

**Architecture:** Coordinator-led implementation with cross-model review gates. The Coordinator (Claude Opus 4.7 in this session) owns the master state, dispatches per-task subagents to the model best suited for that task (Claude Opus, Codex, or Gemini Pro), runs cross-model code review on every commit, consolidates findings, and surfaces blockers to the user. **Quality-first priority** (see §11). Live-credit-spending phases stay on Claude/user pair to keep the human in the loop.

**Branch:** `feature/multi-image-prompt` (already created; carries the 5 planning commits 85d43e8..9b63b19).

## 0. Model availability — verified 2026-05-21

Probed at orchestration-plan commit time. Update §1 and §4 dispatch commands if these change:

| CLI | Default model | Other usable models | Verified | Notes |
|---|---|---|---|---|
| `codex` (v0.131.0) | **GPT-5 Codex** | per `~/.codex/config.toml` overrides | ✅ headless `codex exec` works | Auth: completed (logged in). |
| `gemini` (v0.42.0) | `gemini-3.1-flash-lite` (auto-fallback) | `gemini-2.5-pro` (preferred, 429 intermittently) | ⚠️ **rate-limited** on OAuth-personal tier | `gemini-3-pro` / `gemini-3-flash` return 404 (not exposed to this account). API endpoint: `cloudcode-pa.googleapis.com`. |

**Practical consequence:** every dispatch that *must* run on Gemini has a **Claude-subagent fallback**. The orchestration tolerates Gemini unavailability gracefully — see §4.7 Fallback policy. If Gemini quota resets and capacity is available, the Coordinator routes to Gemini for the long-context tasks (Phase 4 e2e draft, cross-model spec reviews); otherwise it routes to a Claude Opus subagent.

**Optional user mitigation:** switch Gemini auth from OAuth-personal to a paid API key (`gemini auth --api-key`) to get reliable headless quota. The orchestration plan does not require this — it works either way.

---

## 1. Roles

| Role | Model | Invocation | What it owns |
|---|---|---|---|
| **Coordinator** | Claude Opus 4.7 (this session) | n/a — me | Master plan state, dispatch decisions, review consolidation, verdict on disagreements, user escalation, git operations (push/PR/branch), credit-spending Phase 6 matrix. |
| **Implementer A — Claude** | Claude Sonnet 4.6 / Opus 4.7 via `Agent(subagent_type=general-purpose)` | inline via Agent tool | Judgment-heavy tasks: count-selector refactor (Phase 1), Phase 3.1 carry-forward, e2e dry-run verification, judgment commits. |
| **Implementer B — Codex** | `codex-cli 0.131.0` (GPT-5-codex) | `codex exec` (headless) | Mechanical pattern-based work: Phase 1b (`--seed` deletion across many sites), Phase 3.2 TDD events, Phase 3.3/3.4 fixtures, Phase 5 docs templating. |
| **Implementer C — Gemini** | Gemini 2.5 Pro (default) via `gemini -p` | `gemini -p` (headless) | Long-context, holistic work: Phase 4 e2e file draft (needs spec + e2e patterns + fixture references all in context). |
| **Reviewer R-Code** | Codex `codex review` | `codex review` mode | Code-quality review of any commit. Runs by default after every implementer commit unless that commit was authored by Codex. |
| **Reviewer R-Spec** | Gemini headless | `gemini -p` | Spec-conformance + cross-model lens. Runs on commits #1, #1b, #2, and the pre-PR council. |
| **Reviewer R-Claude** | Claude Sonnet 4.6 via `Agent(subagent_type=general-purpose)` | Agent tool | TDD discipline + security review. Runs on Phase 2.0 (red tests), Phase 4 (e2e assertions), Phase 6 evidence. |
| **User (Flavio)** | n/a | direct conversation | Authorisation for live credit spends (Phase 6 matrix sessions, AC6 verdict-driven re-run), final verdict calls on disputed reviews. Mandatory checkpoint before any `git push`. |

**Consolidation rule (from §questions answered):** Coordinator decides. Non-controversial fixes applied automatically; controversial calls surfaced to the user. No reviewer-veto deadlock.

---

## 2. Task Matrix

Numbering follows the v2 implementation plan's phases.

| # | Task | Wave | Implementer | Reviewer | Live? | Depends on |
|---|---|---|---|---|---|---|
| 0.1 | Cut `feature/multi-image-prompt` from develop | 0 | Claude (done) | none | no | none |
| 0.2 | Tree-replay PR #35 onto branch | 0 | Claude | Codex (R-Code) | no | 0.1 |
| 1.1 | Commit #1 — native xN count selector | 1 | **Claude** | Codex + Gemini (R-Code + R-Spec) | no | 0.2 |
| 2.0 | Commit #1b red tests (TDD) | 1 | **Codex** | Claude (R-Claude TDD lens) | no | 1.1 |
| 2.1 | Commit #1b — strip `--seed` from CLI | 2 | **Codex** | Claude | no | 2.0 |
| 2.2 | Commit #1b — strip `seed`/`batch_id` from public client | 2 | **Codex** | Claude | no | 2.0 |
| 2.3 | Commit #1b — update tests | 2 | **Codex** | Claude | no | 2.1, 2.2 |
| 2.4 | Commit #1b — verify + commit (BREAKING) | 2 | Claude | Codex + Gemini council | no | 2.3 |
| 3.1 | Commit #2 prep — stage image_batch.py from PR #35 | 3 | Claude | n/a | no | 2.4 |
| 3.2 | Commit #2 — observability events (TDD) | 3 | **Codex** | Claude (R-Claude TDD lens) | no | 3.1 |
| 3.3 | Commit #2 — malformed-row fixture + parametrized test | 3 | **Codex** | Claude | no | 3.1 |
| 3.4 | Commit #2 — sample manifests | 3 | **Codex** | n/a (file-only) | no | 3.1 |
| 3.5 | Commit #2 — verify + commit | 3 | Claude | Codex + Gemini | no | 3.2, 3.3, 3.4 |
| 4.0 | Commit #3 prep — `uv add --dev pillow` | 4 | Claude | n/a | no | 3.5 |
| 4.1 | Commit #3 — e2e file draft | 4 | **Gemini** | Codex + Claude | no | 4.0 |
| 4.2 | Commit #3 — static checks only (no smoke run) | 4 | Claude | n/a | no | 4.1 |
| 4.3 | Commit #3 — commit | 4 | Claude | n/a | no | 4.2 |
| 5.1 | Commit #4 — USAGE.md | 5 | **Codex** | Claude | no | 4.3 |
| 5.2 | Commit #4 — CHANGELOG.md | 5 | **Codex** | Claude | no | 4.3 |
| 5.3 | Commit #4 — INDEX.md | 5 | **Codex** | n/a | no | 4.3 |
| 5.4 | Commit #4 — verify + commit | 5 | Claude | Codex + Gemini | no | 5.1, 5.2, 5.3 |
| 6.1 | Phase 6 — write evidence skeleton | 6 | Claude | n/a | no | 5.4 |
| 6.2 | Phase 6 — matrix session 1 (R1, R2, R3 × N=3) | 6 | **Claude + User** | n/a | **YES** | 6.1 |
| 6.3 | Phase 6 — matrix session 2 (≥2h later) | 6 | **Claude + User** | n/a | **YES** | 6.2 |
| 6.4 | Phase 6 — verdict | 6 | Claude | Gemini (cross-model evidence-reading lens) | no | 6.3 |
| 6.5 | Commit #5a — evidence file | 6 | Claude | n/a | no | 6.4 |
| 6.6 | Commit #5b — code/docstring per verdict | 6 | Claude | Codex + Gemini | no | 6.5 |
| 7.0 | Rebase on develop | 7 | Claude | n/a | no | 6.6 |
| 7.1 | Final sweep (AC1–16) | 7 | Claude | Codex (R-Code) | no | 7.0 |
| 7.1.6 | AC6 verdict-driven e2e re-run | 7 | **Claude + User** | n/a | **YES** | 7.1 |
| 7.2 | Push + open PR | 7 | Claude | n/a | no | 7.1.6 |
| 7.PRE-PR | Pre-PR council (Claude + Codex + Gemini in parallel) | 7 | n/a (review only) | all three | no | 7.2 |
| 7.3 | Close PR #35 | 7 | Claude | n/a | no | 7.2 |
| 8.* | Post-merge cleanup | 8 (later) | Claude | n/a | no | merge of new PR |

---

## 3. Parallel Execution Waves

Dependencies above define the partial order. Within each wave, tasks may run concurrently. Crossing wave boundaries requires the prior wave's gate to pass.

```
Wave 0 — Bootstrap (already complete for 0.1; 0.2 next)
  └── 0.2 [Claude]                                  ─── seq

Wave 1 — Count selector + TDD red
  ├── 1.1 [Claude]                                  ─┐
  └── 2.0 [Codex] (red tests; can start once 1.1     │ partial parallel:
       lands, since #1b reds depend on #1's          │ 2.0 trails 1.1 closely
       _drive_images_generation contract)            ─┘

Wave 2 — Seed cleanup
  ├── 2.1 [Codex]                                   ─┐
  ├── 2.2 [Codex]                                   ─┤ parallel
  └── 2.3 [Codex]                                   ─┘
  └── 2.4 [Claude verify + commit + council]        ─── gate

Wave 3 — Feature + observability + fixtures
  ├── 3.1 [Claude]  (carry-forward)                 ─── seq (others depend on it)
  ├── 3.2 [Codex]   (TDD events)                    ─┐
  ├── 3.3 [Codex]   (malformed fixture)             ─┤ parallel after 3.1
  └── 3.4 [Codex]   (happy fixtures)                ─┘
  └── 3.5 [Claude verify + commit + council]        ─── gate

Wave 4 — E2E
  ├── 4.0 [Claude]  (uv add pillow)                 ─── seq
  └── 4.1 [Gemini]  (e2e draft)                     ─┐
  └── 4.2 [Claude]  (static gates only)             ─┘ seq within wave
  └── 4.3 [Claude]  (commit; NO live run)           ─── commit

Wave 5 — Docs
  ├── 5.1 [Codex]                                   ─┐
  ├── 5.2 [Codex]                                   ─┤ parallel
  └── 5.3 [Codex]                                   ─┘
  └── 5.4 [Claude verify + commit + council]        ─── gate

Wave 6 — Matrix (LIVE)
  └── 6.1 [Claude]                                  ─── seq
  └── 6.2 [Claude+User]      ─ session 1 (LIVE)
       └── 6.2 step 2.5 abort gate                  ─── may short-circuit
  └── 6.3 [Claude+User]      ─ session 2 (≥2h gap, LIVE)
  └── 6.4 [Claude + Gemini cross-model]              ─── verdict
  └── 6.5 [Claude]   (evidence commit)
  └── 6.6 [Claude + Codex + Gemini council]         ─── code/doc commit + council

Wave 7 — PR
  └── 7.0 [Claude] (rebase on develop)
  └── 7.1 [Claude + Codex R-Code]   (AC1–16 sweep)
  └── 7.1.6 [Claude+User] (verdict-driven e2e, LIVE)
  └── 7.2 [Claude]   (push + open PR)
  └── 7.PRE-PR [Claude + Codex + Gemini in PARALLEL]  ─── final council
  └── 7.3 [Claude]   (close PR #35)

Wave 8 — Post-merge (later, user-triggered after PR merge)
  └── 8.1, 8.2, 8.3
```

**Maximum parallelism opportunities:**
- Wave 2 (three Codex tasks parallel) — single Codex shell session can pipeline these
- Wave 3 (three Codex tasks parallel after 3.1)
- Wave 5 (three Codex doc tasks parallel)
- Wave 7 Pre-PR council (3 reviewers truly parallel — independent processes)

**Sequential bottlenecks (cannot parallelize):**
- Wave 6 matrix runs — credit-spending, single profile, user must observe
- Wave 1 (1.1 → 2.0) — 2.0's tests depend on #1's contract
- Wave 4 (4.0 → 4.1 → 4.2 → 4.3) — pillow dep must land before e2e import

---

## 4. Per-Task Workflow

### 4.1 Standard Dispatch — Claude implementer

For tasks marked `[Claude]`:

```python
# Inside the Coordinator session (this conversation):
Agent(
    description="<5-word task summary>",
    subagent_type="general-purpose",
    prompt="""
You are a senior Python engineer working in `C:\\development\\github\\gflow-cli` on branch
`feature/multi-image-prompt`.

CONTEXT:
- Read `docs/superpowers/plans/2026-05-21-multi-image-prompt.md` Task <N.M> for the
  exact instructions.
- Read `docs/superpowers/specs/2026-05-21-multi-image-prompt-design.md` for design rationale.
- Repo conventions: `CLAUDE.md` (TDD mandatory, no print/import logging in src/, async-all-the-way,
  Conventional Commits, no AI co-author, output to tmp/).

TASK:
<Paste the task's steps verbatim from the implementation plan>

CONSTRAINTS:
- Do NOT commit. Stage changes only.
- Do NOT touch files outside the task's declared file list.
- Run all required local verification (`/gflow:check` scoped).
- Return a STRUCTURED REPORT (see below).

REPORT FORMAT (markdown):
## Files changed
- <path> — <one-line summary>
## Test results
<scoped pytest output excerpt>
## Lint results
<ruff/pyright output excerpt — should be clean>
## Open questions for Coordinator
- <if any>
"""
)
```

### 4.2 Standard Dispatch — Codex implementer

For tasks marked `[Codex]`. Codex runs in headless mode, writes its output, and returns. The Coordinator stages results into git.

**Pattern:**

```powershell
# Pre-write the prompt to a file so multi-line content is preserved.
$prompt = @'
<task-prompt; see template below>
'@
$prompt | Out-File -FilePath tmp/codex-prompt-<task-id>.md -Encoding UTF8

# Codex `exec` runs non-interactively. Output is captured.
codex exec --prompt-file tmp/codex-prompt-<task-id>.md `
  --output-format json `
  --working-dir "C:\development\github\gflow-cli" `
  2>&1 | Tee-Object -FilePath tmp/codex-output-<task-id>.log

# (If codex exec does not support --prompt-file in your version, pipe via stdin:)
# Get-Content tmp/codex-prompt-<task-id>.md | codex exec --json 2>&1 | Tee-Object ...

# Coordinator then reads tmp/codex-output-<task-id>.log, inspects changes
# with `git status` + `git diff`, and either accepts or sends a corrective
# follow-up prompt.
```

**Codex prompt template:**

```markdown
You are implementing a single task from a Python project's implementation plan.

WORKING DIRECTORY: C:/development/github/gflow-cli
BRANCH: feature/multi-image-prompt
ROOT-DOC: docs/superpowers/plans/2026-05-21-multi-image-prompt.md
SPEC: docs/superpowers/specs/2026-05-21-multi-image-prompt-design.md
PROJECT CONVENTIONS: CLAUDE.md (read first)

TASK: <paste the task's full step list verbatim>

CONSTRAINTS:
1. Edit only the files listed in the task's "Files:" header.
2. Do NOT commit. Stage with `git add` if you must, but prefer leaving unstaged
   so the Coordinator can inspect.
3. Do NOT touch CLAUDE.md, KNOWN_ISSUES.md, or any docs outside the task scope.
4. Run `uv run ruff check <changed paths>` and `uv run pyright <changed paths>`
   after editing. Iterate until clean.
5. Tests: run scoped pytest matching the task; report pass/fail counts.
6. If a code symbol or import name in the task instructions does not match the
   actual file (e.g., a typo in the plan), STOP and report — do not guess.

REPORT: After completing the task, emit a markdown report on stdout with:
## Files changed
## Test results
## Lint results
## Anything I did not understand or had to guess at
```

### 4.3 Standard Dispatch — Gemini implementer

For tasks marked `[Gemini]`. Used for Phase 4 (e2e file) and cross-model reviews. Gemini's strength here is large-context: read the spec + plan + existing e2e patterns + manifest fixtures in one shot.

**Pattern:**

```powershell
$prompt = @'
<task-prompt; see template below>
'@

gemini -m gemini-2.5-pro `
       -p $prompt `
       --include-directories "tests/e2e,docs/superpowers,src/gflow_cli/image_batch.py" `
       --output-format text `
       2>&1 | Tee-Object -FilePath tmp/gemini-output-<task-id>.log
```

**Gemini prompt template (for Phase 4 e2e draft):**

```markdown
You are drafting tests/e2e/test_image_batch_e2e.py for the gflow-cli project.

INPUTS (read these first):
- docs/superpowers/specs/2026-05-21-multi-image-prompt-design.md §7 (e2e design)
- docs/superpowers/plans/2026-05-21-multi-image-prompt.md Task 4.1 (exact step list)
- tests/e2e/test_video_t2v_e2e.py (canonical e2e pattern to mirror)
- tests/e2e/conftest.py (provides e2e_profile_dir fixture at line 25)
- src/gflow_cli/image_batch.py (parse_manifest_file, run_manifest_image_batch, BatchOutcome, BatchPromptItem)

OUTPUT: a single Python file written to tests/e2e/test_image_batch_e2e.py.

CONSTRAINTS:
1. Use `from __future__ import annotations` at the top.
2. Module-level `pytestmark = pytest.mark.e2e`.
3. e2e_profile_dir is a pytest fixture (NOT a function call).
4. Env-var schema EXACTLY: GFLOW_CLI_E2E_PROFILE (gate), GFLOW_CLI_E2E_BATCH_MANIFEST,
   GFLOW_CLI_E2E_BATCH_SAME_PROJECT, GFLOW_CLI_E2E_BATCH_JITTER.
5. Jitter override via DI: jitter_range=(0.0, 0.0). NOT monkeypatch.
6. tmp_path fixture for output dir.
7. Accept PNG / JPEG / WebP magic bytes. Anything else fails loud.
8. Aspect tolerance ±2%.
9. _resolve_manifest_path MUST assert "_invalid" not in stem.
10. try/except around run_manifest_image_batch dumping last_events.json on failure.
11. Project-ID assertion: same_project=1 → single ID; same_project=0 → reads
    project_id from submission_result events (since submission_attempt has the
    sentinel "<per-prompt>" in that mode).

VERDICT: emit only the file body. Do NOT include any preamble, postamble, or
markdown around the Python code.
```

### 4.4 Standard Review — Codex R-Code

After every implementer commit (except commits authored by Codex itself), dispatch Codex to review.

```powershell
$prompt = @'
You are reviewing a single git commit on branch feature/multi-image-prompt.

COMMIT: <SHA>
COMMIT MESSAGE:
<paste git log -1 SHA>

DIFF:
<paste git show SHA>

REVIEW AGAINST:
- docs/superpowers/specs/2026-05-21-multi-image-prompt-design.md (relevant section)
- docs/superpowers/plans/2026-05-21-multi-image-prompt.md (relevant task)
- CLAUDE.md (project conventions)

FOCUS:
- Python craft, SOLID, KISS, YAGNI
- Type-safety with pyright --strict
- Test coverage for the code paths added
- Conventional Commits compliance
- Hidden coupling, dead code, magic constants

OUTPUT: markdown report with sections:
## Severity: BLOCKER
## Severity: MAJOR
## Severity: MINOR / nit
## Things this commit got RIGHT
## Verdict: APPROVE | MINOR-FIXES | MAJOR-REVISION | REJECT
'@

codex review --prompt-file - <<< $prompt 2>&1 | Tee-Object tmp/codex-review-<SHA>.md
# Or, if your Codex version uses different flags:
# codex exec --review --prompt-file ... or codex exec "$prompt"
```

### 4.5 Standard Review — Gemini R-Spec

For cross-model spec-conformance reviews. Used on commits #1, #1b, #2 (BREAKING), and the pre-PR final council.

```powershell
$prompt = @'
You are reviewing a code change against an authoritative spec.

SPEC: docs/superpowers/specs/2026-05-21-multi-image-prompt-design.md (v3)
PLAN: docs/superpowers/plans/2026-05-21-multi-image-prompt.md (v2)
COMMIT: <SHA>
DIFF: <paste git show SHA, or rely on workspace inspection>

AUDIT QUESTIONS:
1. Does this commit deliver what the spec section says it must, no less and no more?
2. Are any spec out-of-scope items accidentally included?
3. Are commit-message conventions honoured (Conventional Commits, BREAKING CHANGE
   footer where required, no AI co-author)?
4. Does the diff introduce any hidden ambiguity, type drift, or test gap?
5. Are project conventions in CLAUDE.md respected (output to tmp/, no print/
   import logging, frozen dataclasses where applicable, async-all-the-way)?

VERDICT (one line at the end): PROCEED-AS-IS / MINOR-EDITS / MAJOR-REVISION
'@

gemini --approval-mode plan -p $prompt 2>&1 | Tee-Object tmp/gemini-review-<SHA>.md
```

### 4.6 Standard Review — Claude R-Claude (TDD/security)

Used on Phase 2.0 red tests, Phase 4 e2e assertions, Phase 6 evidence.

```python
Agent(
    description="TDD discipline + security review",
    subagent_type="general-purpose",
    prompt="""
You are a senior test architect + security reviewer.

REVIEW: commit <SHA> on branch feature/multi-image-prompt.

CONTEXT FILES:
- docs/superpowers/specs/2026-05-21-multi-image-prompt-design.md (spec v3)
- docs/superpowers/plans/2026-05-21-multi-image-prompt.md (plan v2)
- The diff: `git show <SHA>`

LENSES:
1. TDD red-green discipline — did this commit's tests fail before the implementation?
   (Inferable from commit order if the red commit precedes the green.)
2. Assertion strength — are tests pinning behaviour or just smoke?
3. Test isolation — fixture cleanup, no global state bleed.
4. Security — secrets, injection, path traversal, auth. (Most commits here are
   low risk but flag anything that touches `tmp/`, file ops, or env vars.)
5. CLAUDE.md compliance — output rule, no print, no AI co-author, etc.

OUTPUT format (markdown):
## Severity: BLOCKER / MAJOR / MINOR / nit
## Verdict: APPROVE | FIXES_REQUESTED | REJECT

Stay under 500 words.
"""
)
```

### 4.7 Fallback policy — Gemini unavailable

Verified at orchestration time (see §0): the `gemini` CLI is rate-limited on OAuth-personal auth. Every dispatch that prefers Gemini has a fallback path.

**Fallback rules:**

| Original assignment | If Gemini returns 429 / 404 / capacity error | If Gemini still failing after retry |
|---|---|---|
| Phase 4 e2e draft (Gemini implementer) | Retry once after 60s with `-m gemini-2.5-pro` | Route to Claude Opus subagent via `Agent(subagent_type=general-purpose)` with the same prompt template. **Document the fallback in commit #3's message body** ("e2e drafted by Claude Opus fallback — Gemini unavailable at 2026-05-21T<HH:MM>Z, status=429"). |
| G1 council Gemini reviewer | Retry once after 60s | Skip Gemini; G1 reduces to 2 reviewers (Codex + Claude). Coordinator notes in the consolidation report. |
| G2 council Gemini reviewer | Retry once after 60s | Skip Gemini; G2 verdict relies on Claude subagent only. Coordinator surfaces the verdict to the user with reduced confidence. |
| G3 council Gemini reviewer | Retry once after 60s | Skip Gemini; G3 reduces to 2 reviewers (Codex + Claude). Coordinator notes in PR description. |

**Detection:** if `gemini -p` output contains `status: 429`, `code: 404`, or `An unexpected critical error occurred`, treat as failure for the fallback decision.

**Quota-conscious dispatch order:** when Gemini IS available, run Gemini dispatches first in a given wave so we know early whether to fall back. Codex/Claude run in parallel as backup.

---

## 5. Council Gates

Three council gates fire in this plan. Each one runs 3 reviewers in parallel and the Coordinator consolidates.

### 5.1 Council G1 — Pre-commit-#1b (BREAKING change)

Triggered just before staging commit #1b. The #1b commit removes a documented user-facing flag and breaks the library API.

**Reviewers (parallel):**
- Codex `codex review` — code-quality lens on the diff
- Gemini `gemini -p` — spec-conformance + BREAKING-CHANGE-footer check
- Claude subagent (R-Claude) — TDD lens on the red tests; security lens on the deletion

**Verdict aggregation:** APPROVE if 3/3 say PROCEED-AS-IS. MINOR-EDITS if any reviewer flags but Coordinator can apply patches inline. MAJOR-REVISION if any reviewer flags a regression — escalate to user.

### 5.2 Council G2 — Post-Phase-6 verdict (jitter DROP vs KEEP)

Triggered after matrix completes. The §8 verdict drives commit #5b.

**Reviewers (parallel):**
- Claude subagent — read the evidence file, classify the matrix outcomes per §8 decision rule
- Gemini `gemini -p` — cross-model evidence reader; verify the decision rule was applied correctly

**Verdict aggregation:** both must agree on DROP / KEEP / INCONCLUSIVE-KEEP. If they disagree, surface to user with both interpretations.

### 5.3 Council G3 — Pre-PR final (before `gh pr create`)

Triggered just before opening the new PR. Validates the full branch state against AC1–16.

**Reviewers (parallel):**
- Codex `codex review` on the entire diff `develop..HEAD`
- Gemini `gemini -p` on the spec-conformance for all 7 commits
- Claude subagent on TDD + security + project conventions

**Verdict aggregation:** ALL three must say APPROVE for the PR to proceed. If any blocks, fix in place and re-run the council on the relevant commit.

---

## 6. Quality Gates Between Phases

After every implementer commit (before the next phase starts):

1. **Static gates** (mandatory, scoped to changed dirs per `full-test-suite-ooms` memory):
   - `uv run python scripts/ci/check_repo_hygiene.py`
   - `uv run ruff check <changed paths>`
   - `uv run ruff format --check <changed paths>`
   - `uv run pyright <changed paths>`
   - `uv run pytest -q <changed paths>`

2. **Stale-test grep** (after #1b, #2, and at final sweep):
   - `Select-String -Path tests/ -Pattern 'not yet available|temporarily unavailable|5-prompt cap|--seed|seed=42|seed=1\b' -SimpleMatch -Recurse`
   - Expected: zero hits outside `tests/api/test_image_body.py` (body builder tests still take `seed=`).

3. **Reviewer verdict** (per §4.4–4.6 + §5):
   - Must be APPROVE or MINOR-EDITS-Coordinator-applied. MAJOR-REVISION halts the wave.

4. **Coordinator checkpoint:** the Coordinator writes a one-line entry to the state log (see §8) before proceeding.

---

## 7. Escalation Rules

The Coordinator stops and escalates to the user under these conditions:

1. **Reviewer flags a BLOCKER that affects user behaviour** (regression in `gflow image t2i` single-prompt path, `--seed` re-introduction by mistake, project_id wiring lost).
2. **BREAKING change discovered outside #1b** — only #1b is allowed to break public API in this PR.
3. **Matrix mid-abort** (Task 6.2 Step 2.5) — Coordinator notifies user, records verdict=KEEP, asks user to confirm before committing #5b.
4. **Reviewer disagreement after consolidation attempt** — if Coordinator cannot tell which reviewer is right, surface both with summaries.
5. **Live-credit threshold exceeded** — if any single matrix run uses >10 image generations (suggests a bug in the e2e), abort and notify user.
6. **`pyright --strict` regression** anywhere outside the changed file list — implies the change rippled further than expected.
7. **Authorship leak** — if `git log --format='%an' origin/develop..HEAD` shows anything other than the human user, halt and hard-reset (spec §6.B / D6 forbids `--amend --reset-author`; redo the offending phase).
8. **`/gflow:check` fails on a previously-clean phase** — implies a later phase regressed an earlier one; halt and bisect.

---

## 8. State Tracking

The Coordinator maintains state in two places:

### 8.1 Live state — `tmp/orchestration-state.md`

A single-page markdown file updated after every wave gate. Format:

```markdown
# Orchestration state for feature/multi-image-prompt

## Current wave
Wave <N> — <wave description> — <status: in-progress | passed | blocked>

## Commits landed
- <SHA> <subject>   — reviewer verdicts: Codex APPROVE, Gemini APPROVE
- ...

## Phase-6 matrix progress
Session 1: R1=<3/3 pass>, R2=<3/3 pass>, R3=<3/3 pass>
Session 2: <pending | in-progress | done>
Verdict: <DROP | KEEP | pending>

## Escalations open
- <if any>

## Next dispatch
- Wave <N+1> task <X.Y> — implementer <model> — review <model>
```

This file is git-ignored (under `tmp/`) and rewritten in place. **Do not commit it.**

### 8.2 Per-dispatch artefacts — `tmp/dispatches/<task-id>/`

For each subagent dispatch:
- `prompt.md` — the exact prompt sent
- `output.log` — captured stdout/stderr
- `verdict.md` — reviewer's report
- `coordinator-decision.md` — what the Coordinator did with it (apply / send back / escalate)

These are also git-ignored.

### 8.3 Permanent artefacts

The only state that gets committed is:
- The 7 plan commits (already committed)
- Code commits #1, #1b, #2, #3, #4, #5a, #5b
- `docs/LIVE_VERIFICATION_image_batch.md` (commit #5a)
- The PR description (via `tmp/pr-body.md`, written to file then `gh pr create --body-file`)

---

## 9. Definition of Done

The orchestration is complete when **all** of the following are true:

1. All 7 implementation commits land on `feature/multi-image-prompt` per the v2 plan.
2. All three council gates (G1, G2, G3) returned APPROVE.
3. AC1–16 from spec §10 satisfied (see plan §Self-review table).
4. New PR opened, base `develop`, no AI co-author, body starts `Supersedes #35.`.
5. PR #35 closed with back-pointer comment.
6. `origin/claude/plan-next-issue-Stegy` still exists (deletion happens in Phase 8 post-merge, per spec D7 + council R3).
7. `docs/LIVE_VERIFICATION_image_batch.md` present, has verdict, INDEX-linked.
8. State log archived to `docs/superpowers/plans/2026-05-21-multi-image-prompt-orchestration-receipts.md` (optional but recommended — a one-time snapshot of `tmp/orchestration-state.md` final value, with all reviewer SHAs).

**Out of DoD scope (Phase 8 / post-merge):**
- `claude/*` branch deletion
- Memory updates (`stale-test-discovery.md`, `branch-naming-convention.md`)
- Feature branch local + remote deletion

---

## 10. Model Selection Rationale

For the record (to inform future plans):

- **Claude Opus 4.7 as Coordinator** — best at long-context planning, judgment calls, integrating reviewer feedback. Premium cost is justified by the centralized decision role.
- **Claude Sonnet 4.6 / Opus 4.7 as Implementer A** — used where Python judgment matters (count-selector refactor, git ops, matrix interpretation). Sonnet at lower cost for mechanical Claude tasks.
- **Codex (GPT-5-codex) as Implementer B** — pattern-based deletions, file scaffolding, TDD red tests. Codex's strength is rapid mechanical code transformation. Cheaper than Opus for mechanical work; built-in `codex review` mode is convenient.
- **Gemini 2.5 Pro as Implementer C** — large-context single-file authoring (Phase 4 e2e), and cross-model spec-conformance review. Gemini's >1M-token context fits the entire spec + plan + relevant code without summarization. Cheaper than Opus for one-shot file generation.
- **Three-model council at G1/G3** — gives independent perspectives without the deadlock risk of majority-vote (Coordinator decides). Three is the smallest n where one outlier can be overruled.

**Why no Claude Haiku for implementers:** at this PR's complexity, Haiku's quality-per-token isn't a win — Codex/Sonnet land at similar cost with better single-task focus.

**Why no GPT-4o / Claude Sonnet 3.5:** legacy; the project standardizes on the freshest stable releases (Opus 4.7, Sonnet 4.6, GPT-5-codex, Gemini 2.5 Pro).

---

## 11. Priority — Quality First

Council finding compliance is the bar. We accept higher token spend and higher wall-clock time in exchange for:
- Cross-model review on every implementer commit (not just sampling)
- Three-reviewer council on the BREAKING commit (#1b) and the final PR
- Conservative-default-KEEP on jitter inconclusive (D13)
- Two-session matrix with ≥2h gap (D14)
- Live e2e re-run after #5b verdict-driven code change (AC6)

**Concrete tradeoffs accepted:**
- Phase 3 (image batch) is heavier than necessary because of the four new observability events. Worth it: catches future throttling regressions without re-instrumenting.
- Phase 4 (e2e) is implemented by Gemini for context, then reviewed by Claude + Codex. Two reviewer rounds on a single file. Worth it: e2e correctness is load-bearing for the matrix.
- Council G3 (pre-PR) re-reads all 7 commits. Worth it: catches cross-commit regressions before push.

If at any point the Coordinator detects diminishing returns (e.g., a council finding that adds zero signal), it documents the rationale and shrinks the next round — but the default is quality-maximizing.

---

## 12. Setup confirmations — user-answered 2026-05-21

| Item | Confirmed | Note |
|---|---|---|
| Codex auth | ✅ logged in, GPT-5 Codex default | Coordinator invokes `codex exec` / `codex review` directly. |
| Gemini model | ⚠️ inspected first; `gemini-2.5-pro` is the target but the OAuth-personal tier is rate-limited; `gemini-3.x` family returns 404 on this account | §0 + §4.7 fallback policy applies. |
| Working directory | `C:\development\github\gflow-cli` | Set per invocation. |
| Network egress | assumed allow-listed for `cloudcode-pa.googleapis.com` (Gemini) and OpenAI endpoints (Codex) | If failures look network-shaped, retry once then escalate. |
| Council parallelism | acceptable | G1/G3 each fan out 3 reviewers; Wave 2/3/5 fan out Codex 3×. |
| Phase 6 live-credit budget | ✅ full matrix (~72 generations) approved | Mid-matrix abort caps to ~12 if R1 session-1 fails non-listener-miss. |

**Optional user mitigation worth noting:** the Gemini OAuth-personal tier exhausts quickly. If the user later switches to a paid Gemini API key (set `GEMINI_API_KEY` env var and re-auth), §4.7 fallbacks would fire less often and Gemini would handle Phase 4 (its strongest role). Not required.
