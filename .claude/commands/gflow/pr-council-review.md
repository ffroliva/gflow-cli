---
description: Multi-dimensional LLM council review of an open PR. Five baseline dimensions (correctness, quality, security, tests, memory-hygiene) plus adaptive per-surface dimensions. Sub-agents invoke specialized skills (security-review, code-review, verify). With no argument, lists open PRs ranked by priority. Wrapper around skills/pr-council-review/SKILL.md (canonical body).
---

# `/gflow:pr-council-review [PR#]`

**Read `skills/pr-council-review/SKILL.md` and follow its protocol now**, treating `$ARGUMENTS` as the PR number (or empty for prioritize-mode). That file is the canonical body: preflight, dimension detection, parallel dispatch, synthesis, report.

> Do **not** call `Skill(skill="pr-council-review")` — the repo's `skills/*/SKILL.md` files are plain Markdown, not registered as Skill-tool-invocable (only `.claude/commands/gflow/*` are). Invoking it errors with `Unknown skill: pr-council-review`. Read the file directly instead.

**Required gate — SonarCloud must be green.** The council reviews the *diff*; it does
not see SonarCloud's verdict. Before declaring the final verdict, run **`/gflow:sonar`**
for this PR — the `SonarCloud analysis` gate must be green (zero new issues). A red gate
is a blocking finding regardless of the council's own conclusions; report it with the
exact failing conditions and do not call the PR merge-ready until it is green.

Sibling: `/review` is the single-agent Claude-Code built-in (fast, one-pass). Use it for spot-checks; use this command for pre-merge multi-dim audits.
