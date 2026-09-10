# Scenario: attributable click timeouts on the migrated composer (#776)

Feeds from [`PREDICT.md`](PREDICT.md) (STOP on the original proposal → CAUTION on the
revised one) and the spike
[`2026-09-10-migrated-click-blocked.md`](../../spikes/2026-09-10-migrated-click-blocked.md).

## Coverage map

| Dim | Active? | Why |
|---|---|---|
| **D3** Selector drift & locale invariance | **Yes — primary** | The whole change is what a failed click reports. The occluder must be named structurally; a translated label would violate AGENTS.md and be useless to a zh-CN reporter (#776 is one) |
| **D7** Error propagation & exit codes | **Yes — primary** | Bare `TimeoutError`/exit 1 → `UiSelectorDriftError`/exit 23. `retryable` must not move as a side effect |
| **D12** Observability | **Yes** | `detail` now reaches console raw, structlog, and `--json`. New event/field names are a contract |
| **D13** MCP parity | **Yes — the larger half** | The queued path currently hashes the detail away entirely (`daemon.py:441-475` `else`). The fix flips it to the `GFlowError` branch |
| **D10** Headless vs headed | **Yes** | The probe runs `page.evaluate` on a real page; must not break when the page is mid-teardown |
| **D8** Cross-platform | Partial | #776 is a Windows report, but the failure is upstream of any path handling. Only console encoding matters, already covered by `cli.py:76-82` |
| D1 auth · D2 WAF · D4 batch · D5 concurrency · D6 data · D9 transport · D11 input | No | The change is confined to one driver's failure branch. No auth, no wire call, no schema, no new input |

## Scenario table

| # | Dim | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D7/D3 | The settings trigger is visible but the click never lands; the **agent-mode chip is pressed** | **Critical** | `UiSelectorDriftError` (23) naming *agent mode*, not an overlay. This is #752 finding #7's predicted cause | E2E (BDD) |
| 2 | D7/D3 | The click never lands because an **element covers** the trigger | **Critical** | `UiSelectorDriftError` (23) naming the occluder by tag + structural class | E2E (BDD) |
| 3 | D7 | The click never lands and **every probe reads healthy** | **High** | The error says exactly that — visible, enabled, hit-testable — instead of inventing a cause. Rules out three of Playwright's four conditions and points at *stable* | E2E (BDD) |
| 4 | D7 | **A/B control** — an unobstructed trigger | **Critical** | The click lands, nothing is raised, no probe runs. Without this, scenarios 1–3 could pass against a helper that always raises | E2E (BDD) |
| 5 | D3/D12 | The occluding element carries an **account-identifying attribute** (`aria-label` with an email, `src` with a signed URL) | **Critical** | Neither appears anywhere in the message. Security persona's mandated regression test; PR #777 was this exact bug class | E2E (BDD) |
| 6 | D7 | `retryable` after the change | **High** | Still `False` — *preserved, not measured* (Bug Lane "A flag is a claim", middle row) | Unit |
| 7 | D12 | The occluder's class list is pathologically long | Medium | Capped client-side so the queued path's 500-char slice (`data/redaction.py:117`) cannot clip the remediation off | Unit |
| 8 | D13 | The same failure over **MCP** | **Critical** | Reaches the agent as RFC 9457 problem details with `exit_code: 23`, not `sha256:…` | E2E (MCP path) |
| 9 | D10 | The page is closed/navigating when the post-mortem read runs | High | The probe returns "unreadable" and the error still raises, naming the locator. A diagnostic must never replace the failure it is describing | E2E (BDD) |
| 10 | D7 | A click failing for a **non-timeout** reason | Medium | Not converted — only an actionability timeout is reinterpreted | Unit |

## Must-cover before merge (Critical + High)

1, 2, 3, 4, 5, 6, 8, 9 — i.e. every row above except 7 and 10, which are unit-level guards.

## Deferred

- The other 15 bare click sites (`_select`, `_select_model`, the frame picker). Per the
  Devil's Advocate and #759, each waits for its own signature rather than a blanket
  conversion. The helper exists, so adopting one later is a one-line change.
- Whether Flow's announcement modal reaches the migrated host at all — **unmeasured**, and
  the fix is deliberately built not to depend on the answer.

## Suggested BDD scenarios

Browser-only by construction: Playwright's actionability gate (attached → visible → stable
→ receives-events → enabled) is what fails, and a mocked `Page` whose `.click()` is a stub
cannot express it. Per the Bug Lane step 5, that makes these e2e.

```gherkin
@e2e @e2e_auth
Feature: A click that never lands says why

  Scenario: A pressed agent-mode chip is named as the cause
    Given a Flow project page whose settings trigger is covered
    And the agent-mode chip is pressed
    When the driver opens the settings pane
    Then it fails with exit 23
    And the message names Flow's agent mode
    And the message does not blame an overlay

  Scenario: A covering element is named by its structure
    Given a Flow project page whose settings trigger is covered
    When the driver opens the settings pane
    Then it fails with exit 23
    And the message names the covering element by tag and structural class

  Scenario: A healthy-looking failure is reported as unexplained
    Given a Flow project page whose settings trigger accepts no click
    When the driver opens the settings pane
    Then it fails with exit 23
    And the message reports the control as visible, enabled and hit-testable
    And the message does not name a cause it did not observe

  Scenario: An unobstructed trigger still opens the pane
    Given a Flow project page whose settings trigger is clickable
    When the driver opens the settings pane
    Then the pane opens and nothing is raised

  Scenario: An account identifier on the covering element never reaches the message
    Given a Flow project page whose settings trigger is covered
    And the covering element carries an account email and a signed media URL
    When the driver opens the settings pane
    Then it fails with exit 23
    And the message contains neither the account email nor the signed URL

  Scenario: A page that cannot be read still reports the failed locator
    Given a Flow project page whose settings trigger is covered
    And the page stops answering probes
    When the driver opens the settings pane
    Then it fails with exit 23
    And the message names the settings trigger
```

Binding: `tests/e2e/test_click_attribution_bdd.py` via
`scenarios("../features/click_attribution.feature")`. One feature, one module —
`tests/features/test_e2e_binding_guard.py` enforces that offline.

## Known-issues cross-reference

| Entry | Relationship |
|---|---|
| [#752](https://github.com/ffroliva/gflow-cli/issues/752) finding #7 | **Predicted this symptom at this function.** The `wait_for` half was fixed; the click was not. Scenario 1 is that finding's regression test |
| [#749](https://github.com/ffroliva/gflow-cli/issues/749) / KNOWN_ISSUES "agent-mode chip hides the settings trigger" | Same mechanism, one gate later |
| [#593](https://github.com/ffroliva/gflow-cli/issues/593) / KNOWN_ISSUES "changelog modal wedges" | The labs precedent. Scenario 2 covers the shape **without** asserting it occurs on this host |
| [#722](https://github.com/ffroliva/gflow-cli/issues/722) | Blanks the incident bundle on this path — verification must lean on the raised error and the log line |
| [#759](https://github.com/ffroliva/gflow-cli/issues/759) | Comment bloat in this file. One helper, not eighteen message blocks |
| [#770](https://github.com/ffroliva/gflow-cli/issues/770) | Live precedent for a typed "most likely" message being wrong. Scenario 3 is the direct countermeasure |
| [#643](https://github.com/ffroliva/gflow-cli/issues/643) | The reporter's locale error. Measured irrelevant by the spike — 3/3 reproduced it while the click landed |
