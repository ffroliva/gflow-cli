---
name: llm-council-doc-review-v0.9.0
description: "Second validation of the 3-agent LLM council protocol for `/gflow:doc-review` — caught 7 Tier 1 doc gaps on v0.9.0 that the mechanical checklist missed"
---

The LLM council protocol (3 parallel auditors: completeness / cross-reference / drift) was first validated on the v0.8.1 release. It paid for itself again on v0.9.0 — found 7 Tier 1 documentation gaps that all three auditors flagged consistently, before the v0.9.0 tag was re-signed.

**v0.9.0 council findings the mechanical checklist would have missed:**

1. `README.md:107` — claimed "v0.9.0 — alpha (latest on PyPI)" before publish actually succeeded.
2. `README.md:107` — claimed "sponsorship wiring" shipped after it had been stripped 4 commits earlier.
3. `README.md:101` — claimed experimental HTTP transports "have not yet been extracted into a subpackage" — the `src/gflow_cli/api/transports/experimental/` package exists with all three modules. This was a re-incarnation of the same fiction caught on v0.8.1 — drift creeps back in.
4. `docs/ARCHITECTURE.md:55` — data layer described as "feature/data-layer, targeting v0.9.0" with stale DB filename `gflow.db`. Real default is `data.db` under `GFLOW_CLI_DB_PATH`. Two doc errors in one paragraph.
5. `docs/USAGE.md` — zero coverage of `gflow data list` (the marquee v0.9.0 feature). Only `gflow data media` existed there.
6. `docs/INDEX.md` — "latest live-verified release" shortcut still pointed at v0.8.1.
7. `docs/PROJECT_STATUS.md` — Current release line + Develop line + milestone-history rows all stale for v0.9.0; PR #74 (the wheel-build hotfix) absent.
8. `CHANGELOG.md [0.9.0]` — did not mention PR #74, the wheel-build fix that was IN the commit being tagged.

**Why:** All 3 auditors returned YELLOW (no RED). YELLOW is not "release-blocking" by the skill's strict letter, but the findings were release-blocking by reputation — shipping a README that lies about your own publish state, or a roadmap that brags about features stripped earlier, is the kind of thing reviewers screenshot and roast you for. **Treat the council's YELLOW as a soft block: apply Tier 1 fixes before the tag, even if the verdict isn't RED.**

**How to apply:**

- Run the council on every release (not just `>= v0.8.1` — the protocol pays for itself reliably).
- Apply Tier 1 fixes BEFORE the tag. Don't ship a wrong claim about your own release into the PyPI artifact.
- Watch for "fiction re-incarnation" — claims caught on a previous release can creep back in when a section is edited. The "experimental subpackage doesn't exist yet" claim was fixed for v0.8.1 and reappeared in a different README section by v0.9.0. Maintain a stable set of test-claims the council validates each release.
- The auditor 3 (Drift & staleness) is the highest-leverage of the three — it does the empirical filesystem grep that the human reviewer skips. Always include it.

Linked: [[llm-council-audit-protocol]], [[release-spec-plan-memory-consolidation]], [[pypi-readme-staleness-fix]].
