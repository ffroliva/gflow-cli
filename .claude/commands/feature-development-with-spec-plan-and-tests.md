---
name: feature-development-with-spec-plan-and-tests
description: Workflow command scaffold for feature-development-with-spec-plan-and-tests in gflow-cli.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /feature-development-with-spec-plan-and-tests

Use this workflow when working on **feature-development-with-spec-plan-and-tests** in `gflow-cli`.

## Goal

Implements a new feature or major fix using a spec-driven, test-first workflow: design spec and plan docs, implementation, tests, and documentation updates.

## Common Files

- `docs/superpowers/specs/*.md`
- `docs/superpowers/plans/*.md`
- `src/gflow_cli/**/*.py`
- `tests/**/*.py`
- `CHANGELOG.md`
- `KNOWN_ISSUES.md`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Write design spec(s) and implementation plan in docs/superpowers/specs/ and docs/superpowers/plans/
- Update or add implementation code in src/gflow_cli/ (often multiple modules)
- Add or update tests in tests/ (unit and e2e as needed)
- Update documentation (docs/USAGE.md, docs/USER_GUIDE.md, docs/ARCHITECTURE.md, CHANGELOG.md, KNOWN_ISSUES.md, etc.)

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.