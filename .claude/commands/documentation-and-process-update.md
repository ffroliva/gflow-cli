---
name: documentation-and-process-update
description: Workflow command scaffold for documentation-and-process-update in gflow-cli.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /documentation-and-process-update

Use this workflow when working on **documentation-and-process-update** in `gflow-cli`.

## Goal

Adds or updates project documentation, especially process docs (DEVELOPMENT.md, CONTRIBUTING.md, INDEX.md), often in response to workflow or test suite changes.

## Common Files

- `docs/DEVELOPMENT.md`
- `CONTRIBUTING.md`
- `docs/INDEX.md`
- `.github/PULL_REQUEST_TEMPLATE.md`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Edit or add documentation files (docs/DEVELOPMENT.md, CONTRIBUTING.md, docs/INDEX.md, .github/PULL_REQUEST_TEMPLATE.md)
- Update references or cross-links between docs
- Clarify or correct test/process instructions

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.