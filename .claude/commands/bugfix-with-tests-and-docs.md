---
name: bugfix-with-tests-and-docs
description: Workflow command scaffold for bugfix-with-tests-and-docs in gflow-cli.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /bugfix-with-tests-and-docs

Use this workflow when working on **bugfix-with-tests-and-docs** in `gflow-cli`.

## Goal

Fixes a bug (often auth-related), updates implementation, adds or updates tests, and amends documentation and changelog.

## Common Files

- `src/gflow_cli/**/*.py`
- `tests/**/*.py`
- `CHANGELOG.md`
- `docs/USAGE.md`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Fix implementation in src/gflow_cli/
- Update or add relevant tests in tests/
- Update documentation (docs/USAGE.md, CHANGELOG.md, etc.)

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.