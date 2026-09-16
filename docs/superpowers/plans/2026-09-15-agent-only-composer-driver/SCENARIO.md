# Scenario: drive the agent-only composer (#799)

## Coverage map

Active: D3 selectors/locale · D5 page state · D7 errors · D9 transport/completion ·
D11 input boundaries · D12 observability · D13 MCP.
Skipped: D1 auth / D2 reCAPTCHA (no new auth path; same profile + page as classic) · D4 batch
(no batch video; image batch loops through the same `run_images`) · D6 data (return types
unchanged, recorder untouched) · D8 paths (downloads reuse existing writers) · D10 headless
(same constraint as every UI path).

## Scenario table

| # | Dim | Scenario | Sev | Expected behaviour | Test |
|---|---|---|---|---|---|
| 1 | D9 | A live credit gate from an **earlier** session is on the page before submit | Critical | Not approved. Baseline records it; only a gate absent from baseline is ours | Integration (fixture DOM) |
| 2 | D9 | Agent asks for approval **twice** in one run (prompt raised spend) | Critical | First approved, second not; run fails `FlowAgentUiError` naming the extra gate, $ spent = one | Integration |
| 3 | D11 | Prompt text contains "make 4 videos" while `--count 1` | Critical | Same as #2 at the gate; for images (no gate) produced count ≠ requested → typed failure listing ids | Integration |
| 4 | D9 | Video: poster tile appears ~30 s before `<video>` | High | Not complete until `<video src=/video/<uuid>>`; poster-only at timeout → exit 9 | Integration |
| 5 | D9 | Same uuid rendered twice (grid tile + chat option) | High | One result per uuid | Unit |
| 6 | D9 | Media already in project before submit | High | Excluded by uuid baseline, never returned | Integration |
| 7 | D9 | Turn ends (submit back from `stop`) with no gate and no tile — agent refused / asked a question | High | Fail fast, not at poll timeout: `ContentPolicyError`-style typed error carrying redacted last reply text | Integration |
| 8 | D11 | `--aspect` 4:3 / 3:4 for image; 16:9 / 9:16 for video | High | Directive carries it; resulting tile orientation checked (portrait/landscape/square) | E2E image |
| 9 | D11 | `--count 2..4` image | High | Measured in e2e before enabling; until then refused exit 36 | E2E image |
| 10 | D11 | `--model` differs from Agent-settings default | High | Per plan Q1 | Integration + E2E |
| 11 | D11 | i2i / refs / `--reference-entity` / instructions / i2v / r2v / extend on this composer | High | `FlowHostMigratedError` exit 36, $0, before typing | Unit |
| 12 | D11 | `--duration` other than measured 4 s | Medium | Directive carries it; duration unverified → logged, not asserted | E2E video |
| 13 | D3 | Non-English account (reporter is `ru`) | High | No text selectors anywhere; directive text is English — agent must still accept it. Unmeasured on non-en → record, e2e on en only | Unit (selector lint) |
| 14 | D5 | Page opens with Agent-settings pane or a previous session's chat open | Medium | Close pane via `arrow_back` / start typing in live editor; never Save | Integration |
| 15 | D5 | ProseMirror already holds a half-typed draft | Medium | Cleared before insert | Integration |
| 16 | D9 | Out of credits at the gate | High | Existing `_raise_if_out_of_credits` → `InsufficientCreditsError` 37 | Integration |
| 17 | D7 | Readiness: classic composer healthy | Critical (regression) | Classic path unchanged, one wait, no agent probing | Existing tests + new race test |
| 18 | D7 | Readiness: #749 recoverable agent mode (chip pressed) | Critical (regression) | Still recovered to classic, never routed to agent driver | Existing tests |
| 19 | D12 | Signed URL in logs / errors | High | `Signature`/`Expires` stripped; events `migrated.agent_only.*` | Unit |
| 20 | D13 | MCP `gflow_generate_image` direct and queued on this account | High | Same result shape as CLI; e2e run on the MCP door | E2E image (MCP) |
| 21 | D13 | MCP/docstrings/KNOWN_ISSUES still say "cannot be driven" | Medium | Updated in the same PR | Doc review |

## Must-cover before merge
1–11, 13, 16–20.

## Deferred
12 (duration verification — needs media probe), 14/15 beyond one fixture each, `WuwhI` wire
decoding, i2v/r2v through `Add ingredients`, "Never confirm" setting behaviour.

## Suggested BDD scenarios

Offline — `tests/features/agent_only_composer.feature`, bound from
`tests/features/test_agent_only_composer_steps.py`:

```gherkin
Feature: Agent-only composer driver
  Scenario: A stale credit gate is never approved
    Given the agent-only composer with a live credit gate already in the chat
    When a video is requested
    Then the pre-existing gate is not clicked

  Scenario: A second credit gate stops the run
    Given the agent-only composer that asks for approval twice
    When a video is requested with count 1
    Then exactly one gate is approved
    And the run fails with a FlowAgentUiError naming the second gate

  Scenario: An unported form is refused before typing
    Given the agent-only composer
    When an image-to-image request is made
    Then FlowHostMigratedError is raised
    And nothing was typed into the editor
```

Live — `tests/e2e/agent_only_composer_e2e.feature`, bound from
`tests/e2e/test_agent_only_composer_bdd.py`:

```gherkin
@e2e @e2e_image
Feature: Agent-only composer — image
  Scenario: t2i on an agent-only account returns one downloaded image
    Given a profile whose Flow project serves the agent-only composer
    When `gflow image t2i` runs with aspect 9:16 and count 1
    Then exit code is 0
    And one PNG/JPEG is written whose height exceeds its width

@e2e @e2e_video
Feature: Agent-only composer — video
  Scenario: t2v on an agent-only account returns one MP4
    Given a profile whose Flow project serves the agent-only composer
    When `gflow video t2v` runs with duration 4 and aspect 9:16
    Then exit code is 0
    And the file starts with an ftyp box
```

## Known-issues cross-reference
- KNOWN_ISSUES § "Some accounts get an agent-only composer" — **resolved** for t2i/t2v by this
  plan; stays Mitigated for every other form.
- #792 (portrait blank page) — unrelated DOM, but the agent driver must not park the page
  before failure capture (reuse `_deferred_park_pending` in callers; no change needed).
