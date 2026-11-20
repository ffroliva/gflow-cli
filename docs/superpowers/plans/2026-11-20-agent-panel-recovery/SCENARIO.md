# Scenario: recover the classic composer from Flow's agent mode and agent panel (#1374, #1384)

Evidence: [`2026-11-20-agent-mode-switch-and-panel-rendering.md`](../../spikes/2026-11-20-agent-mode-switch-and-panel-rendering.md).
Root cause, stated at the root rather than at the symptom: the migrated editor gate
(`src/gflow_cli/api/transports/_mc_editor_gate.py` `ensure_editor` / `_exit_agent_mode`) leaves
agent mode only through a pressed `button.agent-mode-chip`. It closes the agent panel only
*after* seeing that chip, and only through the `close` ligature. Flow's 2026-11-20 build replaces
the chip with `flow-agent-mode-switch > button.switch` and closes the panel through
`right_panel_close`. The switch is absent from the DOM while the panel is open. Two reports with
one root:

- #1374: the panel is read as #799's agent-only composer (old build).
- #1384: the panel is read as selector drift (new build).

Both are one scenario.

## Coverage map

| Dimension | Active | Why |
|---|---|---|
| D3 Selector drift and locale invariance | yes | the whole change is anchors; two builds must both match |
| D5 Concurrency and page pool | yes | the toggle is account state shared by every page on a profile |
| D7 Error propagation and exit codes | yes | exit 23 / 36 / success boundaries move; messages name a control that changed |
| D10 Headless vs headed | yes | the scenario exists only in a real browser; e2e is the evidence layer |
| D12 Observability | yes | gflow changes a setting Flow remembers per account; the user must be able to see that |
| D13 MCP parity | yes | MCP reaches the same gate through the shared transport; docstrings make claims about agent-only |
| D1 Auth, D2 WAF, D4 Batch, D6 Data, D8 Paths, D9 Transport wire, D11 Input | no | no auth, token, wire, data, path or input change; the recovery clicks layout controls only and submits nothing |

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D3 | New build, panel open (switch absent, `right_panel_close` in the panel header) | High | gate closes the panel, waits for the pressed switch, turns it off, returns `classic`; the classic state persists across a reload | E2E live (`@e2e @e2e_auth`, $0) + offline fake page |
| 2 | D3 | New build, agent mode on, panel closed (switch pressed, trigger `hidden`) | High | the #749 exit clicks the switch and returns `classic` | E2E live ($0) + offline |
| 3 | D3 | Old build, panel open (`close` ligature, chip absent) | High | same recovery via `close`; returns `classic` | Offline (the old build can no longer be served here) |
| 4 | D3 | Old build, chip pressed, panel closed | High | unchanged #749 behaviour | Offline (existing tests) |
| 5 | D3/D7 | Healthy classic page, either build | Critical | no click, no extra wait: one count on the happy path, as today | Offline + existing e2e |
| 6 | D3/D7 | Panel open, close clicked, but no toggle appears within the wait | High | **falls through to today's agent-only check** (`tune` anchor + settle, `_is_agent_only_composer`), unchanged: old build → `agent_only` as today (#799's account rendered the panel; the 2026-09-15 driver opens `flow-settings-view` inside `flow-agent-panel` itself); new build → no `tune` → drift (fails closed). The gate does not invent a new verdict from an absence | Offline |
| 7 | D3/D7 | Panel open, close click fails (covered or detached) | High | drift (exit 23) naming the panel close, never `agent_only` | Offline |
| 8 | D3/D7 | Toggle probe unreadable (`None`) after the close | Medium | drift; unknown is not absent | Offline |
| 9 | D3 | A `close` button inside the panel's prompt box (an attachment chip) or the `settings_2` in `flow-tile-view-header` | High | never clicked: the close anchor excludes `flow-creative-agent-prompt-box`; no `settings_2` is matched at all | Offline (fake DOM carrying both decoys) |
| 10 | D3 | Old-build agent-only page (`tune` anchor, no chip), with or without the agent panel open | High | `agent_only` (#799) as today. With the panel open, the gate now closes it first, finds no toggle, and falls through to the same check. Unmeasured: the agent-only driver run on a page whose panel gflow just closed. The 2026-09-15 spike shows the driver's settings click opens the panel's settings view itself, so this is expected to hold; no account with no classic arm is available to run it | Offline (existing tests + a closed-panel arm) |
| 11 | D3/D7 | New-build account with no classic arm (unobserved) | Medium | fails closed as drift (exit 23); the agent-only anchor is not widened to `settings_2`. Named blocker: no such account is available | Offline (pins the fail-closed reading) |
| 12 | D3 | Slow mount: trigger and switch render seconds after `domcontentloaded` | High | the existing early slice and re-entry still apply; at most one panel close per gate call | Offline |
| 13 | D5 | Two pages on one profile recover at once; page B's pressed switch disappears before its click | Medium | before raising "blocked", re-read the trigger; if it is visible, return `classic` | Offline |
| 14 | D12 | Agent mode turned off by gflow | Medium | one `warning` event naming that Flow remembers the change for the account; no URL beyond `safe_page_url`, no panel text | Offline (log capture) |
| 15 | D7 | Drift and agent-mode remediation messages | Medium | name the switch and the panel: "close the agent panel, then switch Agent off"; no "chip" wording | Offline |
| 16 | D13 | MCP `gflow_image_t2i` / video tools on a page in states 1 and 2 | High | same recovery through the shared `ensure_editor` (no MCP-side code); docstrings that describe agent-only stay true | E2E live through the MCP door for state 1 ($0) |
| 17 | D3 | Locale other than en-GB | Low | anchors are structural (custom elements, ligatures, `aria-pressed`); unmeasured on a second locale, recorded as such | Not run (deferred) |

## Must-cover before merge (Critical + High)

1, 2, 3, 4, 5, 6, 7, 9, 10, 12, 16.

## Deferred (Medium + Low): issues, not blockers

- 11: an agent-only driver for the new build, once an account with no classic arm is observed (`AGENT_ONLY_ANCHOR` / `agent_only_composer.py` `SETTINGS_BUTTON`).
- 17: a second-locale run.
- A read-only "which composer" verdict in `gflow doctor` plus an MCP twin (#1374's ask). It needs a browser, a project and a lease; it gets its own issue.

## Suggested BDD scenarios

```gherkin
@e2e @e2e_auth
Feature: The editor gate returns to the classic composer from Flow's agent mode

  Scenario: The agent panel is open and Flow remembers it
    Given a project page with agent mode on and the agent panel open
    And the page has been reloaded
    When gflow prepares the editor
    Then the composer is the classic one
    And the classic composer is still served after a reload

  Scenario: Agent mode is on with the panel closed
    Given a project page with agent mode on and the agent panel closed
    When gflow prepares the editor
    Then the composer is the classic one

  Scenario: A CLI video command reaches the submit from the agent panel
    Given a project page with agent mode on and the agent panel open
    When the CLI client path generates a text-to-video clip with the submit aborted
    Then a video submit was attempted
    And the classic composer is served after a reload

  Scenario: The MCP door reaches the submit from the agent panel
    Given a project page with agent mode on and the agent panel open
    When the MCP video tool generates a text-to-video clip with the submit aborted
    Then a video submit was attempted
```

```gherkin
Feature: The editor gate never calls a recoverable page agent-only

  Scenario Outline: The panel is open and the recovery cannot finish
    Given a fake project page in the new build with the agent panel open
    And <failure>
    When gflow prepares the editor
    Then it reports selector drift, not the agent-only composer

    Examples:
      | failure                                   |
      | the switch never appears after the close  |
      | the panel close cannot be clicked         |
      | the switch probe cannot be read           |

  Scenario: An old-build agent-only page with its panel open keeps its verdict
    Given a fake old-build project page with the agent panel open, the tune anchor and no toggle
    When gflow prepares the editor
    Then the panel was closed once
    And the composer is the agent-only one

  Scenario: Decoy buttons are never clicked
    Given a fake project page with the agent panel open
    And a close button inside the panel's prompt box
    And a settings button in the library header
    When gflow prepares the editor
    Then only the panel header's close button was clicked
```

## Known-issues cross-reference

- `KNOWN_ISSUES.md` § "Some accounts get an agent-only composer on flow.google.com": its "no chip means agent-only" and "a healthy composer carries `button.agent-mode-chip`" statements become false on the new build. The fix resolves the misreading; the entry is rewritten.
- `KNOWN_ISSUES.md` § "Expanded chat sidebar left the composer unrecoverable (exit 23)" (#493, labs path): not affected. It is the precedent this fix follows: act only from the stuck state.
