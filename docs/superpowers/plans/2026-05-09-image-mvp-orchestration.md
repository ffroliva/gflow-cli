# Image MVP — Multi-Agent Orchestration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:dispatching-parallel-agents (for parallel reviewers) and superpowers:subagent-driven-development (for sequential implementer dispatch). The Coordinator (the main session) owns plan progress, dispatches sub-agents, and makes go/no-go calls between tasks.
>
> **Companion doc:** [`2026-05-09-image-mvp.md`](2026-05-09-image-mvp.md) — the 12-task implementation plan that this orchestration executes.
>
> **Pattern reference:** This orchestration follows the same coordinator-led model as [`2026-05-09-video-mvp-orchestration.md`](2026-05-09-video-mvp-orchestration.md) (Phase 2). Read that document for the full stage-by-stage detail. This document specifies only what is **specific** to the Image MVP: the task → agent matrix, security-trigger flags, and per-task notes.

The same flow runs for every task in the implementation plan. The Coordinator drives.

**Architecture:** Coordinator-led, single human-in-the-loop, multiple specialist sub-agents per task. Each task moves through stages A–G; Stage E (Security Reviewer) is conditional on the security-touched flag below.

---

## 1. Roles

Identical to Phase 2. See [`2026-05-09-video-mvp-orchestration.md` § 1 Roles](2026-05-09-video-mvp-orchestration.md#1-roles) for the full table. In summary:

| Role | Subagent (`subagent_type`) |
|---|---|
| Coordinator | (none — main session) |
| Implementer | `gsd-executor` |
| Test Auditor | `gsd-nyquist-auditor` |
| Python Reviewer | `everything-claude-code:python-reviewer` |
| Code Reviewer | `everything-claude-code:code-reviewer` |
| Security Reviewer | `everything-claude-code:security-reviewer` (conditional) |
| Build Resolver | `everything-claude-code:build-error-resolver` (only if CI fails) |

---

## 2. Per-task workflow

Identical to Phase 2 — see [§ 2 stages A–G](2026-05-09-video-mvp-orchestration.md#2-per-task-workflow). Quick recap:

- **Stage A** — Coordinator dispatches Implementer with the exact task block.
- **Stage B** — Coordinator gates on completion: confirms diff exists, all four quality gates pass (ruff / format / pyright / pytest), commit landed.
- **Stage C** — Coordinator dispatches Test Auditor with the diff + spec.
- **Stage D** — Coordinator dispatches Python Reviewer + Code Reviewer **in parallel** (one Task tool message, two Agent calls).
- **Stage E** — Coordinator dispatches Security Reviewer **only if** the task is flagged security-touched in the matrix below.
- **Stage F** — Coordinator merges findings, decides go / re-loop / escalate.
- **Stage G** — After all 12 tasks: dispatch `gsd-verifier` against the Definition-of-done checklist in the plan.

---

## 3. Task → agent matrix

For each task in the implementation plan, the Coordinator dispatches the agents in this matrix. ✅ = always, ⚠️ = conditional, ✗ = skipped for this task.

| # | Task (short) | Implementer | Test Auditor | Python Rev | Code Rev | **Sec Rev** | Notes |
|---|---|---|---|---|---|---|---|
| 1 | Image value objects + body builder | `gsd-executor` | ✅ | ✅ | ✅ | ⚠️ skip | Pure module — no network, no I/O. Skip security review. |
| 2 | `GeneratedImage` DTO | `gsd-executor` | ✅ | ✅ | ✅ | ⚠️ skip | Pure data parsing. |
| 3 | Image route URL helper | `gsd-executor` | ✅ | ✅ | ✅ | **✅ run** | URL builder — verify path-traversal guard. |
| 4 | `generate_image()` single | `gsd-executor` | ✅ | ✅ | ✅ | **✅ run** | Auth-bearing request, reCAPTCHA mint. |
| 5 | `generate_images_batch()` parallel | `gsd-executor` | ✅ | ✅ | ✅ | **✅ run** | Concurrency, partial-failure handling, multiple token mints. |
| 6 | `download_image()` signed fifeUrl | `gsd-executor` | ✅ | ✅ | ✅ | **✅ run** | File write to disk from external URL — SSRF/path-traversal review. |
| 7 | CLI `gflow image upload` | `gsd-executor` | ✅ | ✅ | ✅ | **✅ run** | User-supplied path → upload — file-handling review. |
| 8 | CLI `gflow image t2i` | `gsd-executor` | ✅ | ✅ | ✅ | ⚠️ skip | Wires existing pieces; no new attack surface beyond Task 4–6. |
| 9 | CLI `gflow image i2i --ref` | `gsd-executor` | ✅ | ✅ | ✅ | **✅ run** | UUID parsing of user input + auto-upload — input-validation review. |
| 10 | Smoke test script | `gsd-executor` | ⚠️ skip | ✅ | ✅ | ⚠️ skip | Live script, no contract claims. |
| 11 | Documentation updates | `gsd-executor` | ⚠️ skip | ⚠️ skip | ✅ | ⚠️ skip | Code Reviewer covers prose + freshness. |
| 12 | Tag v0.3.0a1 | (Coordinator-only) | ⚠️ skip | ⚠️ skip | ⚠️ skip | ⚠️ skip | Version bump + tag. Ask user to invoke `/release`. |

**Security-touched tasks: 3, 4, 5, 6, 7, 9.** Stage E is mandatory for these.

---

## 4. Sequence diagrams

Identical to Phase 2 — see [§ 4](2026-05-09-video-mvp-orchestration.md#4-sequence-diagrams). Single-task happy path: A→B→C→D (parallel)→E (if security)→F (merge)→commit→next task. Re-loop on findings: F→A (with feedback)→B→…

---

## 5. Coordinator's prompt template (per task)

When dispatching the Implementer for task N, the Coordinator's `Task` tool prompt **must** contain:

1. **Task header verbatim** from `2026-05-09-image-mvp.md` — the entire `## Task N: ...` block including Goal, Files, Steps, Acceptance criteria.
2. **Quality gates reminder** — verbatim:
   ```
   Before declaring done, run all four:
     uv run ruff check src tests
     uv run ruff format --check src tests
     uv run pyright src
     uv run pytest -q --cov=flow_cli
   All must pass. Coverage must not regress below current baseline.
   ```
3. **CLAUDE.md invariants reminder** — verbatim:
   ```
   - No `Co-Authored-By: Claude` (or any AI co-author) in commit messages.
   - No `print()` in `src/` — use `logging` (Phase 1 preceded structlog).
   - `pathlib.Path` everywhere, never raw strings for filesystem paths.
   - Frozen dataclasses for value objects; `Protocol` for ports.
   - Async all the way down: handlers and providers are `async def`.
   - Don't `pip install` — always `uv add`.
   - Conventional Commits subject lines (feat / fix / docs / test / chore / refactor).
   ```
4. **Captured-sample paths** (for Tasks 1–6 only):
   ```
   Ground-truth wire format lives in:
     samples/captured/06_batchGenerateImages.json   (T2I baseline)
     samples/captured/07_batchGenerateImages_seeded.json   (I2I + 4:3 + parallel)
     samples/captured/01_upload_image.json   (upload route — already used by I2V)
   Build the body to match these. Do NOT introduce wire fields not present in
   the samples.
   ```
5. **Single-task scope reminder** — verbatim:
   ```
   Implement ONLY this task. Do not pre-emptively write code that belongs to a
   later task. If you find yourself touching a file that another task owns,
   stop and tell the Coordinator.
   ```

For Reviewer dispatches, the prompt must include the diff (`git diff HEAD~1..HEAD`) plus the same task block, scoped to the reviewer's lens (e.g. "as a Python reviewer, evaluate idiomatic usage, type hints, performance, …").

For Security Reviewer dispatches, also include the project's `docs/SECURITY.md` and the OWASP Top 10 cheat sheet as the framing.

---

## 6. Coordinator's checklist (per task)

Before moving to task N+1, the Coordinator verifies:

- [ ] Implementer's commit landed on the current branch (one commit per task, atomic).
- [ ] All four quality gates GREEN (verify via `git log -1 --stat` then run `uv run pytest -q` locally if any doubt).
- [ ] Test Auditor returned PASS or NO-OP (auditor confirms tests actually exercise the contract).
- [ ] Python Reviewer + Code Reviewer findings: zero CRITICAL / HIGH outstanding. MEDIUM addressed-or-deferred-with-reason.
- [ ] Security Reviewer (if applicable): zero CRITICAL / HIGH outstanding.
- [ ] Acceptance criteria from the plan satisfied.
- [ ] If any finding triggered a re-loop: the fix is its own atomic commit on top of the implementer's commit (do NOT rewrite history).

If any of these fail, the Coordinator re-dispatches the Implementer with the specific feedback (Stage A → B again).

---

## 7. Escalation rules

Identical to Phase 2 — see [§ 7](2026-05-09-video-mvp-orchestration.md#7-escalation-rules). Summary:

- **Quality gates fail in CI but pass locally:** dispatch Build Resolver with the CI logs.
- **Reviewer disputes:** Coordinator decides; if undecidable, surface to user with a 2-3 sentence summary of the disagreement.
- **Wire-format mismatch (4xx from server):** STOP. Re-capture HAR from a fresh UI generation. Update the sample, update the body builder, re-run the task.
- **Inferred aspect ratio (`LANDSCAPE` or `PORTRAIT_THREE_FOUR`) returns 4xx:** capture the actual enum from a fresh HAR, fix `Aspect` enum, replay.
- **reCAPTCHA failures:** check that `grecaptcha` is loaded on the bootstrap page; site-key may have changed. Refer to Phase 2 Task 2 (site-key discovery) — same approach.

---

## 8. State tracking

Coordinator maintains a running task ledger. Suggested format (in `MEMORY.md` or as a TodoWrite list):

```
Phase 3 (image MVP) — v0.3.0a1
  [ ] T1: image value objects + body builder
  [ ] T2: GeneratedImage DTO
  [ ] T3: route URL helper
  [ ] T4: generate_image() single
  [ ] T5: generate_images_batch() parallel
  [ ] T6: download_image() fifeUrl
  [ ] T7: gflow image upload
  [ ] T8: gflow image t2i
  [ ] T9: gflow image i2i
  [ ] T10: smoke_image.py
  [ ] T11: docs
  [ ] T12: tag v0.3.0a1
```

After each task: tick the box, record commit SHA, record any deferred MEDIUM findings.

---

## 9. Why this orchestration vs. simpler subagent-driven-development?

Phase 2 chose multi-agent orchestration over flat sub-agent dispatch because it gives independent eyes (Test Auditor catches "tests that pass without testing", Security Reviewer catches what Python Reviewer doesn't, Code Reviewer catches what type-checker doesn't). Phase 3 inherits the same reasoning. The infrastructure is already there from Phase 2 — using it for image is essentially free in terms of design effort and pays off the same way.

---

## 10. Definition of done (this orchestration)

- [ ] All 12 task ledger items ticked.
- [ ] One atomic commit per task (or task + small-fixup commits if re-loop required), in order.
- [ ] Phase verifier (`gsd-verifier`) returns PASS against the [implementation plan's Definition of done](2026-05-09-image-mvp.md#definition-of-done-phase-3--v030a1).
- [ ] Tag `v0.3.0a1` pushed.
- [ ] Memory updated: `project_phase2_progress.md` either renamed to `project_phase3_progress.md` and recapped, or its "next" pointer moved to a `project_phase4_progress.md` stub with whatever scope comes after image (likely concurrency-pool / I2V batch parity / etc.).

---

## Self-review checklist (completed by author)

- [x] References Phase 2 orchestration for shared structure rather than duplicating 388 lines.
- [x] Specifies the task → agent matrix with explicit security-touched flags.
- [x] Coordinator's prompt template is concrete and copy-pasteable.
- [x] Per-task checklist defines a clean go/no-go gate.
- [x] State-tracking format is explicit (so a session restart can resume mid-phase).
- [x] Escalation rules cover the wire-format-mismatch case (specific to Phase 3's two inferred aspect ratios).
- [x] Definition of done points back to the implementation plan, no duplication.

---

## Execution handoff

Coordinator: start with **Task 1**. Dispatch the `gsd-executor` Implementer with the prompt template from § 5. After Stage F (commit landed, all reviewers PASS), tick the ledger and proceed to Task 2. Repeat through Task 12.

If a re-loop is triggered, do NOT advance the ledger until the task is fully done.

After Task 12, dispatch `gsd-verifier` for Stage G against the implementation plan's Definition of done. If PASS, surface to user with a one-line summary; ask user to invoke `/release` (or push the tag manually if release is `on: push tags`).

_End of orchestration plan._
