---
name: llm-council-data-layer-fixes
description: Third validation of the 3-agent LLM council pattern (2026-05-26 PR
---

Third confirmed run of the LLM council pattern (after v0.9.0 release validation and PR #70 cleanup). Dispatched against PR #84 (housekeeping + #82 cp1252) and PR #89 (#87 cross-profile + #88 auto-migration).

**Verdict pattern held:**
- Completeness reviewer: GREEN
- Cross-reference reviewer: GREEN
- Drift reviewer: YELLOW — and its 2 must-fix items were both real:
  1. `data media` disambiguation hint missing `kind` annotation → image/video collision under same `flow_media_id` would be invisible. Fixed in commit `b32d595`.
  2. `docs/LIVE_VERIFICATION_v0.9.0.md:69` references `test_data_list_db_missing_exits_16` — renamed/inverted by #88. Added a post-v0.9.0 superseded note.

**Why:** the drift reviewer's "hidden caller / untested edge case / memory contradiction" framing catches the gaps the other two miss. After 3 validations, treat drift YELLOW as a soft block; apply must-fix items before merge.

**How to apply:** before merging any PR that touches a user-visible contract (CLI behavior, exit codes, error messages), dispatch the 3-agent council. Completeness + cross-reference can run quickly; allocate the most depth to drift. Pattern lives at `[[llm-council-audit-protocol]]` and `[[llm-council-doc-review-v0.9.0]]`.

Related:
- [[llm-council-audit-protocol]] — original 3-agent protocol
- [[llm-council-doc-review-v0.9.0]] — second validation (v0.9.0 docs)
- [[data-layer-overview]] — data layer this PR set polished
- [[pr-hygiene-revert-and-multi-commit]] — multi-commit + --merge worked cleanly here
