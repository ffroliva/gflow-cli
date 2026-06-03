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

## Command surfaces

The `gflow` CLI exposes these command groups (full reference in [docs/USAGE.md](USAGE.md)):

- `gflow auth` — one-time Chrome login, status, logout.
- `gflow image` — `t2i` / `i2i` / `upload` (Imagen / Nano Banana).
- `gflow video` — `t2v` / `i2v` / `r2v` / `batch`, plus `chain` (last-frame I2V chaining from a JSONL manifest; link 0 is t2v, later links are i2v seeded by the previous clip's last frame; veo models only).
- `gflow character` — `create` / `list` / `show` / `voices`: reusable, project-scoped Flow Character entities (a named subject with reference images, optional voice and personality) for consistent subjects across generations. See [docs/CHARACTER.md](CHARACTER.md).
- `gflow scene` — `create` / `show`: compose ordered clips into a scene; `create --output` renders a credit-free server-side extended video via `runVideoFxConcatenation` (no local ffmpeg).
- `gflow data` — query the local SQLite catalog.

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

## When in doubt

Read [docs/INDEX.md](INDEX.md). It has a topic-shortcut block at the bottom that answers most "where do I find…?" questions in one hop.
