# The migrated composer has an agent arm, and it `hidden`s the settings trigger

**Date:** 2026-09-08 · **Issue:** #749 · **Cost:** $0 (navigation, DOM reads, one toggle click)
**Probe:** [`scripts/dev/spike_migrated_composer_arms.py`](../../../scripts/dev/spike_migrated_composer_arms.py)
**Captures:** `scripts/dev/_spike_out/spike_migrated_composer_arms_20260908_1312*.json` (gitignored)

## Question

A reporter on `flow.google.com` finds `READY_ANCHOR = ".settings-trigger-button"` present in
the DOM but carrying a bare `hidden` attribute, so `migrated_composer.py:466`'s
`wait_for(state="visible")` can never pass and both `gflow video t2v` and `gflow image t2i`
die at 30 s. Their DOM shows a different settings control in an `agent-footer-actions`
component. Is that arm reachable from our accounts — and what puts the `hidden` there?

## What was observed

Reproduced on **two** accounts (`ffroliva`, `denon82`), same result on both.

Flow's composer carries an **agent-mode chip** — `button[aria-pressed].agent-mode-chip`.
Toggling it swaps the whole prompt box:

| | chip `aria-pressed=false` (default) | chip `aria-pressed=true` |
|---|---|---|
| `.settings-trigger-button` | `hidden`=**false**, `display: flex`, 147×32, hit-testable | `hidden`=**true**, `display: none`, 0×0, not hit-testable |
| component chain | `flow-base-prompt-box` → `flow-prompt-box` | `flow-base-prompt-box` → **`flow-creative-agent-prompt-box`** → `flow-prompt-box` |
| `div.agent-footer-actions` | absent | present |
| `button.agent-action-button` + `mat-icon` `tune` | absent | present, hit-testable |
| `agent-*` elements | 2 | 5 |

**That is the reporter's DOM, exactly**, produced on demand by one click. The `hidden`
attribute is not drift and not a cohort: it is Flow's agent mode, and gflow's migrated
driver has no idea the mode exists.

`migrated_composer.py` contains no arm handling at all — grep it for `mode_control`,
`agent` or `sidebar` and you get nothing across 1568 lines. The module that *does* handle
the agentic↔classic split, `mode_control.py`, refuses this host by construction
(`raise_if_migrated`, `mode_control.py:146`).

**`mode_control`'s selector would not have worked anyway.** Its
`AGENT_TOGGLE_SELECTOR = "button[aria-pressed]:has(span.content)"` (`mode_control.py:45`)
does not match this chip: measured `has_span_content: false`; the migrated chip's label is
`span.agent-mode-chip-label`. Same concept, different toolkit — the labs/migrated split
this project keeps re-learning.

## Consequence in shipped code

An account left in agent mode fails `ensure_editor` as `UiSelectorDriftError` **exit 23**,
whose message tells the user to file a frontend-drift bug. It is not drift and the user can
do nothing with that advice. Same misclassification shape as #493 (a recoverable mode state
reported as exit 23) and #721 (a credit shortfall reported as exit 23).

Flow remembers the chip per account, which is how a user gets permanently broken: one click
in the browser, and every later gflow run lands in agent mode.

## What this means for the fix

The reporter proposed retargeting `READY_ANCHOR` at the agent-mode button and reordering
`apply_video_settings` after `send_prompt`. Both are unnecessary and one is harmful:

- Retargeting would break every account that renders the classic arm — the default state on
  both of ours.
- `get_by_role("button", name="Configuración")` anchors on a translated label, which
  AGENTS.md forbids and this file's own docstring already says is never used on this host.
- Their "the composer must be touched first" observation is about the *agent* prompt box.
  Leaving agent mode makes the whole call-ordering question moot — no structural change.

The fix is to **detect the chip and toggle it off** before waiting for the trigger.

## What was NOT measured

- **Whether the reporter's account can leave agent mode.** Ours toggle freely in both
  directions. If Flow pins the mode for some cohort, toggling recovers nothing and the
  second arm becomes real work. The fix must therefore report *what it observed* when the
  trigger stays hidden after a toggle attempt — not retry blindly.
- **Whether `gflow image t2i` shares the path.** The reporter says both commands fail; only
  the video path was read here.
- **What the second `agent-action-button` (ligature `article_spark`, `aria-pressed=false`)
  does.** Recorded because it makes a bare `button[aria-pressed]` ambiguous in agent mode —
  the toggle must be anchored on `.agent-mode-chip`.
- **labs.google.** Every account on this machine is migrated, so the labs side of this
  comparison is unavailable — as in the 2026-09-07 spike.

## Probe note

The first version of this probe classified the arm by "does any `agent-*` element exist".
That is wrong and the capture proves it: `button.agent-mode-chip` is present in **both**
states. A signal present on both sides of a transition is not a settle signal. The committed
version classifies on the chip's `aria-pressed` and the trigger's `hidden`, which are the two
things that actually swap.
