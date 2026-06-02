# Scenario: `gflow character` (issue #145)

Pre-implementation edge-case decomposition (12-dimension `/gflow:scenario`). Feeds the PLAN acceptance
criteria and the e2e DoD matrix in [`docs/CHARACTER.md`](../CHARACTER.md) §13. v1 = create + list + show +
video `--character` + data layer. Transport: structural ops REST; **generation is UI-driven** (reCAPTCHA wall).

## Coverage map

| Dim | Active? | Why |
|---|---|---|
| D1 Auth/session | ✅ | tRPC(session) + aisandbox(Bearer) + UI(gen) — three planes, different expiries (we hit 401 then 403 live) |
| D2 WAF/reCAPTCHA | ✅ Critical | generation reCAPTCHA-walled; create fires 1–2 mints (face+body) rapidly; reuse fires another |
| D3 Selector drift | ✅ | char editor + picker selectors; 2 no-ligature controls; locale leak |
| D4 Batch/resume | ⚠️ partial | no batch char-create in v1, but the create saga is multi-step → partial-failure recovery |
| D5 Concurrency/Page pool | ⚠️ low | create is single-flight; face→body sequential; reuse poll frees the Page |
| D6 Data layer | ✅ | migration + OperationKind.CHARACTER + redaction + persist-before-spend |
| D7 Error/exit codes | ✅ | char-not-found, poll-timeout, **403→AuthExpiredError misclassification (live bug)** |
| D8 Cross-platform | ✅ | accented personality/prompt (cp1252 — hit live in tooling), paths |
| D9 Transport edge | ✅ | projectInitialData shape, entityContext-missing → unbound gen, tRPC GET encoding |
| D10 Headless/headed | ✅ | gen MUST be headed (reCAPTCHA); CI/no-display |
| D11 Input validation | ✅ | prompt length, invalid --voice/--model/--character, name collision, empty prompt |
| D12 Observability | ✅ | per-step structlog + entityId on failure; RFC 9457; correlation_id |

## Scenario table (severity-ranked)

| # | Dim | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D2 | Direct-REST gen attempted (no UI) | Critical | NEVER do this — 403 reCAPTCHA wall (proven). Generation only via UI passive-capture. Guardrail test asserts char-gen routes through the UI transport | Integration |
| 2 | D7 | Gen/PATCH returns **HTTP 403** | Critical | Must raise a **WAF/reCAPTCHA** error (e.g. `WafRejectionError`) with remediation, NOT `AuthExpiredError` — current `_raise_for_non_retryable` mislabels 403 as auth-expired (live bug). Fix + test | Unit + Integration |
| 3 | D6/D4 | Saga fails after `createEntity` but before gen (or after gen, before PATCH entity) | Critical | persist-before-spend: entityId + any workflowId recorded; re-run resumes / no orphan double-charge; partial character is recoverable, surfaced with entityId | Integration + BDD |
| 4 | D2 | `createEntity` OK but **credit already spent** then gen 403s on retry | Critical | Do not silently re-generate (double-bill). Surface spent state; the captured workflow is reusable by entityId | Integration |
| 5 | D9 | Generation fires but workflow has **no `parentEntityId`** (entityContext not set — wrong nav context) | Critical | Detect unbound workflow (parentEntityId≠entityId) → error, do not PATCH a foreign workflow (this is exactly the spike-v1 404) | Integration + E2E live |
| 6 | D1 | `denon82` session expired at create start | High | Clear `AuthExpiredError` (exit 8) + hint `gflow auth login --profile … --browser chrome` (reproduced live) | Integration |
| 7 | D1 | Session valid for tRPC (createEntity) but Bearer expired for aisandbox PATCH | High | 401 on PATCH → token refresh retry (existing `_post_json` path); if still failing, AuthExpired with hint | Integration |
| 8 | D3 | Picker **"Incluir no comando"** / slot-add (no ligature, localized text) | High | Use structural anchor (dialog-footer primary button / slot-row position), never localized text; test in a non-EN locale fixture | Integration + BDD |
| 9 | D3 | `accessibility_new` matches both the nav button AND character cards | High | Disambiguate by tag (`button` vs `div[role=button]`); selector test asserts single match | Integration |
| 10 | D3 | Flow ships UI update; char-editor selector matches zero | High | Fail fast with selector-drift error + screenshot (existing pattern), not a silent hang | Integration |
| 11 | D9 | `flow.projectInitialData` tRPC GET input encoding wrong | High | list/show returns empty/400 → handle + correct encoding; test the exact `?input=` shape (the spike's guessed bit) | Integration + E2E live |
| 12 | D11 | `gflow character show --name X` with duplicate names (Flow allows "Untitled Character" ×N) | High | exit 11 + list the colliding entityIds + "disambiguate with --id" | Unit + BDD |
| 13 | D11 | `--voice gibberish` | High | validated against `gflow character voices` set → exit 11 with valid ids (language-agnostic, no localized strings) | Unit |
| 14 | D11 | `--character <bad-id>` on `gflow video` | High | exit 11 character-not-found before any credit spend | Unit + Integration |
| 15 | D6 | `personalityNotes` persisted with `GFLOW_CLI_HISTORY_PROMPTS=redacted` | High | personality hashed/redacted like a prompt; assert no plaintext in DB | Unit |
| 16 | D6 | Signed `fifeUrl` in entity/media refs reaches a DB row | High | redact/strip; assert no `signature=`/`Expires=` in any stored row; store mediaId/workflowId only | Unit |
| 17 | D6 | Migration on a newer DB schema | High | `DataStoreError` exit 16, never silent corruption; EXIT_CODE_MAP ordering test updated for new `OperationKind.CHARACTER` | Unit |
| 18 | D8 | Accented `--personality`/`--face-prompt` on Windows w/o `PYTHONUTF8` | High | round-trips intact (we hit cp1252 live in tooling); document + test UTF-8 | Unit + BDD |
| 19 | D7 | Reuse async poll exceeds timeout | High | exit 9 `TransportTimeoutError`, remediation surfaces **entityId + workflowId** so the paid asset can be re-polled, not re-generated | Integration + BDD |
| 20 | D9 | Reuse response omits expected `workflows[]`/`media[]` key | Medium | `WireFormatError` with redacted discovery payload, not a crash | Integration |
| 21 | D10 | Char-gen invoked headless / CI no-display | Medium | clear error: generation needs headed Chrome-strategy profile ([[real-browser-auth-mandatory]]) | Integration |
| 22 | D2 | Rapid face+body mints inflate WAF heat on one profile | Medium | sequential gens + submission cadence/jitter; do not parallelize on one profile | Integration |
| 23 | D11 | Empty/oversized `--face-prompt` (0 or >4000 chars) | Medium | validate before submit; clear error | Unit |
| 24 | D12 | Each saga step emits a stable structlog event w/ entityId + correlation_id | Medium | events named + documented; RFC 9457 shape on errors | Unit |
| 25 | D5 | Page returned to pool mid-modal (picker open) after reuse attach | Medium | ensure picker dialog dismissed before checkin (state contamination) | Integration |
| 26 | D11 | `--character` repeated >max refs Flow allows | Low | cap + clear error (multi-ref limit unknown — probe) | Unit |
| 27 | D3 | model picker ligature differs char-editor (`arrow_drop_down`) vs normal (`crop_16_9`) | Low | handle both; selector test | Integration |

## Must-cover before merge (Critical + High) → PLAN acceptance criteria

1. **#1/#5** char-gen routes ONLY through UI passive-capture, and the resulting workflow's `parentEntityId == entityId` is asserted before any PATCH (guards the spike-v1 404 + the reCAPTCHA wall).
2. **#2** 403 maps to a WAF/reCAPTCHA error (fix the `AuthExpiredError` misclassification) with remediation.
3. **#3/#4** persist-before-spend + recoverable partial saga; never double-bill on retry; entityId surfaced.
4. **#6/#7** auth-expiry (session + Bearer) handled with the live-verified `--browser chrome` hint.
5. **#8/#9/#10** language-agnostic structural selectors (no localized text), tag-disambiguated, fail-fast on drift.
6. **#11** correct `projectInitialData` GET encoding for list/show.
7. **#12/#13/#14** input validation → exit 11 (collision, bad voice, bad character id) before spend.
8. **#15/#16/#17** redaction (personalityNotes, signed URLs) + migration safety + EXIT_CODE_MAP test.
9. **#18** UTF-8 / accented text round-trip.
10. **#19** reuse poll-timeout → exit 9 with entityId/workflowId (no re-spend).

## Deferred (Medium + Low — log as issues, not blockers)
#20 wire discovery payload, #21 headless guard message polish, #22 WAF cadence tuning, #23 prompt-length bounds, #24 observability doc, #25 page-pool modal hygiene, #26 multi-ref cap probe, #27 dual model-picker ligature.

## Suggested BDD scenarios (`tests/features/character_*.feature`)
```gherkin
Feature: gflow character create
  Scenario: create a character with a face image binds the generation to the entity
    Given an authenticated denon82 session and an existing project
    When I run "gflow character create Ana --project <pid> --face-prompt '...'"
    Then a CHARACTER entity is minted via flow.createEntity
    And the face generation runs in the character editor (UI, not direct REST)
    And the resulting workflow's parentEntityId equals the entityId
    And the entity is saved with displayName "Ana"
    And exit code is 0

  Scenario: generation rejected by reCAPTCHA returns a WAF error, not auth-expired
    Given a generation request that returns HTTP 403
    When the character image generation runs
    Then a WAF/reCAPTCHA error is raised with a remediation hint
    And the error is NOT AuthExpiredError

  Scenario: saga fails after entity mint but before save is recoverable
    Given flow.createEntity succeeded and the face generation succeeded
    When the PATCH flow/entities step fails
    Then the entityId and workflowId are recorded and surfaced
    And re-running does not generate a second paid image

Feature: gflow character show
  Scenario: ambiguous name requires disambiguation
    Given two characters named "Untitled Character" in the project
    When I run "gflow character show --project <pid> --name 'Untitled Character'"
    Then exit code is 11
    And both entityIds are listed with a "disambiguate with --id" hint

Feature: gflow video --character
  Scenario: reuse a character in a video by entityId (multi-reference)
    Given a saved character with entityId E1
    When I run "gflow video '...' --project <pid> --character E1 --character E2"
    Then the request carries referenceEntities [{E1},{E2}]
    And the async generation is polled until complete
  Scenario: poll timeout surfaces ids without re-spending
    Given a submitted reuse generation
    When the status poll exceeds the timeout
    Then exit code is 9
    And the entityId and workflowId are surfaced for re-poll
```

## Known-issues / memory cross-reference
- **403 → AuthExpiredError misclassification** — NEW bug found live (spike v2); `_raise_for_non_retryable` maps 403 to auth-expired. Fix to a WAF/reCAPTCHA error. Cross-ref [[rest-path-capability-matrix]] (gen reCAPTCHA-walled).
- [[flow-locale-leak-icon-ligatures]] — D3 selectors must be ligature/structural (resolves for char editor + picker if no localized text used).
- [[real-browser-auth-mandatory]] — D10 gen needs Chrome-strategy headed profile.
- [[rest-path-capability-matrix]] — D1/D2 the structural-vs-generative split is the design's backbone.
- [[e2e-exposes-synthetic-fixture-bugs]] / [[feature-dod-full-e2e]] — Critical+High must have live e2e, not just mocked.
- [[playwright-context-request-no-page-deadlock]] / [[subagents-ignore-enterworktree-cwd]] — D5 page-pool + dev hygiene.
- [[exit-code-map-ordering-invariant-test-pitfall]] — D6 new OperationKind.CHARACTER must update the ordering test.
