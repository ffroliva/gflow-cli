---
description: Multi-dimensional LLM council review of an open PR. Adaptive dimensions per PR surface (data / transports / CLI / docs / auth). Baseline always includes correctness, code quality, security, and tests. With no argument, lists open PRs ranked by review priority.
---

# `/gflow:pr-council-review [PR#]` — PR Council Review Gate

Council-driven PR review. Dispatches **4 baseline + N adaptive** parallel reviewers, each scoped to one dimension, then synthesizes a single consensus verdict. Validated on PR #93 (2026-05-26) — see memory `llm-council-code-review-pr93`.

**Two modes:**
1. **No argument** → list open PRs ranked by review priority; user picks.
2. **`PR#` argument** → run the full council on that PR.

Treat **YELLOW as soft block** (per memory `llm-council-data-layer-fixes`).

---

## 0 · Pre-flight

**All four checks are mandatory. Any failure halts before Phase 1/2.**

1. **`gh` authenticated** — run `gh auth status`. Non-zero exit → stop with: *"`gh` is not authenticated. Run `gh auth login` and re-invoke."* Do NOT proceed to dispatch agents that would all fail opaquely.
2. **Inside the repo** — assert `AGENTS.md` AND `CLAUDE.md` exist in the working directory.
3. **Resolve the argument:**
   - **Empty** → jump to **Phase 1 (Prioritize)**.
   - **PR number** → validate it with `gh pr view <N> --json number`. If error → stop with the error verbatim. Do NOT silently fall back to Phase 1.
4. **Draft check** (PR# mode only) — if `gh pr view <N> --json isDraft` returns `true`, surface a banner citing memory `draft-pr-merge-trap`: *"PR #N is DRAFT. Reviewing is fine, but do NOT merge a draft (the merge API can close it + delete the head ref). Run `gh pr ready N` first if you intend to merge. Continue review? (yes/no)"*. Ask the user before dispatching.

---

## 1 · Prioritize (no-argument mode)

Run once, list open PRs in recommended order, then stop and ask the user which to review.

```bash
gh pr list --state open --json number,title,author,isDraft,headRefName,updatedAt,additions,deletions,labels,reviewDecision,statusCheckRollup
```

**Empty-list short-circuit:** if the result is `[]`, print *"No open PRs to review."* and exit. Do NOT render an empty table or fall through to Phase 2.

Rank with these heuristics (highest priority first):

| Signal | Weight | Why |
|---|---|---|
| `isDraft == false` AND CI all-green | +3 | Ready to merge once approved — highest ROI |
| Touched path includes `src/gflow_cli/api/transports/` | +2 | UI-automation is the highest-risk surface (memory `pr-must-verify-on-affected-surface`) |
| Touched path includes `src/gflow_cli/auth/` or `recaptcha` | +2 | Auth changes need security-deep-dive |
| Touched path includes `src/gflow_cli/data/` | +2 | Migration safety + #86 hygiene history |
| Older than 7 days (stale risk) | +1 | Conflict risk grows with age |
| `additions + deletions <= 300` | +1 | Small PRs ship faster |
| Label contains `release-blocker`, `security`, `hotfix` | +5 | Anything labelled urgent jumps the queue |
| `isDraft == true` AND CI red | −2 | Author still iterating; review wastes their time |

Present a numbered table:

```
| Rank | PR# | Title | Ready? | CI | Surface | Why prioritized |
|------|-----|-------|--------|----|---------| ---------------|
| 1 | #71 | feat(image): native count selector …  | ✅ | green | transports | Ready + transports surface + small (220 LOC) |
| 2 | …
```

Then **stop and ask** the user to pick a PR number. Do NOT auto-start a review on Rank 1 — *recommend*, do not *pre-select*. The user MUST type the PR number they want reviewed.

---

## 2 · Gather context (PR# mode)

Pull in parallel. Use `ctx_batch_execute` to avoid context-window flood.

**Commands** (label → cmd):
- `PR_META` → `gh pr view <N> --json title,body,author,baseRefName,headRefName,state,isDraft,additions,deletions,changedFiles,labels,files,statusCheckRollup`
- `PR_DIFF` → `gh pr diff <N>`
- `PR_CHECKS` → `gh pr checks <N>`
- `TOUCHED_PATHS` → `gh pr view <N> --json files --jq '.files[].path' | sort -u`
- `RECENT_COMMITS` → `gh pr view <N> --json commits --jq '.commits[-5:] | .[] | "\(.oid[:7]) \(.messageHeadline)"'`

**Reference files** (Read inline; small enough to load):
- `CLAUDE.md` — Claude-Code-specific protocol
- `AGENTS.md` — universal agent rules
- `docs/INDEX.md` — docs routing
- `~/.claude/projects/C--development-github-gflow-cli/memory/MEMORY.md` — memory index

**Memory traversal:** For each `TOUCHED_PATH`, glob memory for related entries:
- `transports/` (image OR video) → `[[pr-must-verify-on-affected-surface]]`, `[[flow-locale-leak-icon-ligatures]]`, `[[playwright-click-no-downstream-event-signature]]`, `[[rest-transports-drop-ui-fields]]`, `[[image-video-mode-switch-symmetry]]`, `[[video-generation-spec]]`, `[[image-generation-401-next]]`
- `data/` → `[[data-layer-overview]]`, `[[data-layer-test-pollution-trap]]`, `[[data-layer-v0.9.0-bugs]]`, `[[exit-code-16-data-store]]`, `[[on-started-callback-recorder-safety]]`
- `auth/` → `[[real-browser-auth-mandatory]]`, `[[release-signing]]`
- `cli` → `[[release-back-merge-gap-recovery]]`, `[[wheel-build-sanity-gate]]`
- `tests/` (any) → `[[bdd-stubs-mirror-runtime-signatures]]`, `[[background-e2e-pytest-pattern]]`, `[[full-test-suite-ooms]]`, `[[stale-test-discovery]]`, `[[structlog-cache-logger-off-for-tests]]`
- `tests/features/` (BDD) → also `[[bdd-stubs-mirror-runtime-signatures]]` (silent TypeError trap)
- `scripts/` (dev / CI / release helpers) → `[[wheel-build-sanity-gate]]`, `[[release-back-merge-gap-recovery]]`
- `.planning/`, `docs/superpowers/` (process artifacts) → `[[release-spec-plan-memory-consolidation]]`
- `docs/`, `*.md` → `[[readme-hybrid-router-pattern]]`, `[[llm-council-doc-review-v0.9.0]]`, `[[agents-md-vs-llms-txt]]`, `[[pypi-readme-staleness-fix]]`
- `pyproject.toml`, `.github/` → `[[release-spec-plan-memory-consolidation]]`, `[[pr-hygiene-revert-and-multi-commit]]`, `[[draft-pr-merge-trap]]`, `[[pypi-rejected-filename-reusable]]`

---

## 3 · Detect adaptive dimensions

The **baseline** 4 dimensions run for every PR. **Adaptive** dimensions activate only when the touched paths or labels match. Build the council roster before dispatching.

| Dimension | Always? | Activates when… |
|---|---|---|
| **D1 — Correctness & completeness** | ✅ baseline | always |
| **D2 — Code quality & best practices** | ✅ baseline | always |
| **D3 — Security** | ✅ baseline | always |
| **D4 — Tests & coverage** | ✅ baseline | always |
| **D5 — UI / live-verification** | adaptive | any path under `src/gflow_cli/api/transports/` or `tests/e2e/` |
| **D6 — Data-migration safety** | adaptive | any path under `src/gflow_cli/data/` or `*.sql` |
| **D7 — CLI UX & help-text consistency** | adaptive | any path matching `src/gflow_cli/cli*.py` or `src/gflow_cli/commands/` |
| **D8 — Docs cross-reference & drift** | adaptive | ≥2 of these touched: `README.md`, `docs/**`, `CHANGELOG.md`, `AGENTS.md`, `CLAUDE.md`, `PLAN.md` |
| **D9 — Auth / reCAPTCHA / Chrome-profile** | adaptive | any path under `src/gflow_cli/auth/` or label `security` |
| **D10 — Release-gate compliance** | adaptive | `pyproject.toml`, `src/gflow_cli/__init__.py`, `.github/workflows/`, or `release/*` branch |
| **D11 — BDD step-stub signatures** | adaptive | any path under `tests/features/` (silent TypeError trap, memory `bdd-stubs-mirror-runtime-signatures`) |
| **D12 — Dev / release scripts** | adaptive | any path under `scripts/` |

**Baseline floor is non-negotiable.** D1–D4 ALWAYS run, even on docs-only PRs. The user spec explicitly required quality / security / best-practices / tests as a floor — do not skip.

**Docs-only PRs** (defined as: 100% of touched paths match `*.md`, `docs/**`, `CHANGELOG.md`, `README.md`, `LICENSE`, `AUTHORS`) → D4 reframes from "test code coverage" to "docs-verification" (broken cross-references, dead links, code examples that don't run). The dimension still runs; only its lens shifts.

---

## 4 · Dispatch the council

Use `superpowers:dispatching-parallel-agents`. Send **all** agents in one message — they must run concurrently. Each agent must:

- Get the PR number, base branch, and head branch.
- Be told its dimension AND told what NOT to duplicate (the other dimensions' scope).
- Use `ctx_execute` for large outputs (diff, file reads) — never flood the context.
- Output a structured report: **Verdict (GREEN / YELLOW / RED)**, **Must-fix** (numbered, file:line refs), **Nice-to-have** (numbered), **Confirmed-good/safe/correct** (1-line bullets).
- Be word-limited to **under 500 words** so the synthesizer doesn't drown.

**Per-dimension prompt skeleton** (fill in `<…>`):

```
You are one of <N> parallel reviewers on a council reviewing PR #<N> of `gflow-cli` at C:\development\github\gflow-cli.

Your dimension is **<DIMENSION NAME>**. Other agents handle <other dimensions> — do NOT duplicate their work.

Get the diff with `gh pr diff <N>` via ctx_execute. If `additions + deletions > 5000`, do NOT pull the full diff at once — instead enumerate touched files with `gh pr view <N> --json files --jq '.files[].path'`, then read per-directory with `gh pr diff <N> -- <path>` to keep each call under 2k lines. Read the unchanged surrounding code with Read for any file you flag.

Assess specifically:
1. <dimension-specific question 1, with code citation hooks>
2. <…>
3. <…>

**Mandatory memory you MUST consult and cite if relevant** (from the Dimension → Slugs table below): <fixed slug list for this dimension>.

Output a structured report under 500 words:
- Verdict: GREEN / YELLOW / RED
- Must-fix (numbered, file:line refs)
- Nice-to-have (numbered)
- Confirmed-<good/safe/correct> (1-line bullets)

Be skeptical. Cite file paths and line numbers. **If you have nothing to flag in your dimension, say so explicitly and state GREEN with a one-line justification — do NOT manufacture findings to look thorough.**
```

**Dimension → mandatory slugs table** (always pass these in the prompt, regardless of which paths the PR touches — they encode the recurring traps for that dimension):

| Dim | Mandatory memory slugs |
|---|---|
| D1 | `[[pr-must-verify-on-affected-surface]]` |
| D2 | (none mandatory; consult surrounding code style) |
| D3 | `[[real-browser-auth-mandatory]]`, `[[release-signing]]` |
| D4 | `[[pr-must-verify-on-affected-surface]]`, `[[full-test-suite-ooms]]`, `[[stale-test-discovery]]`, `[[structlog-cache-logger-off-for-tests]]` |
| D5 | `[[flow-locale-leak-icon-ligatures]]`, `[[playwright-click-no-downstream-event-signature]]`, `[[rest-transports-drop-ui-fields]]`, `[[image-video-mode-switch-symmetry]]`, `[[verification-ledger-5-layer]]` |
| D6 | `[[on-started-callback-recorder-safety]]`, `[[data-layer-test-pollution-trap]]`, `[[exit-code-16-data-store]]`, `[[data-layer-v0.9.0-bugs]]` |
| D7 | (none mandatory) |
| D8 | `[[readme-hybrid-router-pattern]]`, `[[agents-md-vs-llms-txt]]`, `[[pypi-readme-staleness-fix]]`, `[[llm-council-doc-review-v0.9.0]]` |
| D9 | `[[real-browser-auth-mandatory]]` |
| D10 | `[[release-back-merge-gap-recovery]]`, `[[wheel-build-sanity-gate]]`, `[[pypi-rejected-filename-reusable]]`, `[[draft-pr-merge-trap]]` |
| D11 | `[[bdd-stubs-mirror-runtime-signatures]]` |
| D12 | `[[wheel-build-sanity-gate]]` |

**Per-dimension specifics** (concrete checks each agent must perform):

- **D1 Correctness & completeness:**
  - Does the fix address the *root cause* or only the symptom? Cite the function being changed.
  - Are edge cases handled? Spot-check 2 boundary conditions.
  - Does the CHANGELOG entry match what shipped? Quote both.
  - **PR-body compliance:** does the PR body's description match the diff? Are the PR body's test-plan checkboxes (the `- [ ]` / `- [x]` markers) accurate? If any unchecked box represents work the PR claims to deliver, flag it. If the PR claims `Closes #N` for an issue whose acceptance criteria are not met by the diff, flag it.
- **D2 Code quality:** Style consistency with surrounding code? Comments explain WHY not WHAT (per `CLAUDE.md`)? Any abstraction that doesn't earn its keep? SRP intact? Type annotations on every new signature?
- **D3 Security:** Injection vectors? Env-var trust boundary? Shell interpolation (`subprocess`, `shell=True`)? Path traversal? Secret-shaped literals? `--no-verify` or signature-bypass in commits?
- **D4 Tests:**
  - **Mandatory affected-surface check** (cardinal rule, memory `pr-must-verify-on-affected-surface`): identify the runtime surface the fix touches (T2V / I2V / R2V / data / CLI / auth / etc.). If the test suite does not exercise that exact surface (e.g. a fix for `_attach_frame` with only T2V tests), this is **automatically YELLOW**. Cite the test `file:line` that proves coverage of the affected surface — or state explicitly that no such test exists.
  - Test pyramid placement (unit vs integration vs e2e)?
  - Do tests verify *behavior* or just shape (static strings, mocked returns)?
  - Coverage delta meaningful, or dead-coverage (counts new constants without exercising them)?
- **D5 UI/live-verify:** Will this selector match real Flow DOM on non-EN locales? Is there runtime evidence beyond static-string invariants? Has the PR author pasted live-verify evidence (file count + magic bytes + Pillow dims + structlog events such as `new_project_clicked` / `submit_clicked` / `frame_attached` / `image_mode_entered` / `count_setter_completed` / `reference_attached`) in the PR body or a comment? **Absent live-verify on a UI-automation PR is YELLOW per memory `pr-must-verify-on-affected-surface`.**
- **D6 Data-migration & on_started safety:**
  - **Mandatory on_started callback check** (memory `on-started-callback-recorder-safety`): grep the diff for `VideoStartedCallback` / `on_started` / any new callback invoked inside a transport during a paid Flow generation. Verify each callsite is wrapped in `try/except DataStoreError` (and ideally a broad guard). **A bare callback in a paid-generation path is automatically RED** — uncaught exceptions abort paid generation.
  - Schema-compat with existing rows? Migration script idempotent?
  - `DataStoreError` vs `DataMigrationError` vs `DataIntegrityError` semantics correct? Pre-Flow failures vs post-success-warn-and-return-0 per memory `exit-code-16-data-store`?
- **D7 CLI UX:** Help text matches behavior? Flag names follow `--kebab-case`? Does the new flag appear in `--help` golden-snapshot tests? Examples in docstring runnable?
- **D8 Docs drift:** Cross-references mutually consistent (CLAUDE.md / AGENTS.md / docs/INDEX.md / README.md / PLAN.md)? Code examples actually run? Version strings consistent across files?
- **D9 Auth:** Chrome-strategy profile required (memory `real-browser-auth-mandatory`)? Profile-dir SecurityError boundary intact (`outside of GFLOW_CLI_HOME`)? No new secret-shaped strings?
- **D10 Release-gate:**
  - **Quote the version string** from `pyproject.toml` AND from `src/gflow_cli/__init__.py` and confirm they match exactly. If only one was bumped → RED.
  - Verify `CHANGELOG.md` `[Unreleased]` was emptied and a new version section added — cite the line range.
  - Wheel build clean (`uv build` + ZIP-dupe check per memory `wheel-build-sanity-gate`)?
  - Back-merge gap from prior releases addressed (memory `release-back-merge-gap-recovery`)?
- **D11 BDD step-stub signatures:** any new `_run_*` kwarg or runtime signature change requires mirroring in `tests/features/_fake_*` stubs — silent `TypeError` trap. Cite each fake stub touched (or assert no signature change occurred).
- **D12 Dev/release scripts:** any new script must run cleanly on Windows (memory `windows-dev-quirks`); release scripts must include the wheel-build sanity gate.

---

## 5 · Synthesize the verdict

After all agents return:

1. **Tally verdicts:** count GREEN / YELLOW / RED per dimension.
2. **Handle missing agents:** if any dimension failed to return (timeout, dispatch error), mark it `UNKNOWN` and downgrade the consensus by one step (GREEN → YELLOW, YELLOW → YELLOW, RED stays RED). Never wait indefinitely. Surface which dimension is missing so the user can re-dispatch if they want.
3. **Consensus rule:**
   - Any RED → **RED** (block merge).
   - Any YELLOW → **YELLOW** (soft block — must address before merge, per memory `llm-council-data-layer-fixes`).
   - All GREEN → **GREEN** (mergeable).
4. **Deduplicate must-fix AND confirmed-good items:** if two dimensions flag the same issue (or confirm the same positive), list it once and credit both.
5. **Live-verify gate** (D5 fired + PR body has unchecked live-verify boxes): emit an explicit `AskUserQuestion` with three options — (a) *Run now (estimate: ~1 Flow image credit per locale)*, (b) *Block merge — add to PR body as required reviewer action*, (c) *Skip and accept the risk*. Cite memory `verification-ledger-5-layer` in the question body so the user is reminded that file count alone is not proof. Do NOT spend credits without an explicit affirmative click.
6. **YELLOW escape valve:** when reporting in Phase 6, the `AskUserQuestion` MUST include a *"Dismiss YELLOW with justification (logged)"* option so the user is never trapped without a path forward. Dismissal requires a one-line reason that gets appended to the PR body or comment, so the override is auditable.

---

## 6 · Report

Output to the user, in this exact shape:

```
# PR #<N> — Council Review Verdict

## Consensus: <emoji> <GREEN | YELLOW | RED>

| Dimension | Verdict | Headline |
|---|---|---|
| <D1> | … | <one-line summary> |
| <D2> | … | … |
…

## Must-fix (<N>)
1. **<short title>** — `<file>:<line>`. <description>. <which dimension flagged>.
2. …

## Nice-to-have (<N>)
1. …

## Confirmed-good (high-confidence positives)
- …

## How to proceed
<AskUserQuestion: which must-fixes to apply now, whether to run live-verify, etc.>
```

Always end with an `AskUserQuestion` so the user can drive next steps (apply fixes, post evidence, dismiss findings).

---

## 7 · Provenance & extensions

> **Provenance:** the council protocol below was validated on PR #93 (locale selectors + DB isolation, merged 2026-05-26). The audit found an unanchored regex + a silent-test-pyramid gap that 152 passing unit tests missed. Then a meta-council audited *this* command itself, surfacing 13 must-fix items now applied. See memory `[[llm-council-code-review-pr93]]`.

- Council baseline = 4 dimensions (D1–D4) always run. Adaptive ceiling = D5–D12 (currently). Going beyond ~12 dimensions adds noise faster than signal — split into two reviews instead.
- Each agent currently uses `general-purpose`. If a future dedicated `code-reviewer` subagent ships, swap it in.
- If a PR touches paths that don't match any adaptive dimension, document the gap and add a new dimension here (D13+).
- The command is **stateless** — concurrent invocations on different PR numbers don't share data, so two instances can run in parallel without interference.
- **Idempotence:** running the same council on the same PR SHA should produce comparable verdicts on different days. If verdicts drift, the cause is usually (a) memory grew new precedents, or (b) the mandatory-slug table here needs an update. Drift is informational, not a defect.
- No destructive git/gh actions are taken by this command. Reviews are read-only. The user always drives apply-fix / push / merge through subsequent commands.
