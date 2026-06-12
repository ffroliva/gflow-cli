# Scenario: Issue #170 — locale-free picker include selectors

> `/gflow:scenario` artifact, 2026-06-11. Feeds the PLAN.md task for fixing
> [#170](https://github.com/ffroliva/gflow-cli/issues/170): `PICKER_INCLUDE_BUTTON`
> and `PICKER_CONTEXT_INCLUDE` (`src/gflow_cli/api/transports/ui_automation_video.py:296-302`)
> hardcode pt-BR "Incluir no comando", breaking `--reference-entity` (image t2i),
> movie R2V entity attach, and Vozes voice attach on every non-Portuguese account.
> Fix shape: icon-first selector (context-menu `add` ligature scoped to the open
> menu) with a multi-locale text fallback tier, per the `UPLOAD_MEDIA_BUTTON`
> tiering doctrine (lines 270-275).

## Coverage map

**Active dimensions**

- **D3 Selector cascade drift** — the core of the change; every tier interaction is a scenario.
- **D5 Concurrency & Page pool** — the failure path raises with the picker dialog / context menu still open; pool contamination risk.
- **D7 Error propagation & exit codes** — error message currently embeds the pt-BR literal; `RuntimeError` is untyped.
- **D11 Input validation** — adjacent pre-existing selector-injection trap in the voice tile locator.
- **D12 Observability** — selector-tier telemetry is the early-warning system for the next drift.
- **D10 Headless vs headed** — only for the live verification matrix (account language, not platform, drives rendering).

**Skipped dimensions**

- D1 auth, D2 WAF/reCAPTCHA — attach is a UI-only, credit-free, token-free interaction; no auth or token-mint surface changes.
- D4 batch/resume — multi-prompt + `--reference-entity` is already rejected at `cli_image.py:674`; movie per-scene failure is covered via D5/D7.
- D6 data layer, D9 transport — no schema, recorder, or wire-shape change.
- D8 cross-platform — no new paths; debug screenshot reuses the existing `_capture_debug_screenshot` helper.

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D3 | **pt-BR regression**: new icon-first selector breaks the currently-working pt-BR path (menu item is a `button` not `[role='menuitem']`, or the Radix menu portal isn't under `[role='menu']`) | Critical | pt-BR attach keeps working: pt text retained in the fallback tier; live route-abort verification on denon82 before merge | Unit + E2E live |
| 2 | D3+D7 | **Silent wrong-element click on the image path**: icon tier matches a *different* `add` element, click lands elsewhere, entity never staged. Image t2i has **no submit backstop** — only a structlog summary (`ui_automation.py:1688`) — so the run degrades to a text-only generation reported as success | Critical | Scope icon selector to the open menu (`[role='menu'] [role='menuitem']:has(i.google-symbols:text-is('add'))`) AND add an image-side backstop mirroring `_assert_entities_attached`: raise `WireFormatError` when the captured submit lacks `referenceEntities` for the requested ids | Integration + E2E live |
| 3 | D3 | **Both tiers miss**: account locale outside pt/ru/en *and* Google renames the `add` ligature | High | Locale-neutral typed error with debug screenshot + remediation hint ("set Flow account language to a supported locale; attach the screenshot to a new issue"); never the pt literal in the message | Unit + Integration |
| 4 | D3 | **Vozes recon gap**: `PICKER_INCLUDE_BUTTON` (line 1293) may carry no ligature icon; voice attach still pt-only after the fix | High | Recon the Vozes include button on ru/en (`scripts/dev/dump_character_selectors.js`); until icon verified, multi-locale text list is the conservative primary; failure raises the same locale-neutral error (today it's a generic click timeout) | Live recon + Integration |
| 5 | D5 | **Pool contamination on failure**: attach raises with the picker dialog or context menu still open; the Page returns to the pool and the next checkout finds the composer blocked by a modal | High | Failure path closes the dialog/menu (Escape + dialog-state check) before raising, or the page is disposed instead of returned | Integration |
| 6 | D7 | **Untyped RuntimeError + pt-embedded message**: current raise at `ui_automation_video.py:1259-1263` embeds 'Incluir no comando' and maps to the generic exit code | High | Message becomes locale-neutral ("context-menu include action did not appear"); prefer a typed error carrying an RFC 9457 remediation hint; exit-code mapping asserted in unit tests | Unit |
| 7 | D7 | **Test-suite literal drift**: `tests/api/transports/test_ui_automation_video.py:1057` and `:1080` assert the pt-BR literal (`pytest.raises(match="Incluir no comando")`) | High | Tests updated to assert the locale-neutral phrase + selector-tier composition; add a non-pt (ru) picker DOM fixture | Unit |
| 8 | D12 | **No drift telemetry**: when the icon tier silently stops matching and the text tier carries the load, nobody notices until the text tier also breaks | Medium | Emit `ui_automation_video.include_selector_tier` (tier=icon\|text) on every successful attach; key documented in `docs/ARCHITECTURE.md` | Unit |
| 9 | D3 | **BDD stub drift**: `tests/features/` fakes mirror runtime signatures; the new selector constants/kwargs change breaks `_fake_*` stubs with TypeError hidden by CI structlog | Medium | Update BDD stubs in the same commit; run the feature files locally before push | BDD |
| 10 | D3 | **User-named collision in text tier**: a character display name equal to a menu caption (e.g. "Add to prompt") matches the text fallback outside the menu | Low | Text-tier selectors also scoped to `[role='menu']` / dialog container, so tile text can't collide | Unit |
| 11 | D11 | **Selector injection (pre-existing, adjacent)**: `voice_id` interpolated into `button:has-text('{voice_id}')` (`ui_automation_video.py:1288-1290`) breaks on quotes in the id | Low | Log as a separate issue; do not fix in this PR (keep the diff reviewable) | — |
| 12 | D10 | **Live verification matrix**: ru locale cannot be verified locally (account language wins over `?hl=en`); pt verified on denon82 | Medium | Credit-free route-abort run on denon82 (pt) + candidate build to the issue reporter (ru, offered); headed real-Chrome profile per repo doctrine | E2E live |

Severity: **Critical** (data loss / billed twice / unrecoverable) · **High** (feature broken, workaround exists) · **Medium** (degraded UX, explicit error) · **Low** (cosmetic or edge-only)

## Must-cover before merge (Critical + High)

1. pt-BR keeps working: pt text in fallback tier + credit-free route-abort live run on denon82 (#1).
2. Icon selector scoped to the open context menu; image-side `referenceEntities` backstop added so a missed attach raises instead of silently generating text-only (#2).
3. Both-tiers-miss path raises a locale-neutral, screenshot-carrying, remediation-hinted error (#3, #6).
4. Vozes include button recon completed on a non-pt locale before deciding its tier order (#4).
5. Failure path leaves no open dialog/menu on a pooled Page (#5).
6. pt-literal test assertions replaced; ru-locale picker fixture added (#7).

## Deferred (Medium + Low — log as issues, not blockers)

1. Selector-tier telemetry event (#8) — small, ideally in-PR, but not a merge blocker.
2. Voice-id selector injection (#11) — file as a separate issue.
3. Text-tier scoping for user-named collisions (#10) — falls out of #2's scoping for free; assert in unit test.

## Suggested BDD scenarios (for `tests/features/`)

```gherkin
Feature: Locale-free character entity attach (issue #170)

  Scenario: Entity attach succeeds on a Russian-locale picker via the icon tier
    Given a Flow resource picker rendered in the "ru" locale
    And a character entity tile with id "ent-123" on the Personagens tab
    When I run image t2i with --reference-entity "ent-123"
    Then the context-menu include item is matched by its "add" ligature icon
    And the structlog event "character_entity_attached" fires for "ent-123"
    And the captured submit payload contains referenceEntities ["ent-123"]

  Scenario: Entity attach still succeeds on a pt-BR picker (regression guard)
    Given a Flow resource picker rendered in the "pt-BR" locale
    When I run image t2i with --reference-entity "ent-123"
    Then the attach succeeds via icon tier or pt text fallback
    And the structlog event reports which selector tier matched

  Scenario: Both selector tiers miss — locale-neutral failure
    Given a Flow resource picker whose context menu carries neither the "add"
      ligature nor any known include caption
    When I run image t2i with --reference-entity "ent-123"
    Then a typed error is raised whose message does not contain "Incluir no comando"
    And the message includes a debug screenshot path and a remediation hint
    And the picker dialog is closed before the page returns to the pool

  Scenario: Attach click lands but the entity never rides the wire
    Given the include click is intercepted so referenceEntities is never staged
    When I run image t2i with --reference-entity "ent-123"
    Then a WireFormatError is raised before the run is reported as success
```

## Known-issues cross-reference

- **`KNOWN_ISSUES.md:349` — "UiAutomationTransport selectors locale-agnostic — issue #24 Phase 5 complete"**: #170 disproves this claim for the two picker constants. Update the entry in the same PR (the claim becomes true again once merged).
- **`docs/superpowers/character-scenario.md:35`** predicted this exact gap as D3/High ("never localized text; test in a non-EN locale fixture") — the fixture was never added. This PR closes that loop; note it in the CHANGELOG entry.
- **`_ONBOARDING_TEXT_SELECTORS` precedent** (KNOWN_ISSUES.md:374): 14-locale text fallback behind structural-first tiers — the established template for the fallback tier here.
