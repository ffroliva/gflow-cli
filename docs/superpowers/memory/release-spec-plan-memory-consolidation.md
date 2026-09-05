---
name: release-spec-plan-memory-consolidation
description: "After every release that produced a spec and plan under docs/superpowers/, extract durable knowledge into memory entries, then delete the spec/plan files from the repo. They are project-management artifacts, not public documentation."
---

Specs (`docs/superpowers/specs/YYYY-MM-DD-*-design.md`) and plans (`docs/superpowers/plans/YYYY-MM-DD-*.md`) are internal project-management artifacts. **They do not belong in the public repository** — users at the edge don't need to know how the work was organized, only what shipped.

**Why:** The repo's `docs/` tree is user-facing. Mixing planning artifacts (which decay fast) with reference docs (which need to stay current) pollutes the routing layer at `docs/INDEX.md` and confuses new contributors. The user explicitly flagged this on v0.8.1: "i dont think this information should be part of the public repository as this is project management information."

**How to apply** (run as the final cleanup before pushing the release branch):

1. **Extract durable patterns** from the spec/plan into memory entries. Anything that future Claude Code sessions should remember (patterns that worked, governance rules, decisions with rationale) → new file under `~/.claude/projects/<...>/memory/`, indexed in `MEMORY.md`.
2. **Delete** the spec file at `docs/superpowers/specs/YYYY-MM-DD-*-design.md`.
3. **Delete** the plan file at `docs/superpowers/plans/YYYY-MM-DD-*.md`.
4. **Keep** local-only research digests under `tmp/` if any — they're already gitignored and serve no purpose in the repo.
5. **Commit** as a final "chore: consolidate v<release> planning artifacts into memory" before pushing.
6. (Optional) If `docs/superpowers/` is now empty of recent files, consider whether to keep it at all. As of v0.8.1 there are still active historical specs/plans there — leave them until they're separately consolidated.

This is now codified in `.claude/commands/gflow/doc-review.md` section "Post-release: memory consolidation."

Related: [[llm-council-audit-protocol]], [[release-back-merge-gap-recovery]].
