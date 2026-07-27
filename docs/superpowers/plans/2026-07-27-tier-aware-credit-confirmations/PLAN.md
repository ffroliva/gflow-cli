# Tier-aware Credit Confirmations Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature tier-aware-credit-confirmations` to find
> the next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Stop `gflow video chain` and `gflow movie run` from presenting pending video work as an
exact one-credit-per-operation charge while preserving all execution, resume, confirmation, and
machine-output behavior.

**Architecture:** Keep the change at the existing Click presentation boundary in
`cli_video.py` and `cli_movie.py`. Reuse each command's current pending/completed/stale decisions,
rename local credit variables to operation counts, and direct users to Google Flow for current
model/duration/tier pricing. Do not add a pricing service, entitlement probe, new flag, new movie
confirmation, transport call, log event, or MCP schema field.

**Predict verdict:** GO — confidence 9/10. The UX lens returned CAUTION only until movie's current
non-interactive behavior was made explicit; the resolved decision is to keep it unchanged.

**Risk register:**

| Severity | Risk | Mitigation |
|---|---|---|
| Critical | A user approves paid work using a false numeric estimate | Remove numeric credit math and say the count is pending video operations, not a charge |
| High | Resume or stale-scene accounting diverges from execution | Reuse `remaining_links` and the existing completed/stale predicate; pin both in tests |
| High | A wording change moves client/tool/browser work before the safety gate | Test dry-run and declined confirmation with tripwire mocks |
| High | Human-output changes leak into JSON or MCP contracts | Add JSON shape regression coverage; change no flags, DTOs, or schemas |
| Medium | Docs, Click help, tests, and agent guidance drift | Search all current one-credit/credit-estimate claims and update canonical mirrors together |
| Medium | Scope grows into guessed dynamic pricing | Explicit non-goal; Flow remains authoritative until a verified price source exists |

---

## File structure

### New files

```text
docs/superpowers/plans/2026-07-27-tier-aware-credit-confirmations/SCENARIO.md
  Severity-ranked edge cases and test matrix.
docs/superpowers/plans/2026-07-27-tier-aware-credit-confirmations/PLAN.md
  This implementation plan.
```

### Modified files

```text
src/gflow_cli/cli_video.py
  Chain plan/help/confirmation describe pending operations and variable Flow pricing.
src/gflow_cli/cli_movie.py
  Movie plan lines and totals describe pending operations rather than credits.
tests/cli/test_cli_video_chain.py
  Red/green Click regressions for dry-run, resume, decline, --yes, and JSON stability.
tests/cli/test_cli_movie.py
  Red/green Click regressions for completed/stale/new scene accounting.
tests/features/video_chain.feature
tests/features/test_video_chain_steps.py
  BDD wording and resume/dry-run acceptance scenarios.
docs/USAGE.md
docs/MOVIE.md
skills/gflow-cli/SKILL.md
  Current user and agent guidance; no fixed per-link/per-scene price.
website/docs/USAGE.md
website/docs/MOVIE.md
  Generated canonical mirrors.
CHANGELOG.md
  Unreleased fix note for corrected operator pricing guidance.
```

Other current test docstrings/help strings found by the final bounded search may be updated only
when they repeat the same fixed one-credit contract. Historical changelog, known-issue, spike, and
live-verification evidence remains unchanged.

---

## Task 1 — Write failing CLI and BDD regressions

**What:** Pin truthful pending-operation output and the unchanged safety/compatibility boundaries
before production edits.

**Files:**

- `tests/cli/test_cli_video_chain.py` — chain plan, resume, confirmation, and JSON assertions.
- `tests/cli/test_cli_movie.py` — movie plan completed/stale/new accounting.
- `tests/features/video_chain.feature` — user-visible dry-run and resume scenarios.
- `tests/features/test_video_chain_steps.py` — mocked step bindings and assertions.

**Steps:**

- [ ] Replace the old fixed-credit expectations with the desired pending-operation contract.
- [ ] Add a resumed dry-run scenario whose completed links are excluded.
- [ ] Pin the variable-cost warning and Flow-authority guidance.
- [ ] Pin the absence of `Estimated credits`, `one per link/scene`, and numeric charge claims.
- [ ] Prove declined chain confirmation constructs no client and invokes no tool/generation work.
- [ ] Prove `--yes` and JSON output behavior remain unchanged.
- [ ] Run the focused tests and record the expected failures against the old production wording.

**Tests created (red):**

- [ ] Fresh chain dry-run reports N pending video operations without a numeric credit estimate.
- [ ] Resumed chain dry-run counts only remaining links.
- [ ] Declined confirmation performs zero external work.
- [ ] Movie dry-run counts new and stale scenes but skips completed scenes.
- [ ] JSON result schema remains unchanged.

Do not commit the intentionally red scaffold. Commit it atomically with Task 2 after green.

---

## Task 2 — Implement the minimal CLI presentation fix

**What:** Make the failing tests pass with local count/name/text changes only.

**Files:**

- `src/gflow_cli/cli_video.py`
- `src/gflow_cli/cli_movie.py`
- Task 1 test files

**Steps:**

- [ ] In chain plan/confirmation, use the existing `remaining_links` count and remove numeric
  credit language.
- [ ] Print a plain-ASCII warning that video operations may consume credits and current cost varies
  by model, duration, account tier, and Flow policy.
- [ ] Keep the existing chain confirmation position and `--yes` behavior.
- [ ] In movie plan output, show `pending`/`re-run` status and total pending video operations using
  the existing completed/stale decision.
- [ ] Keep movie non-interactive and preserve normal/dry-run execution ordering.
- [ ] Change no request DTO, API call, browser path, JSON key, flag, exit code, or MCP mapping.
- [ ] Run focused unit and BDD tests green.
- [ ] Run `/gflow:check`; commit tests and production code atomically.

**Tests green:**

- [ ] `uv run pytest tests/cli/test_cli_video_chain.py tests/cli/test_cli_movie.py tests/features/test_video_chain_steps.py -q`
- [ ] Existing adjacent chain/movie tests remain green.

---

## Task 3 — Align current documentation, skills, and mirrors

**What:** Remove the same fixed-price contract from directly related current guidance while
preserving historical evidence.

**Files:**

- `docs/USAGE.md`
- `docs/MOVIE.md`
- `skills/gflow-cli/SKILL.md`
- `website/docs/USAGE.md`
- `website/docs/MOVIE.md`
- `CHANGELOG.md`
- Current test/help/docstring files discovered by the bounded search, if directly equivalent

**Steps:**

- [ ] Say N links/scenes are N pending video generation operations, not N Flow credits.
- [ ] State that current credit use varies by model, duration, account tier, promotion, and Flow
  policy; direct the operator to verify in Flow.
- [ ] Describe submitted failures conservatively: an accepted priced operation may consume credits
  even if later polling/download fails.
- [ ] Keep historical observed amounts and release evidence unchanged.
- [ ] Regenerate `website/docs/` with the canonical generator.
- [ ] Add an Unreleased changelog entry.
- [ ] Run links, PII, mirror, Ruff/format, Pyright, and focused tests; commit.

---

## Task 4 — Final bounded audit and review

**What:** Prove the runtime contract and its direct current documentation are consistent without
absorbing the broader eligibility-doc branch.

**Steps:**

- [ ] Search current CLI/help/tests/docs/skills for `one credit per`, `one per link`,
  `Estimated credits`, numeric `credit(s)` plans, and `credit estimate`.
- [ ] Classify every remaining match as current-safe, historical evidence, or unrelated backlog.
- [ ] Verify executable diff changes only human presentation/count variable names and test
  expectations; no generation or transport calls change.
- [ ] Run the full Impeccable Routine with the repository's non-live marker guidance.
- [ ] Run an independent spec review followed by a quality review.
- [ ] Re-check open PRs/remote branches before creating a PR.

---

## Definition of done

- [ ] Every Critical/High scenario in `SCENARIO.md` is covered.
- [ ] Red-green TDD evidence is recorded for the old fixed-credit output.
- [ ] Chain resume and movie stale/completed counts match execution decisions.
- [ ] Dry-run and declined confirmation remain zero-I/O.
- [ ] `--yes`, JSON, flags, exit codes, MCP schemas, transport, and auth remain unchanged.
- [ ] Current direct docs/skills/mirrors contain no fixed one-credit-per-operation promise.
- [ ] Historical evidence is preserved.
- [ ] `CHANGELOG.md` `[Unreleased]` is updated.
- [ ] `/gflow:check` is green with at least 80% coverage.
- [ ] Final independent reviews are green.
- [ ] No `# TODO` appears in the diff without a tracked issue.
