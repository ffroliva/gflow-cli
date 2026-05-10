# Phase 4 Hardening — Multi-Agent Orchestration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:dispatching-parallel-agents` (for parallel reviewers) and `superpowers:subagent-driven-development` (for sequential implementer dispatch). The Coordinator (the main session) owns plan progress, dispatches sub-agents, and makes go/no-go calls between tasks.
>
> **Companion docs:**
> - [`2026-05-10-phase-4-hardening.md`](2026-05-10-phase-4-hardening.md) — the 9-task TDD implementation plan that this orchestration executes (T0 spike + T1-T8).
> - [`../specs/2026-05-10-phase-4-hardening-design.md`](../specs/2026-05-10-phase-4-hardening-design.md) — the design spec v4 (authoritative for goals + DoD).
>
> **Pattern reference:** Same coordinator-led model as [`2026-05-09-image-mvp-orchestration.md`](2026-05-09-image-mvp-orchestration.md) (Phase 3) and the Phase 2 video-MVP orchestration that preceded it. Read those for the shared stage-by-stage detail. This document specifies only what is **specific** to Phase 4: the task → agent matrix, security-trigger flags (narrower than Phase 3 — only T2 + T3), the T0-spike workflow (non-coding), and Phase-4-specific escalation rules (Playwright Page-pool cost, tenacity API churn, pytest-bdd step-phrase collisions, RFC 9457 type-URI policy).

The same flow runs for every code task in the implementation plan. The Coordinator drives.

**Architecture:** Coordinator-led, single human-in-the-loop, multiple specialist sub-agents per task. Each code task moves through stages A–G; Stage E (Security Reviewer) is conditional on the security-touched flag below. T0 is a non-coding spike that runs Coordinator + Implementer only (no reviewers).

---

## 1. Roles

Identical to Phase 3. See [`2026-05-09-image-mvp-orchestration.md` § 1 Roles](2026-05-09-image-mvp-orchestration.md#1-roles) for the full table. In summary:

| Role | Subagent (`subagent_type`) |
|---|---|
| Coordinator | (none — main session) |
| Implementer | `gsd-executor` |
| Test Auditor | `gsd-nyquist-auditor` |
| Python Reviewer | `everything-claude-code:python-reviewer` |
| Code Reviewer | `everything-claude-code:code-reviewer` |
| Security Reviewer | `everything-claude-code:security-reviewer` (conditional — T2 + T3 only) |
| Phase Verifier | `gsd-verifier` (Stage G, after T8) |
| Build Resolver | `everything-claude-code:build-error-resolver` (only if CI fails) |

---

## 2. Per-task workflow

Identical to Phase 3 — see [§ 2 stages A–G](2026-05-09-image-mvp-orchestration.md#2-per-task-workflow). Quick recap:

- **Stage A** — Coordinator dispatches Implementer with the exact task block.
- **Stage B** — Coordinator gates on completion: confirms diff exists, all four quality gates pass (ruff / format / pyright / pytest), commit landed.
- **Stage C** — Coordinator dispatches Test Auditor with the diff + spec.
- **Stage D** — Coordinator dispatches Python Reviewer + Code Reviewer **in parallel** (one message, two `Agent` calls).
- **Stage E** — Coordinator dispatches Security Reviewer **only if** the task is flagged security-touched in the matrix below.
- **Stage F** — Coordinator merges findings, decides go / re-loop / escalate.
- **Stage G** — After all 9 tasks: dispatch `gsd-verifier` against the Definition-of-done checklist in the plan.

**Phase 4 exception:** T0 is a non-coding spike that uses Stage A only (Implementer = Coordinator-or-`gsd-executor` writes a `PLAN.md` note). Stages B–E are skipped because there is no production code change. Stage F = Coordinator decides whether the verdict triggers the T0-conditional clause (hard-cap `Settings.concurrency`).

---

## 3. Task → agent matrix

For each task in the implementation plan, the Coordinator dispatches the agents in this matrix. ✅ = always, ⚠️ = conditional, ✗ = skipped for this task.

| # | Task (short) | Implementer | Test Auditor | Python Rev | Code Rev | **Sec Rev** | Notes |
|---|---|---|---|---|---|---|---|
| 0 | Page-pool feasibility spike | `gsd-executor` | ⚠️ skip | ⚠️ skip | ⚠️ skip | ⚠️ skip | Non-coding. Output is a `PLAN.md` note. Stage F decides: pool size capped or not. |
| 1 | `errors.py` (RFC 9457 hierarchy + EXIT_CODE_MAP) | `gsd-executor` | ✅ | ✅ | ✅ | ⚠️ skip | Pure module — no I/O. Skip security. |
| 2 | Per-worker Page pool on `FlowApiClient` | `gsd-executor` | ✅ | ✅ | ✅ | **✅ run** | Concurrency model under auth context — race conditions + cookie isolation. |
| 3 | `_retry.py` + tenacity + typed-error classification + structlog logger swap | `gsd-executor` | ✅ | ✅ | ✅ | **✅ run** | Retry on auth-bearing requests + reCAPTCHA re-mint inside loop + WireFormatError discovery payload redaction. |
| 4a | `_handle_gflow_error` + `_handle_unhandled_error` in `_cli_helpers.py` | `gsd-executor` | ✅ | ✅ | ✅ | ⚠️ skip | CLI boundary handlers — no new attack surface beyond what T1/T3 raise. |
| 4b | Helper relocation (`_resolve_profile` / `_make_provider_dir` → `_cli_helpers.py`) | `gsd-executor` | ✅ | ✅ | ✅ | ⚠️ skip | Pure refactor — negative-import test guards drift. |
| 5 | `observability.py` (structlog bootstrap + emit_*_event + migration) | `gsd-executor` | ✅ | ✅ | ✅ | ⚠️ skip | Logging surface — Python reviewer covers structured-log shape; Code reviewer covers full `logging.*` migration. `show_locals=False` is reviewed but no new auth-bearing code. |
| 6 | pytest-bdd + 3 feature files + 12 scenarios | `gsd-executor` | ✅ | ✅ | ✅ | ⚠️ skip | All BDD steps use mocked `FlowApiClient`. Test Auditor verifies the mocked-only contract. |
| 7 | Documentation (USAGE / CONFIGURATION / CHANGELOG / .env.template / PLAN.md / ARCHITECTURE.md) | `gsd-executor` | ⚠️ skip | ⚠️ skip | ✅ | ⚠️ skip | Code Reviewer covers prose + freshness + version-string consistency. |
| 8 | Bump 0.3.0a1 → 0.4.0a1 + tag | (Coordinator-only) | ⚠️ skip | ⚠️ skip | ⚠️ skip | ⚠️ skip | Version bump + tag. Ask user to invoke `/release`. |

**Security-touched tasks: 2, 3.** Stage E is mandatory for these and ONLY these. Phase 4's security surface is narrower than Phase 3's because Phase 4 doesn't add new wire routes or new user-input parsing — it hardens the existing surface (concurrency model + retry on auth-bearing requests + structured error log redaction).

---

## 4. Sequence diagrams

Identical to Phase 3 — see [§ 4](2026-05-09-image-mvp-orchestration.md#4-sequence-diagrams). Single-task happy path: A→B→C→D (parallel)→E (if security)→F (merge)→commit→next task. Re-loop on findings: F→A (with feedback)→B→…

For T0 (non-coding spike): A (write `PLAN.md` note) → F (Coordinator decides conditional clause). No B/C/D/E.

---

## 5. Coordinator's prompt template (per task)

When dispatching the Implementer for task N, the Coordinator's `Task` tool prompt **must** contain:

1. **Task header verbatim** from `2026-05-10-phase-4-hardening.md` — the entire `## Task N: ...` block including Goal, Files, Steps, Acceptance criteria.
2. **Quality gates reminder** — verbatim:

   ```
   Before declaring done, run all four:
     uv run ruff check src tests
     uv run ruff format --check src tests
     uv run pyright src
     uv run pytest -q --cov=gflow_cli
   All must pass. Coverage must not regress below current baseline (≥ 80% overall;
   ≥ 95% on src/gflow_cli/errors.py and src/gflow_cli/observability.py).
   ```
3. **CLAUDE.md invariants reminder** — verbatim:

   ```
   - No `Co-Authored-By: Claude` (or any AI co-author) in commit messages.
   - No `print()` in `src/` — use structlog. Rich console.print() is fine for user-facing CLI output.
   - `pathlib.Path` everywhere, never raw strings for filesystem paths.
   - Frozen dataclasses for value objects; `Protocol` for ports.
   - Async all the way down: handlers and providers are `async def`.
   - Don't `pip install` — always `uv add`.
   - Conventional Commits subject lines (feat / fix / docs / test / chore / refactor).
   - Package is `gflow_cli` (post-rename at 10bf72e). Env var prefix is GFLOW_CLI_*.
     Legacy FLOW_CLI_* vars still work via _migrate_legacy_env shim (removed in v0.5.0).
   ```
4. **Spec-coverage reminder** (for T1, T2, T3, T4a, T5):

   ```
   The authoritative design spec is at
     docs/superpowers/specs/2026-05-10-phase-4-hardening-design.md (v4).
   Any deviation from the locked design choices (C1-C9 in spec § 2) must be
   surfaced to the Coordinator BEFORE implementing. Do not silently relax a
   design choice (e.g. dropping the per-worker Page model for shared-Page +
   Semaphore would defeat the purpose of T2).
   ```
5. **Single-task scope reminder** — verbatim:

   ```
   Implement ONLY this task. Do not pre-emptively write code that belongs to a
   later task. If you find yourself touching a file that another task owns,
   stop and tell the Coordinator. Example: T1 must NOT rewrite the 7 existing
   FlowApiError raise sites in api/client.py — that's T3's job.
   ```

For Reviewer dispatches, the prompt must include the diff (`git diff HEAD~1..HEAD`) plus the same task block, scoped to the reviewer's lens.

For Security Reviewer dispatches (T2 + T3 only), also include:
- `docs/SECURITY.md` (project's security posture)
- A focused threat brief:
  - **T2:** "Per-worker Page model. Pages share cookies + auth at BrowserContext level. Verify no Page leaks state across workers (e.g. JS-evaluated globals). Verify `_checkout_page` / `_checkin_page` cannot deadlock or starve. Verify Pages are cleaned up on `__aexit__`."
  - **T3:** "Retry layer on auth-bearing requests. Verify reCAPTCHA token is re-minted EVERY attempt (not stale-token-reuse). Verify `Retry-After` cap (60s) prevents server-controlled DoS. Verify `WireFormatError.discovery.body_prefix_redacted` runs `_redact_for_log` BEFORE the prefix is captured (no token leakage through discovery payload)."

---

## 6. Coordinator's checklist (per task)

Before moving to task N+1, the Coordinator verifies:

- [ ] Implementer's commit landed on the current branch (one commit per task, atomic).
- [ ] All four quality gates GREEN (verify via `git log -1 --stat` then run `uv run pytest -q` locally if any doubt).
- [ ] Test Auditor returned PASS or NO-OP (auditor confirms tests actually exercise the contract — not just compile-and-assert-True).
- [ ] Python Reviewer + Code Reviewer findings: zero CRITICAL / HIGH outstanding. MEDIUM addressed-or-deferred-with-reason.
- [ ] Security Reviewer (T2 + T3 only): zero CRITICAL / HIGH outstanding.
- [ ] Acceptance criteria from the plan satisfied.
- [ ] For T2 + T3: re-run `uv run pytest tests/api/ -q` specifically to catch concurrency regressions early.
- [ ] If any finding triggered a re-loop: the fix is its own atomic commit on top of the implementer's commit (do NOT rewrite history).

If any of these fail, the Coordinator re-dispatches the Implementer with the specific feedback (Stage A → B again).

---

## 7. Escalation rules

Inherits the Phase 3 base rules — see [§ 7](2026-05-09-image-mvp-orchestration.md#7-escalation-rules) — plus these Phase-4-specific triggers, mapped from spec § 7 (Open risks post-v4):

- **T0 spike returns INFEASIBLE** (Page cost >200ms or Playwright caps Pages <16):
  - Hard-cap `Settings.concurrency` to the discovered limit.
  - Update T2 tests to use the discovered cap as `N`.
  - Update the `Settings.concurrency` `Field(... le=N)` in `gflow_cli/config.py`.
  - Document in `PLAN.md` T0 note.
- **tenacity API churn during T3** (e.g. `AsyncRetrying` signature changed in a newer version):
  - Pin `tenacity>=8.2,<9` in `pyproject.toml`.
  - Document the pin reason in `KNOWN_ISSUES.md`.
  - Refactor `_make_retrying` if needed.
- **pytest-bdd step-phrase collision during T6**:
  - First fix: confirm per-feature `test_*_steps.py` files are scoped per the spec § 3.3 convention.
  - Second fix: rename one of the colliding step phrases (more specific wording).
  - Surface to user only if neither resolves.
- **RFC 9457 `type` URI policy challenge** (a reviewer asserts the `https://gflow-cli.dev/errors/*` URIs must resolve):
  - Refer the reviewer to spec § 7 "Problem Details `type` URI registration" — non-resolvable URIs are acceptable per RFC 9457 § 3.1 as long as they're stable identifiers.
  - Document in `docs/ARCHITECTURE.md` under the Problem Details note.
- **`correlation_id` cross-task leakage in T5**:
  - Verify `bind_contextvars` is called ONLY at process entry (in `cli.py`), never inside an `asyncio.create_task` or `asyncio.gather` body.
  - Add a regression test in `tests/test_observability.py`.
- **CI green locally / red on CI**: dispatch Build Resolver with the CI logs.
- **Reviewer disputes**: Coordinator decides; if undecidable, surface to user with a 2-3 sentence summary of the disagreement.

---

## 8. State tracking

Coordinator maintains a running task ledger. Suggested format (in `MEMORY.md` or as a TodoWrite list):

```
Phase 4 (hardening) — v0.4.0a1
  [ ] T0: Page-pool feasibility spike
  [ ] T1: errors.py (RFC 9457 hierarchy)
  [ ] T2: per-worker Page pool          [SEC]
  [ ] T3: tenacity retry + classification [SEC]
  [ ] T4a: _handle_gflow_error + _handle_unhandled_error
  [ ] T4b: helper relocation (_resolve_profile / _make_provider_dir)
  [ ] T5: observability.py + structlog migration
  [ ] T6: pytest-bdd + 12 scenarios
  [ ] T7: documentation
  [ ] T8: tag v0.4.0a1
```

After each task: tick the box, record commit SHA, record any deferred MEDIUM findings. Persist the ledger to `~/.claude/projects/C--development-github-flow-cli/memory/project_phase4_progress.md` so a session restart can resume mid-phase.

---

## 9. Why this orchestration vs. simpler subagent-driven-development?

Phase 2 chose multi-agent orchestration over flat sub-agent dispatch because it gives independent eyes (Test Auditor catches "tests that pass without testing", Security Reviewer catches what Python Reviewer doesn't, Code Reviewer catches what type-checker doesn't). Phase 3 inherited that. Phase 4 inherits it again with one twist: **the security review is narrower (T2 + T3 only)**, reflecting that this phase hardens existing surfaces rather than adding new ones. Skipping Sec-Rev on T1/T4/T5/T6/T7 is intentional, not lazy.

The infrastructure is already there from Phase 2 + Phase 3 — using it for hardening is essentially free in terms of design effort and pays off the same way.

---

## 10. Definition of done (this orchestration)

- [ ] All 10 task ledger items ticked (T0 + T1-T8 with T4 split).
- [ ] One atomic commit per task (or task + small-fixup commits if re-loop required), in order.
- [ ] `gsd-verifier` Stage G returns PASS against the [implementation plan's Definition of done](2026-05-10-phase-4-hardening.md#definition-of-done-phase-4--v040a1).
- [ ] Tag `v0.4.0a1` created locally (push is user's gate via `/release`).
- [ ] Memory updated: `project_phase4_progress.md` reflects final state with commit SHAs for each task; a `project_phase5_progress.md` stub created with the Phase 5 (public alpha on PyPI) forward pointer.

---

## 11. Concrete execution timeline

The Coordinator dispatches agents in this exact sequence. Each row is one tool invocation. Reviewer rows marked `║ parallel ║` are dispatched in a SINGLE message with multiple `Agent` tool calls (per `superpowers:dispatching-parallel-agents`).

Legend: `Impl` = `gsd-executor`, `TestAud` = `gsd-nyquist-auditor`, `PyRev` = `everything-claude-code:python-reviewer`, `CodeRev` = `everything-claude-code:code-reviewer`, `SecRev` = `everything-claude-code:security-reviewer`, `Verifier` = `gsd-verifier`. `—` = stage skipped per § 3 matrix.

| Step | Stage | Agent(s) | Inputs | Coordinator gate (must hold before next step) |
|---|---|---|---|---|
| **T0.A** | A | Impl | Task 0 block (PLAN.md note authoring) | `PLAN.md` note committed; N=2/4/8/16 timings recorded |
| T0.B–E | — | — | — | (skipped — non-coding) |
| **T0.F** | F | Coordinator | T0 verdict | Decide: cap `Settings.concurrency` or proceed at le=16 |
| **T1.A** | A | Impl | Task 1 block + reminders (§ 5) | Commit landed; 4 gates green; `errors.py` coverage ≥ 95% |
| **T1.C** | C | TestAud | diff + Task 1 acceptance criteria (parametrized to_problem_details + EXIT_CODE_MAP isinstance walk) | PASS or FAIL+gaps |
| **T1.D** | D | ║ PyRev ‖ CodeRev ║ | diff + Task 1 block | 0 CRITICAL / 0 HIGH from each |
| T1.E | E | — | — | (skipped — pure module, no I/O) |
| **T1.F** | F | Coordinator | All findings | Decide: advance / re-loop / escalate |
| **T2.A** | A | Impl | Task 2 block + reminders | Commit landed; 4 gates green; 5 concurrency tests pass |
| **T2.C** | C | TestAud | diff + Task 2 acceptance criteria (parallel-checkout proof, qsize invariants) | PASS |
| **T2.D** | D | ║ PyRev ‖ CodeRev ║ | diff + Task 2 block | 0 CRITICAL / 0 HIGH |
| **T2.E** | E | SecRev | diff + Task 2 + `docs/SECURITY.md` + T2 threat brief (§ 5) | 0 CRITICAL / 0 HIGH |
| **T2.F** | F | Coordinator | All findings | Decide |
| **T3.A** | A | Impl | Task 3 block + reminders | Commit landed; 4 gates green; 7 retry tests pass; existing 213+ pass |
| **T3.C** | C | TestAud | diff + Task 3 acceptance criteria (4xx no-retry table, Retry-After cap, reraise=True, WireFormatError discovery) | PASS |
| **T3.D** | D | ║ PyRev ‖ CodeRev ║ | diff + Task 3 block | 0 CRITICAL / 0 HIGH |
| **T3.E** | E | SecRev | diff + Task 3 + `docs/SECURITY.md` + T3 threat brief (§ 5) | 0 CRITICAL / 0 HIGH |
| **T3.F** | F | Coordinator | All findings | Decide |
| **T4a.A** | A | Impl | Task 4a block + reminders | Commit landed; 4 gates green; 7 error_handling tests pass |
| **T4a.C** | C | TestAud | diff + Task 4a acceptance criteria (per-class exit codes, error_raised/error_unhandled events) | PASS |
| **T4a.D** | D | ║ PyRev ‖ CodeRev ║ | diff + Task 4a block | 0 CRITICAL / 0 HIGH |
| T4a.E | E | — | — | (skipped — CLI boundary, no new attack surface beyond T1/T3) |
| **T4a.F** | F | Coordinator | All findings | Decide |
| **T4b.A** | A | Impl | Task 4b block + reminders | Commit landed; 4 gates green; negative-import test passes |
| **T4b.C** | C | TestAud | diff + Task 4b acceptance criteria (helpers callable post-relocation, AST drift guard) | PASS |
| **T4b.D** | D | ║ PyRev ‖ CodeRev ║ | diff + Task 4b block | 0 CRITICAL / 0 HIGH |
| T4b.E | E | — | — | (skipped — pure refactor) |
| **T4b.F** | F | Coordinator | All findings | Decide |
| **T5.A** | A | Impl | Task 5 block + reminders | Commit landed; 4 gates green; 8 observability tests pass; `observability.py` coverage ≥ 95% |
| **T5.C** | C | TestAud | diff + Task 5 acceptance criteria (TTY auto-detect, show_locals=False, correlation_id, message_hash/stack_hash) | PASS |
| **T5.D** | D | ║ PyRev ‖ CodeRev ║ | diff + Task 5 block | 0 CRITICAL / 0 HIGH |
| T5.E | E | — | — | (skipped — logging surface, no new auth/data path) |
| **T5.F** | F | Coordinator | All findings | Decide |
| **T6.A** | A | Impl | Task 6 block + reminders | Commit landed; 4 gates green; 12 BDD scenarios pass; mocked-only contract verified |
| **T6.C** | C | TestAud | diff + Task 6 acceptance criteria (12 scenarios, per-feature step files, mocked-only) | PASS |
| **T6.D** | D | ║ PyRev ‖ CodeRev ║ | diff + Task 6 block | 0 CRITICAL / 0 HIGH |
| T6.E | E | — | — | (skipped — test-only changes) |
| **T6.F** | F | Coordinator | All findings | Decide |
| **T7.A** | A | Impl | Task 7 block + reminders | Commit landed; docs updated; CHANGELOG `[Unreleased]` reflects Phase 4 |
| T7.C | C | — | — | (skipped — docs-only) |
| **T7.D** | D | CodeRev | diff + Task 7 block (prose review + version-string consistency check) | 0 CRITICAL / 0 HIGH |
| T7.E | E | — | — | (skipped) |
| **T7.F** | F | Coordinator | All findings | Decide |
| **T8.A** | A | Coordinator | Task 8 block (version bump + tag) | `pyproject.toml` + `__init__.py` updated to 0.4.0a1, commit landed, tag `v0.4.0a1` created locally |
| T8.C–E | — | — | — | (skipped — release task is Coordinator-only) |
| **T8.F** | F | Coordinator | `git log` + tag list | Tag created locally; ask user to invoke `/release` (or push manually) |
| **PHASE.G** | G | Verifier | DoD checklist from `2026-05-10-phase-4-hardening.md` § Definition of done | Verifier returns PASS |

**Reporting cadence to user:** Coordinator surfaces a one-line update only after each `T*.F` (task complete) and on any escalation. No chatter mid-task.

**Total agent invocations under the happy path:** ~40 (one Impl + one TestAud + two parallel reviewers + zero-or-one SecRev per task, minus the Coordinator-only tasks T0/T8 and the docs-only T7). Re-loops add invocations only when findings warrant.

---

## 12. Kickoff prompt (paste this to start)

The user pastes the prompt below into a fresh session (or continuation) where the main session is empty / just-cleared. The prompt installs the Coordinator role and starts at T0.A. Subsequent dispatches are driven by the Coordinator without further user input.

```text
You are the Coordinator for Phase 4 (Hardening) of the gflow-cli project. Execute
the plan at docs/superpowers/plans/2026-05-10-phase-4-hardening.md following
the workflow at docs/superpowers/plans/2026-05-10-phase-4-hardening-orchestration.md.

State on resume:
- HEAD is at commit 10bf72e or later. Spec v4 at 2f6b936. Plan at 255604b
  (with self-review polish at 45e7902). Atomic rename to gflow_cli at 10bf72e.
- Tag v0.3.0a1 at 90e21b3 is the previous shipped release. Phase 4 targets v0.4.0a1.
- 211 tests passing as the green baseline (208 pre-rename + 3 new TestLegacyEnvShim).

Read these in order before any dispatch:
1. ~/.claude/projects/C--development-github-flow-cli/memory/MEMORY.md (index)
2. ~/.claude/projects/.../memory/project_phase4_progress.md (current state)
3. ~/.claude/projects/.../memory/project_rename_gflow_cli.md (rename context)
4. ~/.claude/projects/.../memory/project_conventions.md (modular monolith + RFC 9457)
5. docs/superpowers/specs/2026-05-10-phase-4-hardening-design.md (spec v4 — 9 tasks)
6. docs/superpowers/plans/2026-05-10-phase-4-hardening.md (plan — 10 task headings)
7. docs/superpowers/plans/2026-05-10-phase-4-hardening-orchestration.md (this file)

Then begin at step T0.A: dispatch a gsd-executor Implementer with the Task 0 block
(non-coding Page-pool feasibility spike). After T0.F surface verdict to me ONLY if
the conditional clause fires (Settings.concurrency hard-cap < 16). Otherwise tick
the ledger and proceed to T1.A.

For T1.A onward: dispatch with the full task block, the quality-gates reminder,
the CLAUDE.md invariants, and the spec-coverage reminder — exactly per § 5 of
the orchestration plan.

Proceed through every step in § 11 in order. After each task's Stage F:
  - Tick the ledger in MEMORY (add commit SHA + any deferred MEDIUM
    findings) and surface ONE line to me: "Tn done: <subject> [SHA]".
  - Then proceed to T(n+1).A without waiting for my approval.

Surface to me ONLY when:
  - A task completes (one line as above).
  - An escalation per § 7 fires (T0 infeasibility, tenacity churn, bdd
    collision, RFC 9457 type-URI policy challenge, undecidable reviewer
    disagreement, build resolver invoked, blocker).
  - Stage G (gsd-verifier) returns PASS or FAIL after Task 8.

Do NOT pause between tasks. Do NOT ask for confirmation between stages.
Do NOT skip Stage E for the security-touched tasks (T2, T3) — and only those.
Do NOT batch parallel reviewer dispatches into separate messages — they must go
in one message with two Agent tool calls per superpowers:dispatching-parallel-agents.

When all 9 tasks (T0 + T1-T8) are done, run Stage G (gsd-verifier) and report PASS
or FAIL. If PASS, ask me to invoke /release for the v0.4.0a1 tag push.

Begin.
```

- [x] References Phase 3 orchestration for shared structure rather than duplicating it.
- [x] Specifies the task → agent matrix with explicit security-touched flags (T2 + T3 only — narrower than Phase 3).
- [x] Coordinator's prompt template is concrete and copy-pasteable; includes the post-rename context.
- [x] Per-task checklist defines a clean go/no-go gate.
- [x] State-tracking format is explicit (so a session restart can resume mid-phase).
- [x] Escalation rules cover Phase 4's specific risks (Page-pool cost, tenacity churn, bdd collisions, RFC 9457 type-URI policy, `correlation_id` leakage).
- [x] Definition of done points back to the implementation plan, no duplication.

---

## Execution handoff

Coordinator: start with **Task 0** (non-coding spike). Dispatch the `gsd-executor` Implementer with the prompt template from § 5. After Stage F (PLAN.md note committed; verdict decided), tick the ledger and proceed to T1.A. Repeat through T8.

If a re-loop is triggered, do NOT advance the ledger until the task is fully done.

After T8.F, dispatch `gsd-verifier` for Stage G against the implementation plan's Definition of done. If PASS, surface to user with a one-line summary; ask user to invoke `/release` (or push the tag manually if release is `on: push tags`).

_End of orchestration plan._
