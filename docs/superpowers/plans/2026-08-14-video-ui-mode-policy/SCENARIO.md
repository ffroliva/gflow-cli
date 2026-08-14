# Scenario: #299 PR-A — video joins the UI-mode policy

> Predict verdict: **GO** (2026-08-14, 5-persona; confidence 8/10). Conditions folded
> into PLAN.md tasks. Parent reconciliation: the 2026-08-06-ui-mode-policy plan's
> image-side work shipped in v0.34.0–v0.38.1; this plan is its unfinished video leg.

## Behavior deltas under test

| # | Scenario (Given / When / Then) | Sev | Test layer |
|---|---|---|---|
| S1 | Classic cohort, `video t2v` no flag → binds classic via `get_ui_driver`, generation proceeds unchanged | high | unit (fake page) + live $0 |
| S2 | Agentic cohort lands mid-session, no flag → classic recovery attempted by `get_ui_driver(CLASSIC)`; unrecoverable → **exit 28 pre-submit, $0 spent** (today: 30–40 s of doomed locators → mid-flow exit 23/25) | high | unit + live if cohort serves agentic |
| S3 | `--ui-mode classic` explicit → identical to S1/S2 path | med | unit |
| S4 | `--ui-mode agentic` explicit on `video t2v`/`i2v` → **Click `UsageError` (exit 2) before any browser launch** — no agentic video driver exists; exit 28's "retry may land it" remediation would lie | high | unit (CliRunner) |
| S5 | `GFLOW_CLI_UI_MODE=agentic` in env (set for image workflows) + any video command → structlog **warning + classic-required**, run proceeds; only the explicit flag errors | high | unit |
| S6 | `auto` (default) on video → resolves to classic-required at the call site (`auto ≡ classic` for video until an agentic video driver exists — documented) | med | unit |
| S7 | MCP `gflow_generate_video(ui_mode="classic")` → param accepted, **worker codec round-trips it** to the transport; `ui_mode="agentic"` → tool-layer validation error envelope | high | unit: schema + codec round-trip + parity |
| S8 | `gflow run` manifest with video legs → **no `--ui-mode` flag** (image-batch precedent, `run` is `_MCP_EXEMPT`); env-only resolve applies per S5/S6 | med | unit |
| S9 | Exit-28 message on video names the cohort as server-assigned and possibly pinned ("wait before retrying"); nothing auto-retries 28 | med | unit (message pin) |
| S10 | `factory.py` has **zero diff**; structlog event names unchanged (live-verification ledger hooks) | high | review gate |

## Must-cover before merge
- Bind happens **after** `_enter_editor` + overlay dismissal (probe needs a mounted editor); driver first-use sites all sit after the new bind point.
- `infer_required_ui_mode` is NOT consulted for video (`-i` instructions are an image/agentic surface); the video clamp is `auto→CLASSIC`, env-`agentic`→warn+CLASSIC.
- i2v exercises the same bind path as t2v (shared `_generate_video_locked`).
- CHANGELOG records the behavior change (mid-flow 23/25 → pre-submit 28).
