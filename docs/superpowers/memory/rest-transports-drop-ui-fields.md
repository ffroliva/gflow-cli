---
name: rest-transports-drop-ui-fields
description: "REST transports (evaluate_fetch, bearer, sapisidhash) silently drop UI-only request fields like ref_paths. E2E tests for UI-only features must pin ui_automation and assert on a structlog event proving the UI path executed."
---

The REST transport family — `evaluate_fetch`, `bearer`, `sapisidhash` — has no media-dialog code path. When a `GenerateImageRequest` (or similar DTO) carries UI-only fields like `ref_paths` and routes through a REST transport, those fields are silently ignored. Flow generates a text-only image and an assertion on `image.media_name` / `image.fife_url.startswith("https://")` will PASS — a silent false positive.

**Why:** PR #60 / PR #61 incident — svasakorn's first e2e for i2i ref-attach hardcoded `_make_client("evaluate_fetch", profile)` even though its docstring said "UI-automation transport ONLY". His own EN-profile verification passed (text-only image returned, assertion satisfied) but never exercised the locale-agnostic selectors PR #60 was meant to test. We caught it on ffroliva only because the stale REST session token 401'd instead of returning a (still-bogus) image — a different false signal pointing at the same root.

**How to apply:**

1. **Transport pinning** — any e2e for a UI-only feature must explicitly use `_make_client("ui_automation", profile)`. Never inherit a `strategy` parameter that defaults to REST. The `STRATEGIES = ["evaluate_fetch", "bearer", "sapisidhash"]` constant covers the REST trio only; `ui_automation` is the separate, default browser transport.

2. **Assertion strength** — `media_name` and `https://` URL checks are necessary but not sufficient. Add an assertion on a structlog event that ONLY fires when the UI path executed:
   - For ref-attach: `reference_attached` (or `image_uploaded status=200 target=ref<N>`)
   - For **avatar/likeness** attach: `ui_automation.avatar_attached` — `referenceLikenesses`/`likenessId` is another UI-only field the REST trio drops (PR #123); see [[avatar-likeness-wire-field]]
   - For mode-switch: `image_mode_entered`
   - For count: `count_setter_completed final_displayed_count=<N>`
   PR #61 added the `reference_attached` capture pattern — mirror it.

3. **Code review heuristic** — when reviewing an e2e test that touches `ref_paths` or any future UI-only field, grep the test body for the transport string before approving.

See [[verification-ledger-5-layer]] for the broader credit-spending evidence rule (this is layer 4, "structlog invariants"); [[e2e-tests-parameterize]] for the env-var-defaults rule that pairs with this.
