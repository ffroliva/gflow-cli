---
name: pr-council-review-stale-tree-reads
description: "Bug in /gflow:pr-council-review — sub-agents called Read on local working tree (on develop) instead of PR HEAD, producing 5+ false-positive must-fix items on PR"
---

When `/gflow:pr-council-review <N>` dispatches sub-agents, the agents work in the orchestrator's CWD which is typically on `develop` (the integration branch the user is normally on). The command prompt tells them to "Read the unchanged surrounding code with Read for any file you flag" — but `Read` then returns the file as it exists on `develop`, NOT as the PR will look post-merge. The agents conflate the two and report "file X doesn't exist" or "claim Y not applied" when in fact X exists and Y was applied on the PR HEAD.

**Why:** Surfaced 2026-05-27 running the council on PR #95 (e2e test strategy). D1 and D4 produced 5 false-positive must-fix items, including:
- "`tests/api/transports/test_transport_timeout.py` DOES NOT EXIST" — file exists on PR HEAD, missing from develop
- "smoke test rename NOT applied" — rename present on PR HEAD, absent from develop
- "Cost sub-markers not registered" — all 6 registered on PR HEAD pyproject.toml

This is a serious quality bug — false RED verdicts erode trust in the council. The user pre-emptively flagged it (`i notice that the agents from the pr-council-review did not use a specialized agent for security with security skill capabilities`) and we also independently spotted the stale-tree-read pattern by sanity-checking 3 disputed claims with `git show origin/<head>:<path>`.

**How to apply (the fix the next PR ships):**

1. Update the per-dimension prompt skeleton in `.claude/commands/gflow/pr-council-review.md` (and the soon-to-exist `skills/pr-council-review/SKILL.md`) with this mandatory paragraph:

   > **CRITICAL — file reading rule:** The orchestrator's working tree is on `develop`, not the PR HEAD. **Do NOT use `Read` on files in `C:\development\github\gflow-cli\` — that returns the develop copy and produces false positives.** Instead:
   > - For diff content: `gh pr diff <N>` via `ctx_execute`.
   > - For unchanged-context inspection of a file as it WILL look post-merge: `git show origin/<head-branch>:<path>` via `ctx_execute`.
   > - For PR metadata: `gh pr view <N> --json files,body,...`.
   > - Only use `Read` if you have first verified you are on the PR head branch (`git branch --show-current`).

2. Add a Phase 0 preflight assertion: orchestrator captures `head_branch = gh pr view <N> --json headRefName --jq '.headRefName'` and explicitly passes it to each sub-agent.

3. Per-dimension prompt updates: every "Read the unchanged surrounding code with Read" instruction becomes "use `git show origin/<head>:<path>` via `ctx_execute`."

**Related — also fixed in the same PR:**
- Dimension → specialized agent/skill mapping (current `general-purpose` for all 7 agents misses `security-review`, `code-review`, `review`, `verify` built-ins). See [[pr-council-review-portability-backlog]] Phase A.
- Skill extraction to `skills/pr-council-review/SKILL.md` so the body is reusable across tools. Same backlog Phase A.

After the fix lands, re-run the PR #95 council (Hybrid sequencing per Flavio 2026-05-27) to validate the false-positive rate drops.

Related: [[llm-council-code-review-pr93]], [[pr-council-review-portability-backlog]], [[pr-must-verify-on-affected-surface]].
