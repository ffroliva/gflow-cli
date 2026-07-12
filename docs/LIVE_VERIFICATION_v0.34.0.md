# Live verification — v0.34.0 (2026-07-12)

The release's one user-facing feature — the `--ui-mode` / `GFLOW_CLI_UI_MODE`
ensure-required-mode gate (#299) — exercised against live Flow on the merged
`develop` tip, profile `denon82`. **Zero credits:** the production
`get_ui_driver(page, ui_mode=…)` gate was driven directly over 4 real editor
loads; generation was **never** called.

Ledger adapted for a control-plane feature (no generated artifact): exit-code /
raise outcome · structlog switch invariants · observed DOM arm transition
(before → after) · a re-runnable probe. `scratchpad/ui_mode_gate_e2e.py`.

## What was confirmed (all four gate behaviors)

| Requested | Live mount | Switch attempt (structlog) | Outcome |
|---|---|---|---|
| `auto` | agentic | (none) | binds `agentic` (whatever renders) |
| `agentic` | classic | `ui_driver.ui_mode.attempt_force_agent` → `ui_automation.agent_mode_forced activated=True` | arm before=classic → **after=agentic**, binds `agentic` |
| `classic` | agentic | `ui_driver.ui_mode.attempt_exit_agent` → `ui_automation_video.exited_agent_mode` | arm before=agentic → **after=classic**, binds `classic` |
| `agentic` | agentic | `ui_automation.agent_mode_already_active` | idempotent no-op, binds `agentic` |
| `classic` | agentic (toggle click **timed out**: `agent_toggle_probe_failed`) | switch did not take | **`UiModeUnavailableError` — exit 28 fail-fast** (`requested=classic`), before any submission |

- **Bidirectional switch proven live** — classic→agentic (`_force_agent_mode`)
  and agentic→classic (`_exit_agent_mode`), each verified by a DOM re-probe.
- **Fail-fast proven live** — when the toggle genuinely could not reach the
  required arm, the gate raised exit 28 *before* submitting, spending no credits
  (the money-shot: the honest terminal state fires on a real unreachable arm).
- **Inference / flag / MCP surfaces** — covered by 1529 automated tests
  (flag→request threading, both CLI guards, worker payload→request); the CLI
  guards (`--ui-mode classic` + `-i`, `--ui-mode` single-prompt-only) were also
  smoke-verified at exit 2.

`denon82` rolled agentic on most loads (the current natively-agentic cohort
behavior), which conveniently exercised both switch directions in one run.

## Behavior-change note (carried from the CHANGELOG)

`GFLOW_CLI_PREFER_CLASSIC` is deprecated → maps to `--ui-mode classic`, and the
old silent agentic fallback is gone: a classic-required run now aborts with exit
28 instead of producing an agentic-cohort result. Verified above (the timed-out
case is exactly that abort).

## Not verified this cycle

- The exit-28 abort's *auto-reload-retry* is intentionally **not implemented**
  (deferred; see the design note) — today the retry is the user re-running the
  command. Nothing to verify.
- Item 3a/3b (agentic driver parity) and item 2 (enforce-via-mutation) are not in
  this release.
