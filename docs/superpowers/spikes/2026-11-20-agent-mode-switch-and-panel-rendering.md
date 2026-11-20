# Spike: Flow's agent toggle is now a switch, and the agent panel has a new rendering

**Date:** 2026-11-20 · **Host served:** flow.google.com · **Locale:** en-GB · **Cost:** $0
(navigation, clicks on layout controls, DOM reads; nothing submitted)

Probe: [`scripts/dev/spike_agent_panel_rendering.py`](../../../scripts/dev/spike_agent_panel_rendering.py),
observe mode twice, then `--cycle` once. One profile, one project. Issues: #1374, #1384.

## Question

#1384 reports exit 23 (`UiSelectorDriftError`) on a page whose ligatures include
`right_panel_close`, `forum`, `spark` and `settings_2`, and neither `close` nor `tune`. The
2026-11-19 spike (`2026-11-19-agent-sidebar-and-model-retirement.md`, PR #1376)
measured the agent panel with `close`, `tune` and `edit_square`. Does the new rendering reproduce
here? What are its anchors, and does it recover the way the old one did?

## Observation

### The Agent toggle (observe mode, classic page)

There is no `button.agent-mode-chip` on the page. The toggle is
`flow-agent-mode-switch > button.switch` with `aria-pressed`. It carries the ligature `spark_off`
when off, and `spark` plus the class `checked` when on. The 2026-11-19 chip lived in
`flow-agent-mode-toggle-chip`.

### The cycle

| Step | Toggle | Settings trigger | Agent panel | Agent prompt box buttons | `ensure_editor` |
|---|---|---|---|---|---|
| C0 loaded | switch, `aria-pressed=false` | visible | — | — (classic box) | |
| C1 switch clicked | switch, `aria-pressed=true` | `hidden` | — | `right_panel_open`, `add`, `settings_2` (`.agent-action-button`), `spark` (the switch), `arrow_forward` | |
| C2 `right_panel_open` clicked | **absent** | `hidden` | visible: header buttons `forum`, `settings_2`, `right_panel_close`; the prompt box sits inside the panel with `add`, `arrow_forward` | (inside the panel) | |
| C3 reload | absent | `hidden` | visible, same as C2 | same as C2 | **raises `UiSelectorDriftError`** |
| C4 `flow-agent-panel button:has(mat-icon:text-is('right_panel_close'))` clicked | switch, `aria-pressed=true` | `hidden` | — | same as C1 | |
| C5 switch clicked | switch, `aria-pressed=false` | visible | — | — (classic box) | |
| C6 reload | switch, not pressed | visible | — | — | `classic` |

Other anchors recorded:

- `settings_2` appears in three places: `flow-tile-view-header` (the library/display menu,
  always present), `flow-creative-agent-prompt-box button.agent-action-button` (agent mode on,
  panel closed) and the `flow-agent-panel` header (panel open).
- No `tune` ligature appears in any state of this rendering.
- The panel's full ligature set at C3 matches the one in #1384's incident bundle, including
  `forum`, `right_panel_close` and `spark`.

## Reading

- #1384 reproduces on this account: the same ligatures, and the same exit-23 verdict from
  `ensure_editor` at C3.
- The page is recoverable in two steps, as on 2026-11-19: close the panel with its header close
  button, then turn the switch off. The restored classic state persists across a reload.
- gflow's gate misses all three anchors it depends on in this build:
  - `AGENT_MODE_CHIP` / `AGENT_MODE_CHIP_ANY` (`button.agent-mode-chip`) do not match the
    switch. Plain agent mode (C1, the #749 case) is therefore unrecoverable too, and not only
    the panel state.
  - The panel close in `_exit_agent_mode` matches only `close`.
  - `AGENT_ONLY_ANCHOR` (`tune` inside `flow-creative-agent-prompt-box`) matches nothing.
    The agent-only driver uses the same constant as its settings button.
- With the old chip gone, "no chip" is no longer evidence of a cohort without a classic arm:
  every page in this build has no chip.

## Not measured

- Whether the old rendering (chip, `close`, `tune`) is still served to some accounts. On
  2026-11-19 this account had it, so both renderings have to be handled.
- Whether an account with no classic arm at all exists in this build. The agent-only driver's
  settings anchor in this build is unverified, because no such account is available here.
- Locales other than en-GB. All anchors used are structural: custom elements, `mat-icon`
  ligatures, `aria-pressed`.
