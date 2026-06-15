# Scenario: Pluggable UI Driver Strategy for Agentic UI Support

> **Refreshed 2026-06-14** to match the live-capture evidence
> (`docs/AGENT_UI_RECON.md` § "DOM scraping validation"). Supersedes the pre-evidence
> draft: scraping is by **distinct `name=<uuid>` media ids**, not raw `<img>` count;
> settings are **prompt-encoded**; `flag` is excluded from policy detection; the driver
> binds **per generation** (the cohort flaps).

## Coverage map
- **D3 — Selector cascade drift:** classic `crop_*` probe vs. agentic `tune`/Slate
  composer; prompt-encoded settings reduce popover-selector surface.
- **D5 — Concurrency & Page pool:** per-page DOM scraping under concurrent workers;
  cohort flap across batch items.
- **D7 — Error propagation & exit codes:** content-policy (5), scrape timeout (9),
  forced-agent fail-clean (`FlowAgentUiError`, 25), count mismatch.
- **D9 — Transport edge cases:** worker-delegated responses (page HAR = 0 entries) →
  DOM scraping is the only capture path; UUID dedup; redirect-URL download.

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D3 Selector | Classic UI active | Medium | `crop_*` probe → `ClassicFlowUiDriver`; existing selectors; succeeds | BDD / Unit |
| 2 | D3 Selector | Agentic UI active | High | no `crop_*` + agent pill/chat → `AgenticFlowUiDriver` bound | BDD / Unit |
| 3 | D3 Lifecycle | Cohort flaps mid-session | High | Driver re-probed **per generation** (not cached at `setup()`); correct driver each time | Unit |
| 4 | D3 Selector | Prompt-encoded settings | High | count/aspect/duration composed into the prompt (`Generate {n}…: {prompt}`); agent resolves; no `tune` popover needed | BDD / Unit |
| 5 | D9 Transport | Worker-delegated responses | Critical | page-level network capture yields 0 entries; driver uses DOM scraping, never `page.on('response')` | Unit |
| 6 | D9 Transport | Scrape success + UUID dedup | Critical | snapshot existing `name=<uuid>` ids; poll until **`expected_count` distinct new UUIDs**; one asset = multiple `<img>` nodes must NOT over-count | BDD / Unit |
| 7 | D9 Transport | Build download URL | High | full-res URL `…media.getMediaUrlRedirect?name=<uuid>` (no THUMBNAIL); same-origin cookies authorize | Unit |
| 8 | D7 Errors | Produced count ≠ requested | High | agent returns fewer/more distinct UUIDs than asked → typed mismatch, not a silent wrong-set return | BDD / Unit |
| 9 | D7 Errors | Content-policy block | Critical | scan dialog/stream text + `warning`/`error`/`block` symbols → `ContentPolicyError` (5). **`flag` ligature excluded** (normal chat affordance). *Detection selector pending a live block sample — until then fall back to timeout/`FlowAgentUiError`, never a guessed match* | BDD / Unit |
| 10 | D7 Errors | Scrape timeout | High | expected UUID count never reached, no policy signal → `TransportTimeoutError` (9) | BDD / Unit |
| 11 | D5 Concurrency | Flap across batch items | Medium | item N agentic, item N+1 classic → each item re-binds its driver; no cross-contamination of selectors | Unit |

Severity: **Critical** (data loss / billed twice / unrecoverable) · **High** (feature broken, workaround exists) · **Medium** (degraded UX, explicit error) · **Low** (cosmetic or edge-only)

Test category: **Unit** (no I/O) · **Integration** (mocked HTTP/Playwright) · **BDD** (Gherkin feature file) · **E2E smoke** (`@pytest.mark.smoke`) · **E2E live** (`@pytest.mark.live`, opt-in)

## Must-cover before merge (Critical + High)
1. **Driver binding** — correct driver per generation; survives mid-session/batch flap (#2, #3, #11).
2. **DOM-only capture** — no reliance on page network events; worker bypass asserted (#5).
3. **UUID-dedup scraping** — distinct `name=<uuid>` count, immune to multi-node inflation (#6).
4. **Download URL construction** — full-res redirect URL from scraped UUID (#7).
5. **Count mismatch** — typed error when produced ≠ requested (#8).
6. **Content-policy fail-fast** — `ContentPolicyError`, `flag` excluded; honest fallback while the positive sample is outstanding (#9).

## Outstanding evidence (blocks finalising #9)
A deliberate content-policy-refusal capture in a live agentic session is still needed to
learn how a block surfaces (chat message vs. dialog vs. symbol). Until captured, the
fail-fast text/selector stays a TODO and the driver falls back to timeout/`FlowAgentUiError`.

## Suggested BDD scenarios (for `tests/features/video_agent_ui.feature`)

```gherkin
Feature: Agentic UI Strategy and Generation

  Scenario: Generate images via Agentic UI driver with UUID dedup
    Given the page DOM is in the Agentic UI cohort
    And the canvas already shows 12 media items
    When I run "gflow image t2i a red apple --count 3 --aspect 16:9"
    Then the agentic driver is bound for this generation
    And the prompt encodes count 3 and aspect "16:9"
    And the driver waits for 3 distinct new media UUIDs
    And the 9 duplicate <img> nodes resolve to 3 downloaded images
    And the exit code is 0

  Scenario: Agentic generation produces fewer images than requested
    Given the page DOM is in the Agentic UI cohort
    When I run "gflow image t2i a red apple --count 4"
    And the agent produces only 2 distinct media UUIDs
    Then a count-mismatch error is raised
    And no partial set is silently returned

  Scenario: Agentic generation blocked by content policy
    Given the page DOM is in the Agentic UI cohort
    And the prompt triggers a content policy block in the DOM
    When I run "gflow image t2i something prohibited"
    Then the block is detected via warning/error text, not the flag affordance
    And the exit code is 5
    And the output contains "Content policy"

  Scenario: Cohort flaps between batch items
    Given a batch where item 1 renders the Agentic UI and item 2 renders the Classic UI
    When I run the batch
    Then item 1 binds the AgenticFlowUiDriver
    And item 2 binds the ClassicFlowUiDriver
    And neither item leaks selectors into the other
```
