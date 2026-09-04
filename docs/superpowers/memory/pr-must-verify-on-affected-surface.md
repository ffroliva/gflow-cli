---
name: pr-must-verify-on-affected-surface
description: "A PR fixing a class of bug must be live-verified on the SPECIFIC surface that bug affects, not adjacent ones; PR"
---

**Rule:** when a PR claims to fix a class of bug (e.g. "locale-agnostic selectors"), the live-verification step must exercise the SPECIFIC code path that was changed, not an adjacent one.

**Why:** PR #70 (issue #24 Phase 2) shipped a structural-first cascade for `_attach_frame` and was live-verified with a T2V run on `GFLOW_CLI_LOCALE=de-DE`. The verification passed — but T2V doesn't call `_attach_frame` (only I2V/R2V do). The structural selector `SWAP_CONTAINER = "div:has(> button:has(i.google-symbols:text-is('swap_horiz')))"` was actually broken (icon class is `material-icons` not `google-symbols`; slots are `<div type="button">` not `<button>`). Production I2V silently fell through to the EN text-tier on every non-EN profile and hung with a 30-40 s click-no-event signature.

Discovered 2026-05-26 via DOM probe + live I2V e2e — fixed in PR #90 (issue #63 closure).

**How to apply:**

1. When reviewing a PR that adds a selector cascade or modifies a `_attach_*` helper, ASK: which user-facing command exercises this code path? Then require the LIVE_VERIFICATION evidence to come from THAT command, not a sibling.
2. For locale work specifically: T2V verification ≠ I2V verification ≠ R2V verification. Each exercises a different transport path:
   - T2V: `_switch_to_video_mode` + prompt + Generate (no `_attach_*`)
   - I2V: T2V path + `_attach_frame` (Start, optionally End)
   - R2V: T2V path + `_attach_references` (1-N refs)
3. When adding a structural selector cascade, add a DOM probe script under `scripts/dev/capture_*_dom.py` that exercises the same precondition sequence the production code uses. Run it on the affected profile BEFORE the live e2e — cheap evidence that the selector matches.
4. The probe pattern: `FlowApiClient` → enter editor → switch mode → switch sub-mode (if any) → close settings panel → `page.locator(NEW_SELECTOR).count()` should equal expected. See [[playwright-click-no-downstream-event-signature]] for the symptom heuristic when this rule is violated.

**Related:**
- [[flow-locale-leak-icon-ligatures]] — durable selector pattern; the icon-class assumption that bit PR #70
- [[rest-transports-drop-ui-fields]] — sibling rule for transport pinning in e2e
- [[verification-ledger-5-layer]] — broader credit-spending evidence rule
- [[pr-70-issue-24-phase2-shipped]] — the PR that this lesson came from
