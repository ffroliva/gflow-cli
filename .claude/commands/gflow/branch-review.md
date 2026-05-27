---
description: Multi-dimensional LLM council review on the current local feature branch — pre-PR review. Same dimensions as `/gflow:pr-council-review` but reads `git diff <base>..HEAD` instead of a PR; never posts to GitHub. Wrapper around skills/pr-council-review/SKILL.md § 8 (branch mode).
---

# `/gflow:branch-review [--base <ref>]`

**Invoke `Skill(skill="pr-council-review")` now in BRANCH MODE**, passing `$ARGUMENTS` (optional `--base <ref>`; default `develop`).

The canonical body is `skills/pr-council-review/SKILL.md`. § 8 documents branch-mode-specific behavior: pre-flight, the PR→branch translation table, `release/*` downgrade, SHA drift, and local-only output. All other phases (gather context, detect dimensions, dispatch, synthesize, report) are identical to PR mode.

Sibling: `/gflow:pr-council-review <N>` runs the same council against an open PR.
