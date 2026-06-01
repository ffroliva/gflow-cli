# Agent Guide — mandates & routing rules

> Companion to [AGENTS.md](../AGENTS.md). AGENTS.md is the universal one-page entry point; this file collects the longer mandates that don't fit there.

## Read order

1. [AGENTS.md](../AGENTS.md) — universal coding-agent rules (Dev environment / Testing / Code style / PR conventions).
2. This file — mandates that need more than one paragraph.
3. [docs/INDEX.md](INDEX.md) — full routing layer for every other doc.
4. [docs/ARCHITECTURE.md](ARCHITECTURE.md) — target shape + current limitations.

## Mandates (must-follow)

These are non-negotiable. They override default agent behavior where conflicts exist.

- **TDD before code.** Write a failing test, then the minimum production code to make it pass, then refactor. Coverage floor: 80% overall. See [CONTRIBUTING.md](../CONTRIBUTING.md).
- **Documentation is first-class.** Any user-facing, operator-facing, architecture, configuration, workflow, or agent-rule change must update the matching docs, changelog/release note when relevant, and docs index if a new doc is added. If no docs change, record the reason in the PR/checklist. `scripts/ci/check_doc_links.py` must pass before merge.
- **No raw `print()` or `import logging` in `src/`.** Structured logging via `structlog` only.
- **No secrets in commits.** `.env.local` is gitignored; never commit it. `pre-commit` hooks run `detect-secrets` on staged content.
- **No AI attribution in commit messages.** `Co-Authored-By:` trailers are fine when explicitly requested; auto-generated `🤖 Generated with…` footers are not.
- **Branch naming.** `feature/`, `bugfix/`, `hotfix/`, `chore/`, `docs/`, `test/`, `release/`. Never `claude/` or unprefixed.
- **Signed tags only.** Releases tag with `git tag -s vX.Y.Z`. CI rejects unsigned or lightweight tags.
- **Back-merge `main → develop` after every release.** See the `release-back-merge-gap-recovery` runbook in agent memory.
- **Enforce model-dependent reference caps.** Flow's R2V (reference-to-video) and I2I (image-to-image) reference image caps are model-dependent (Omni=7, Veo Lite/Fast=3, Quality=0). These MUST be enforced at both the Domain layer (`GenerateVideoRequest`) and the CLI layer. Use `reference_cap_for(model)` and include event-based tripwires in E2E tests.

## Routing rules

- **Starting a feature?** Run `/gflow:status` to see the active phase scope, then `/gflow:predict` → `/gflow:scenario` → `/gflow:plan <feature>` to create a task checklist.
- **Touching auth, reCAPTCHA, browser flow, or anything previously flagged?** Run `/gflow:known-issues` first.
- **Cutting a release?** Run `/gflow:release` — it sequences `/gflow:changelog`, `/gflow:check`, `/gflow:doc-review`.
- **Before any commit:** Run `/gflow:check` (or the Impeccable Routine in AGENTS.md), including the documentation link gate.

## Production-ready checklist

Before merge, verify each item and record the evidence in the PR or final handoff:

- **Scope complete:** issue acceptance criteria are met, and any deferred item is named with an issue or follow-up.
- **Tests prove behavior:** behavior changes have focused tests, and the full non-live suite passes with coverage at or above 80%.
- **Documentation is current:** user-facing, operator-facing, architecture, configuration, workflow, and agent-rule changes update the matching docs; new docs are linked from `docs/INDEX.md`; `scripts/ci/check_doc_links.py` passes.
- **Quality gates pass:** repo hygiene, documentation links, ruff check, ruff format check, pyright, and pytest all pass from a clean checkout-equivalent state.
- **Operational risks are explicit:** live/e2e gaps, credit-spending tests, auth/browser limitations, and platform caveats are called out instead of hidden.
- **Git hygiene is clean:** branch targets `develop`, unrelated local changes are not included, commit messages contain no AI attribution, and generated artefacts stay out of git.
- **Memory is updated:** durable project rules or operational lessons are written to agent memory before closing the work.

## Governance & Enforcement

gflow-cli's AI-driven development flow is **followable in-repo**: the lifecycle is
documented here, and the rules that *can* be checked mechanically are. The model is
**advisory-first** — cheap, deterministic rules are hard-enforced; the
judgement-heavy gates (predict / council) are surfaced as non-blocking signals, not
gates you can game. (Patterned on the reference AI-DLC governance orchestrator, which
classifies-always but blocks only on an opt-in flag.)

### The lifecycle

```
/gflow:predict  →  /gflow:scenario  →  /gflow:plan  →  (implement)  →
/gflow:branch-review or /gflow:pr-council-review  →  /gflow:check  →  /gflow:release
```

Skip predict/scenario/plan for trivial fixes (< 10 lines, no boundary cross) and pure
doc changes. Everything else: assess before you build.

### What is hard-enforced vs advisory

| Rule | Enforced by | Type |
|---|---|---|
| No `print()` in `src/` | ruff `T20` (`tests/**`, `scripts/**` exempt) | **hard** (CI lint) |
| Doc links resolve | `scripts/ci/check_doc_links.py` | **hard** (CI) |
| No tracked artefacts / hardcoded paths | `scripts/ci/check_repo_hygiene.py` | **hard** (CI + pre-commit) |
| Coverage ≥ 80% | `pytest --cov-fail-under` / SonarCloud | **hard** (CI) |
| Signed tags | release workflow | **hard** (CI) |
| Material-path list ↔ SKILL §1 in sync | `test_material_list_sync_passes_on_real_skill` | **hard** (CI) |
| Conventional branch prefix | `check_repo_hygiene.py::_check_branch_name` | **advisory** (warns; never blocks — platforms create `claude/*` / `dependabot/*` branches, and it no-ops in CI's detached-HEAD checkout) |
| predict / council on material paths | `governance-advisory.yml` + `check_materiality.py` | **advisory** (recommendation in the job summary) |
| Traceability (plan reference + tests) | `check_materiality.py` | **advisory** (report-only) |

### Materiality coverage (path → recommended gate)

Touching these surfaces triggers an advisory recommendation to run predict + council.
The canonical list is `scripts/ci/check_materiality.py::MATERIAL_PATHS`; the priority
weights live in [`skills/pr-council-review/SKILL.md`](../skills/pr-council-review/SKILL.md) §1.

| Path | Why material | Recommended gate |
|---|---|---|
| `src/gflow_cli/auth/`, `recaptcha` | Google anti-bot / auth lifecycle | predict (security persona) + council |
| `src/gflow_cli/api/transports/` | Highest-risk transport surface; live-verify | predict + council (live-verify) |
| `src/gflow_cli/data/` | SQLite migration / data-loss risk | predict + council (migration safety) |

### Non-blocking signals

The materiality and traceability surfaces are **informational only** — they never fail
the build and are not required checks. They guide behaviour; only the hard gates above
block a merge. Do not read the advisory job's red/green as a merge gate.

### Conscious deferral

Hard enforcement of the material-path gate (an opt-in `--block-on=material` flag plus a
branch-protection required check) is **deliberately deferred**, matching the reference
implementation's opt-in design — it is a future option, not an omission. The hook point
is reserved in `check_materiality.py` (which already classifies every change).

### Satisfying a gate without Claude Code

Every gate enforces a **deliverable**, not a Claude-specific command. Non-Claude agents
(Cursor / Codex / Gemini CLI / Aider) and humans read the relevant `skills/<name>/SKILL.md`
directly and produce the same artifact — e.g. read `skills/predict/SKILL.md` and write the
5-persona verdict into the PR, instead of running `/gflow:predict`.

## When in doubt


Read [docs/INDEX.md](INDEX.md). It has a topic-shortcut block at the bottom that answers most "where do I find…?" questions in one hop.
