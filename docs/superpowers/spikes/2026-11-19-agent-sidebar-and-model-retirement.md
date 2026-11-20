# Spike: the "agent-only composer" is the agent sidebar, and model retirement is wire data

**Date:** 2026-11-19 · **Host served:** flow.google.com · **Locale:** en-GB · **Cost:** $0
(navigation, clicks on layout controls, DOM and network reads; nothing submitted)

Probe: [`scripts/dev/spike_agent_sidebar_and_model_retirement.py`](../../../scripts/dev/spike_agent_sidebar_and_model_retirement.py),
default mode for the model catalog, `--cycle` for the sidebar. One profile, one project.

## Q1 — is "agent-only" (#799) the agent sidebar?

gflow returns `agent_only` from `ensure_editor` when the settings trigger is hidden and no
`button.agent-mode-chip` exists (`_mc_editor_gate.py` `_is_agent_only_composer`), and
`_exit_agent_mode` gives up when it cannot see a pressed chip, before it reaches its own
panel-close code.

### Observation (`--cycle`, starting from a classic page)

| Step | Agent chip | Settings trigger | Agent panel | Prompt boxes | Composer ligatures | `ensure_editor` |
|---|---|---|---|---|---|---|
| C0 loaded | present, `aria-pressed=false` | visible | — | classic | `add`, `crop_16_9`, `arrow_forward` | |
| C1 chip clicked | present, pressed | `hidden` | — | agent + classic | `expand_content`, `add`, `article_spark`, `tune`, `arrow_forward` | |
| C2 `expand_content` clicked | **absent** | `hidden` | **visible** | agent only | `add`, `article_spark`, `tune`, `arrow_forward` | |
| C3 reload | absent | `hidden` | visible | agent only | same as C2 | **`agent_only`** |
| C4 panel close (`flow-agent-panel button:has(mat-icon:text-is('close'))`) | present, pressed | `hidden` | — | agent + classic | same as C1 | |
| C5 chip clicked | present, `aria-pressed=false` | visible | — | classic | same as C0 | |
| C6 reload | present, not pressed | visible | — | classic | same as C0 | `classic` |

### Reading

- The `agent_only` verdict was reached on an account whose classic composer is two clicks
  away: close the agent panel, then turn the Agent chip off. With the panel expanded, the
  chip is not in the DOM, which is the condition gflow reads as "no classic arm".
- Flow keeps the expanded panel across reloads (C3), and keeps the restored classic state too
  (C6). Both are account state, so the recovery has to be done by gflow, not assumed.
- `expand_content` is the control that opens the panel; it exists only while agent mode is on
  (C1, C4). The panel's own close button is a `mat-icon` `close` inside `flow-agent-panel`.
- This explains the 2026-11-19 observation in #1354's verification: one click on the Agent
  chip, then the panel, left the account reporting `agent_only` on every load until a person
  closed the panel in a browser.

### Not measured

- Whether every account reported as agent-only since #799 (2026-09) is in this panel state,
  or whether a cohort without any classic arm also exists. The #799 report predates the
  panel; its DOM was not captured in this form. A recovery has to fall back to the agent-only
  driver when closing the panel does not reveal a chip.
- Locales other than en-GB (the anchors used are structural: `flow-agent-panel`,
  `mat-icon` ligatures, `aria-pressed`).

## Q2 — where does "Leaving 5 Dec" come from?

### Observation

- The video model menu items are `[role=menuitem]` with a `span.label` (the model name) and,
  on three of them, a `span.caption` ("Leaving 5 Dec").
- The project load's `HTrJv` reply (the model catalog, `docs/FLOW_WIRE_RPCS.md`, "used: no")
  lists model families as `[display_name, usages[], enabled, family_key, alias, …]`. Three
  families end with a date tuple at index 6:

| Family (display name) | Family key | Index 6 |
|---|---|---|
| Omni 1.2 Flash | `macaron_cookie` | — |
| Omni 1.1 Flash | `abra` | `[2026, 12, 5]` |
| Veo 3.1 - Lite | `veo_3_1_lite` | — |
| Veo 3.1 - Fast | `veo_3_1_fast` | `[2026, 12, 5]` |
| Veo 3.1 - Quality | `veo_3_1_quality` | `[2026, 12, 5]` |
| Veo 3.1 - Lite [Lower Priority], upsamplers, image models, Gemini TTS | various | — |

### Reading

The retirement date is structured wire data, present on exactly the three families the menu
labels as leaving and on no other. It can be read from `HTrJv` without depending on the menu's
translated caption.

### Not measured

- Whether the slot carries anything other than a retirement date on other families or days.
- The labs `modelConfig` JSON (`api/video_extend.py` `extract_video_models`) was not checked
  for an equivalent field.
