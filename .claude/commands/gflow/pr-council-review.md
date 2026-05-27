---
description: Multi-dimensional LLM council review of an open PR. Five baseline dimensions (correctness, quality, security, tests, memory-hygiene) plus adaptive per-surface dimensions. Sub-agents invoke specialized skills (security-review, code-review, verify). With no argument, lists open PRs ranked by priority. Wrapper around skills/pr-council-review/SKILL.md (canonical body).
---

# `/gflow:pr-council-review [PR#]`

**Invoke `Skill(skill="pr-council-review")` now**, passing `$ARGUMENTS` (the PR number, or empty for prioritize-mode). The skill at `skills/pr-council-review/SKILL.md` contains the full protocol: preflight, dimension detection, parallel dispatch, synthesis, report.

Sibling: `/review` is the single-agent Claude-Code built-in (fast, one-pass). Use it for spot-checks; use this command for pre-merge multi-dim audits.
