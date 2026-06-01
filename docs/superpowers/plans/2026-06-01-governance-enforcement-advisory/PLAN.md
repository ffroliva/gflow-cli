# In-Project Governance Enforcement (Advisory-First) Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature governance-enforcement-advisory`
> to find the next unchecked task. Implement one task at a time. Run `/gflow:check`
> before every commit.

**Goal:** Make gflow-cli's existing AI-driven governance flow (predict → scenario →
plan → council → check → release) *enforceable and discoverable in-repo* — so any
contributor or agent can follow it — without adding a parallel compliance layer.
Enforcement is **advisory-first**: cheap hygiene rules become hard checks; the
materiality + traceability surfaces are non-blocking signals.

**Architecture:** No new runtime code in `src/gflow_cli/`. Three surfaces change:
(1) `pyproject.toml` ruff config + `scripts/ci/check_repo_hygiene.py` gain enforcement
of rules AGENTS.md already mandates; (2) a new `scripts/ci/check_materiality.py` +
a dedicated advisory CI job classify touched paths and emit a non-blocking
governance summary; (3) docs (`docs/AGENT_GUIDE.md`, `docs/INDEX.md`) document the
flow and a static path→gate coverage table. **AGENTS.md remains the single source
of truth** — no `steering.yml`, no rule duplication, no generated `GOVERNANCE.md`.

**Predict verdict:** CAUTION → GO on this reshaped scope — confidence 8/10
(`/gflow:predict`, 5-persona council, 2026-06-01). The original 4-part proposal
scored 5/10; the council cut the steering-YAML duplication, the gameable proof-label
hard-block, and the generated governance doc, and validated the reshaped advisory
scope against the reference implementation's own behavior.

**Reference grounding:** Modeled on `kdeath83/ai-dlc-governance-orchestrator`'s
*actual* enforcement, read from source: its risk gate **classifies always, blocks
only on opt-in `--block-on=material`, and delegates real blocking to branch
protection**; its traceability audit **reports but never blocks** (the AI-marker is
logged but excluded from pass/fail). A framework built for regulated banks chose
advisory-by-default — so this lower-stakes CLI goes no harder.

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| High | Self-applied "proof" label is gameable + breaks forked PRs (Security 9/10) | No label gate. Advisory comment/summary only; never a required check. |
| High | `steering.yml` duplicates AGENTS.md → silent drift (Architect 9, Devil's-Advocate 10) | No YAML policy file. Enforce existing rules via ruff + hygiene script. |
| High | Generated `GOVERNANCE.md` decays + adds an 8th entry doc (Contributor-UX 9) | Static section in `docs/AGENT_GUIDE.md` + `docs/INDEX.md` routing row only. |
| High | "tests must change when src changes" → 20–30% false positives (DX 9/10) | Traceability is a **report-only signal**, never a gate. Refactor/docstring/deletion diffs exempt by construction (no enforcement). |
| Medium | `T20` false-positives on sanctioned `console.print` / test-debug prints | `T20` flags only builtin `print()`; `console.print` is an attribute call (unaffected). `tests/**` per-file-ignore for `T20`. |
| Medium | Materiality path list forks from `pr-council-review` §1 → divergence | Single canonical list lives in `check_materiality.py`; doc table + skill cross-reference it. One place to update; doc-link check guards the reference. |
| Medium | Forked-PR token is read-only → can't post a comment | Primary surface is `$GITHUB_STEP_SUMMARY` (works on forks, no token). PR comment is best-effort, same-repo only. |
| Low | Branch-naming check breaks in detached HEAD / CI / tags | `_check_branch_name()` no-ops when HEAD is detached or branch is `main`/`develop`/`HEAD`. |

---

## File structure

### New files
```
scripts/ci/check_materiality.py
  Classify git-diff touched paths into material vs routine; emit advisory
  governance report (markdown) to stdout / $GITHUB_STEP_SUMMARY. Never exits non-zero.
tests/ci/test_check_materiality.py
  Unit tests: material-path detection, routine classification, plan-reference
  signal, test-change signal, exit-code-always-0, tool-agnostic remediation text.
tests/ci/test_check_repo_hygiene_branch.py
  Unit tests for the new branch-naming check (valid prefix, bad prefix,
  detached HEAD no-op, protected-branch no-op).
.github/workflows/governance-advisory.yml
  Dedicated non-blocking job on pull_request; runs check_materiality.py and
  writes the report to the job summary (forked-PR safe).
```

### Modified files
```
pyproject.toml
  Add "T20" to [tool.ruff.lint] select; add [tool.ruff.lint.per-file-ignores]
  with "tests/**" = ["T20"].
scripts/ci/check_repo_hygiene.py
  Add _check_branch_name(); wire into main(); keep exit semantics.
docs/AGENT_GUIDE.md
  New "Governance & Enforcement" section + static path→gate coverage table.
docs/INDEX.md
  Routing row pointing at the new AGENT_GUIDE section.
AGENTS.md
  One line under "PR instructions"/"Code style" noting T20 + branch-naming are
  now machine-enforced (so the prose and the check stay visibly linked).
CHANGELOG.md
  [Unreleased] entries for the new checks + advisory job.
```

---

## Task 1 — Branch-naming hygiene check (test scaffold)

**What:** Red unit tests for a branch-name validator before writing it.

**Files:**
- `tests/ci/test_check_repo_hygiene_branch.py` — new

**Steps:**
- [ ] Create `tests/ci/__init__.py` if `tests/ci/` does not exist.
- [ ] Write tests importing `_check_branch_name` from `scripts.ci.check_repo_hygiene` (add path shim if needed; mirror how existing CI-script tests import, or invoke via subprocess if the script isn't importable).

**Tests created (red):**
- [ ] `test_valid_prefix_passes` — `feature/foo` → no error returned.
- [ ] `test_invalid_prefix_flagged` — `myfix` → returns one actionable error string.
- [ ] `test_detached_head_noops` — detached HEAD → returns no error.
- [ ] `test_protected_branch_noops` — `main`/`develop` → returns no error.

---

## Task 2 — Branch-naming hygiene check (implementation)

**What:** Make Task 1 green; enforce the prefix mandate AGENTS.md already states.

**Files:**
- `scripts/ci/check_repo_hygiene.py` — add `_check_branch_name()`, wire into `main()`.

**Steps:**
- [ ] Resolve current branch via `git rev-parse --abbrev-ref HEAD`; treat `HEAD` (detached) and `main`/`develop` as no-op.
- [ ] Validate against `^(feature|bugfix|hotfix|chore|docs|test|release)/`.
- [ ] On mismatch, return a message naming the branch and listing valid prefixes (actionable remediation, consistent with existing hygiene messages).
- [ ] Aggregate into `main()`'s error list so exit code stays 0/1 as today.
- [ ] Run `/gflow:check`.

**Tests:** Task 1 tests pass green.

---

## Task 3 — Ruff T20 print-ban

**What:** Actually enforce "no raw `print()` in `src/`" (AGENTS.md mandates it; nothing checks it).

**Files:**
- `pyproject.toml` — add `"T20"` to `[tool.ruff.lint] select`; add `[tool.ruff.lint.per-file-ignores]` `"tests/**" = ["T20"]`.

**Steps:**
- [ ] Add `T20`; confirm `uv run ruff check src tests` stays green (0 builtin `print()` in `src/`/`tests/` today; 84 `console.print` are attribute calls and unaffected).
- [ ] Add the `tests/**` ignore so future test-debug `print()` doesn't block contributors.
- [ ] Note in AGENTS.md that `T20` machine-enforces the rule.

**Tests created (red→green):**
- [ ] Manual/inline: a temporary `print("x")` added to a `src/` file fails `ruff check`; removing it passes. (Document the check; do not commit the temp file.)

---

## Task 4 — Materiality classifier (test scaffold)

**What:** Red tests for the advisory classifier before implementation.

**Files:**
- `tests/ci/test_check_materiality.py` — new

**Steps:**
- [ ] Define the canonical material-path list as a module constant in the (not-yet-written) `check_materiality.py`; tests assert against it.

**Tests created (red):**
- [ ] `test_auth_path_is_material` — `src/gflow_cli/auth/x.py` → material.
- [ ] `test_transports_path_is_material` — `src/gflow_cli/api/transports/x.py` → material.
- [ ] `test_data_and_recaptcha_material` — `data/`, `recaptcha` → material.
- [ ] `test_docs_only_is_routine` — `docs/x.md` → routine.
- [ ] `test_exit_code_always_zero` — even with material paths, process exits 0.
- [ ] `test_remediation_is_tool_agnostic` — report text mentions `/gflow:predict` AND `skills/predict/SKILL.md` AND a human path.
- [ ] `test_plan_reference_signal` — branch with a `docs/superpowers/plans/...` reference reports the traceability checkbox checked.
- [ ] `test_no_block_when_tests_absent` — touched `src/` without test changes → signal reported, exit still 0.

---

## Task 5 — Materiality classifier (implementation)

**What:** Make Task 4 green. Classify diff, emit advisory markdown, never block.

**Files:**
- `scripts/ci/check_materiality.py` — new

**Steps:**
- [ ] Compute touched paths from `git diff --name-only <base>...HEAD` (base defaults to `origin/develop`; overridable via arg/env).
- [ ] Classify each path with the canonical material list (the single source; `pr-council-review` §1 and the doc table reference *this*).
- [ ] Build a markdown report: material paths found, recommended gates (`/gflow:predict`, `/gflow:pr-council-review`), tool-agnostic remediation, and the report-only traceability checklist (plan referenced? touched-src test changes present?).
- [ ] Print to stdout; if `$GITHUB_STEP_SUMMARY` is set, append there too.
- [ ] **Always `sys.exit(0)`** — this is advisory.
- [ ] Run `/gflow:check`.

**Tests:** Task 4 tests pass green.

---

## Task 6 — Advisory CI job

**What:** Wire the classifier into CI as a **non-blocking** job, forked-PR safe.

**Files:**
- `.github/workflows/governance-advisory.yml` — new

**Steps:**
- [ ] Trigger on `pull_request`; `permissions:` read-only contents (no extra write scope needed for the summary surface).
- [ ] Fetch base ref; run `uv run python scripts/ci/check_materiality.py`.
- [ ] Primary surface = `$GITHUB_STEP_SUMMARY` (works on forks). Optional best-effort PR comment guarded to same-repo PRs only.
- [ ] Job must not be a required status check; it cannot fail the merge.

**Tests:**
- [ ] Self-verify: the PR for *this* plan touches no material `src/` paths, so the job summary should classify it routine — confirm on the live PR.

---

## Task 7 — Governance docs (the followable flow + coverage table)

**What:** Make the flow discoverable so any contributor/agent can follow it.

**Files:**
- `docs/AGENT_GUIDE.md` — new "Governance & Enforcement" section + static
  path→gate coverage table (auth/transports/data/recaptcha → predict + council;
  with the rationale per path).
- `docs/INDEX.md` — routing row → the new section.
- `AGENTS.md` — one line linking the prose rules to their machine enforcement.

**Steps:**
- [ ] Write the "Governance & Enforcement" section: the full lifecycle
      (predict → scenario → plan → council → check → release), what is hard-enforced
      (T20, branch-naming, coverage floor, doc-links, signed tags) vs advisory
      (materiality + traceability), and how non-Claude agents satisfy each gate
      (read the SKILL.md; produce the deliverable).
- [ ] Add the coverage table; cross-reference `skills/pr-council-review/SKILL.md` §1
      as the canonical priority weights.
- [ ] Add the `docs/INDEX.md` routing row.
- [ ] Run `uv run python scripts/ci/check_doc_links.py` (merge gate).

**Tests:**
- [ ] `check_doc_links.py` green (no broken links introduced).

---

## Task 8 — Full gates + changelog

**What:** Green the whole suite; record the change.

**Files:**
- `CHANGELOG.md` — `[Unreleased]` entries.

**Steps:**
- [ ] `/gflow:check` green (ruff incl. T20 / format / pyright / pytest ≥ 80%).
- [ ] `check_repo_hygiene.py` + `check_doc_links.py` green.
- [ ] CHANGELOG `[Unreleased]`: branch-naming check, T20 print-ban, advisory
      materiality+traceability job, governance docs.

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage)
- [ ] `CHANGELOG.md` `[Unreleased]` section updated
- [ ] Docs updated (`docs/AGENT_GUIDE.md`, `docs/INDEX.md`, `AGENTS.md`)
- [ ] New CI checks have unit tests; advisory job verified non-blocking on the live PR
- [ ] No new top-level doc; no `steering.yml`; no gameable proof-label; no hard block
- [ ] No `# TODO` in diff without a tracked issue link
- [ ] LLM council (`/gflow:branch-review`) run before implementation begins (per session flow)
