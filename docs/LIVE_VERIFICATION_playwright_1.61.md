# Live Verification — playwright 1.59.0 → 1.61.0

**Date:** 2026-08-05
**Profile:** `denon82`
**Change:** `playwright>=1.59.0,<1.60.0` → `>=1.61.0,<1.62.0`
**PR:** #450

## Why this document exists

`pyproject.toml` states the rule this verification satisfies:

> Raise this deliberately, after live-verifying a generation on the new minor —
> offline tests cannot see a driver-behaviour regression.

The regression being guarded against was observed 2026-08-03 on **1.62.0**: every
`video i2v` run **hung silently right after the frame upload** — browser alive,
no error, no timeout. Offline tests and zero-credit e2e cannot observe that,
because the failure is in driver behaviour at the upload step.

**1.62.0 remains excluded.** It has never been root-caused. This raise steps to
1.61.0 only.

---

## Layer 1 — Offline gates

| gate | result |
|---|---|
| `pytest` (offline suite) | 2923 passed, 3 skipped |
| `ruff check` / `ruff format --check` | clean |
| `pyright src` | 0 errors |
| `pip-audit --all-extras` | no known vulnerabilities |
| CI (14 checks) | all green |

`tests/test_playwright_pin.py` failed on the first attempt — correctly. Raising
the bound also requires `PINNED_PLAYWRIGHT` and `SUPPORTED_PLAYWRIGHT_RANGE` in
`ui_automation_video.py` to move, because those strings are printed in the stall
error that tells a user how to recover. Both updated; guard passes.

## Layer 2 — Zero-credit live gates

| gate | result |
|---|---|
| `-m e2e_auth` | **16 passed** — browser launch, persistent context, cookie state, session verification, transport health checks |
| `tests/e2e/test_daemon_e2e.py` | **1 passed** — MCP over Streamable HTTP against a live spawned daemon |
| `scripts/dev/live_verify_mcp_tasks.py` | **5/5 layers passed** |

## Layer 3 — Live i2i (Imagen credits) — the same upload mechanism

```
pytest -m e2e tests/e2e/test_transports_e2e.py::test_e2e_i2i_local_ref_attach
  1 passed in 96.71s
```

Local reference image uploaded and attached, generation completed, asset
downloaded. **The upload path — the one 1.62.0 wedged — works on 1.61.0.**

## Layer 4 — Live i2v (Veo credits) — the exact failing path

Driven via the CLI so `--duration` could be omitted (see Layer 5):

```
gflow video i2v tmp/c5e3f668-...jpg "slow gentle camera push in, cinematic" --model veo-lite
```

Structlog trace, in order:

| event | value |
|---|---|
| `video_submode_entered` | `sub=frames` |
| `aspect_set` | `portrait` |
| `output_count_set` | `count=1` |
| **`image_uploaded`** | **`target=Start status=200`** |
| **`frame_attached`** | **`slot=Start`** |
| `stage_completed` | `send_prompt elapsed_s=0.75` |
| `prompt_submitted` | via `arrow_forward` |
| **`generate_captured`** | **`status=200`**, `batchAsyncGenerateVideoStartImage`, `startImage=893a001d` parsed |
| `poll_terminal` | `MEDIA_GENERATION_STATUS_FAILED` |

Final outcome: `PUBLIC_ERROR_VIDEO_GENERATION_TIMED_OUT`.

**This is the verification, and it passes.** Every browser-automation step
completed: the frame uploaded (HTTP 200), attached, the prompt submitted, and
Flow's generate call was captured at HTTP 200 with the start image correctly
parsed. Polling then ran to a terminal state and the CLI surfaced a structured
error.

The generation itself failed **server-side inside Flow** (`TIMED_OUT`), which is
Flow capacity, not driver behaviour. That is categorically different from the
1.62.0 symptom:

| | 1.62.0 (2026-08-03) | 1.61.0 (this run) |
|---|---|---|
| after frame upload | **hung silently** | returned `status=200`, continued |
| error surfaced | none | structured, terminal |
| timeout | never fired | polled to terminal normally |
| browser | alive, wedged | drove the full flow |

**Not claimed:** a finished mp4 on disk. Flow's own generation timed out.
Retrying would spend another Veo credit against a server-side condition this
change cannot influence.

## Layer 5 — Unrelated defect found, NOT caused by this change

`test_e2e_i2v_start_end_frame_attach` fails before generation with:

```
UiSelectorDriftError: probe=duration_tab: the 4s duration tab was not found on
the Flow editor; refusing to proceed
```

**A/B verified as pre-existing.** Identical failure, same probe, on
**playwright 1.59.0** (24.33s) and **1.61.0** (28.16s). Also reproduced with
`GFLOW_CLI_E2E_VIDEO_DURATION=8` — the 8s tab is missing too, so the drift is
the whole duration-tab UI, not one value.

Consequence: **`--duration` is currently broken for i2v against live Flow**, on
every playwright version. The guard is doing its job (refusing rather than
silently accepting Flow's default — issue #288), but the selector needs
re-deriving. Screenshot: `tmp/pytest/.../debug_no_duration_tab.png`.

This is tracked separately; it is not a blocker for this bound raise, and it is
why Layer 4 was driven via the CLI without `--duration`.

---

## Verdict

**Safe to raise to `>=1.61.0,<1.62.0`.**

The regression that motivated the bound does not reproduce on 1.61.0: the frame
upload returns 200 and every downstream step executes. 1.62.0 stays excluded
until its hang is root-caused.
