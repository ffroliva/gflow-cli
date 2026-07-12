# Spike evidence — UI-cohort backend config surface (issue #299 gate)

**Date:** 2026-07-12 · **Verdict: GO (partial) — the UI arm IS a readable, persisted
backend setting; enforcement is a captured mutation away.** This overturns the
`AGENT_UI_RECON.md` § Gating conclusion that "there is no client-readable cohort
signal."

## Method

Automatable half of the #299 spike (H1/H3), zero credits — navigation only, no
Generate click. `scratchpad/ui_cohort_spike.py`: launched a headless persistent
Chrome context on `profile_denon82`, cold-loaded the editor
(`/project/f6caf027…`) **3×**, and per load captured (a) the UI arm via the
production `detect_ui_mode` probe, (b) the client gating surface (`__NEXT_DATA__`
pageProps, localStorage, sessionStorage, cookie names), and (c) every network
response, with bounded JSON bodies for config/preference/RPC-looking URLs.

The recon (2026-06-14) inspected localStorage / cookies / `__NEXT_DATA__` pageProp
**keys** and correctly found no arm flag there. It did **not** read the **tRPC
query response bodies** — which is where the arm actually lives.

## Result

- **Arm this session:** `classic` on all 3 headless loads — yet today's earlier
  CLI e2e (also headless, same profile) hit `agentic`. So the cohort **flaps**;
  denon82 is **not** permanently agentic.
- **`GET /api/trpc/videoFx.getUserSettings`** →
  `{"isAgentModeToggled": false, "isChatPanelOpen": false, "tilesDisplayMode":
  "grid", "completedOnboardingIds":["AGENT","MODEL"], …}` — a **per-user persisted
  setting**. `isAgentModeToggled:false` is consistent with the 3 classic loads.
- **`GET /api/trpc/videoFx.getFlowAppConfig`** → `"agentModeDefaultState":
  "agent_off"`, plus `"activeExperimentIds":[106077941, 105798603, …]` and a raft
  of `isXEnabled` feature flags — the app-level default + server experiment list.
- **`GET /api/trpc/general.fetchUserPreferences`** → history/product-improvement
  only (no arm).
- Endpoints seen on cold load are **all GET** (plus a `general.submitBatchLog`
  telemetry POST). No mode-mutation fired — it only fires on the Agent-pill click.

## Interpretation

The effective arm is best modelled as:

```
effective_arm  =  experiment_override(activeExperimentIds)   # server, may force agentic
              ??  user_setting(isAgentModeToggled)           # persisted, client-readable
              ??  app_default(agentModeDefaultState)          # "agent_off" today
```

- **H1 — CONFIRMED.** The intended arm is **readable pre-generation** from
  `getUserSettings.isAgentModeToggled` + `getFlowAppConfig.agentModeDefaultState`
  / `activeExperimentIds`, via an authenticated in-page `fetch` on the editor we
  already load. DOM `detect_ui_mode` stays the ground-truth post-render check;
  the tRPC read gives a *pre-render prediction* + a diagnosable "why".
- **H2 — STRONGLY INDICATED, not yet captured.** `isAgentModeToggled` is a
  persisted tRPC-backed setting, so the Agent pill flips it through a mutation
  (expected `videoFx.setUserSettings` / `updateUserSettings` shape). It does not
  fire on load, so its exact name/payload needs one pill-click capture.
- **H3 — residual.** If an entry in `activeExperimentIds` force-assigns agentic,
  setting `isAgentModeToggled:false` will not override it. Reading still lets us
  **fail fast** before spending; enforcement is then partial, not guaranteed.

## Decision

**GO into `/gflow:predict`** with this evidence. Recommended scope, in reliability
order:

1. **Read-and-fail-fast (no mutation needed, ships on H1 alone):** the `--ui-mode`
   policy (#299 item 1) gains a pre-submission tRPC read of `isAgentModeToggled` /
   `agentModeDefaultState`; a strict `classic` request aborts cleanly (typed
   error, zero credits) when the predicted/probed arm is agentic. Deterministic,
   low-risk.
2. **Enforce (needs the H2 capture):** replace the best-effort pill-click in
   `get_ui_driver(prefer_classic=True)` with the captured `setUserSettings`
   mutation to persist `isAgentModeToggled:false`, then re-probe. Predict-gated
   (transport change).
3. **Driver parity (H3 fallback, from #299 item 2):** wire-response attribution +
   content-type retry — the fix when an experiment pins agentic and enforcement
   can't win.

## Follow-up needed (manual, blocked on a human at the keyboard)

`gflow-agent-browser-spike/scripts/capture-agent-ui.ps1` already automates the
capture around a pill click + one generation. Run it on a **pill-rendering
cohort** (try `promo-denon82` / `test_acc`; toggle the Agent pill) to record the
mutation endpoint + payload (H2) and, ideally, an `agentic` load to confirm the
arm correlates with `isAgentModeToggled:true`. That closes H2/H3 before predict
finalizes item 2.
