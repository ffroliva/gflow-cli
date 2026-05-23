---
name: back-merge-release-into-develop
description: Workflow command scaffold for back-merge-release-into-develop in gflow-cli.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /back-merge-release-into-develop

Use this workflow when working on **back-merge-release-into-develop** in `gflow-cli`.

## Goal

Synchronize changes from the main branch (after a release) back into the develop branch to keep feature development up-to-date with released code and documentation.

## Common Files

- `CHANGELOG.md`
- `pyproject.toml`
- `src/gflow_cli/__init__.py`
- `uv.lock`
- `README.md`
- `AGENTS.md`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Merge main into develop after a release.
- Resolve any merge conflicts.
- Update versioned files (CHANGELOG.md, pyproject.toml, uv.lock, __init__.py).
- Update or add documentation files as needed (README.md, AGENTS.md, docs/*, etc).

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.