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
- **No raw `print()` or `import logging` in `src/`.** Structured logging via `structlog` only.
- **No secrets in commits.** `.env.local` is gitignored; never commit it. `pre-commit` hooks run `detect-secrets` on staged content.
- **No AI attribution in commit messages.** `Co-Authored-By:` trailers are fine when explicitly requested; auto-generated `🤖 Generated with…` footers are not.
- **Branch naming.** `feature/`, `bugfix/`, `hotfix/`, `chore/`, `docs/`, `test/`, `release/`. Never `claude/` or unprefixed.
- **Signed tags only.** Releases tag with `git tag -s vX.Y.Z`. CI rejects unsigned or lightweight tags.
- **Back-merge `main → develop` after every release.** See the `release-back-merge-gap-recovery` runbook in agent memory.
- **Enforce model-dependent reference caps.** Flow's R2V (reference-to-video) and I2I (image-to-image) reference image caps are model-dependent (Omni=7, Veo Lite/Fast=3, Quality=0). These MUST be enforced at both the Domain layer (`GenerateVideoRequest`) and the CLI layer. Use `reference_cap_for(model)` and include event-based tripwires in E2E tests.

## Routing rules

- **Starting a feature?** Run `/gflow:plan` first to see the active phase scope and definition of done.
- **Touching auth, reCAPTCHA, browser flow, or anything previously flagged?** Run `/gflow:known-issues` first.
- **Cutting a release?** Run `/gflow:release` — it sequences `/gflow:changelog`, `/gflow:check`, `/gflow:doc-review`.
- **Before any commit:** Run `/gflow:check` (or the Impeccable Routine in AGENTS.md).

## When in doubt

Read [docs/INDEX.md](INDEX.md). It has a topic-shortcut block at the bottom that answers most "where do I find…?" questions in one hop.
