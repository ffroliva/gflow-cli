# Spike — in-project image UUIDs as video `i2v` Start/End frames on `flow.google.com` (#1542)

**Date:** 2026-11-28 · **Profile:** `<profile>` (Flow served `flow.google.com`, locale `en`) · **Cost:** **0 Veo credits**, 0 uploads (`maseQ` count = 0), every submit aborted in-browser via Playwright `page.route("**/batchexecute*", ...)` · **Probe:** [`scripts/dev/spike_1542_migrated_i2v_uuid_frames.py`](../../../scripts/dev/spike_1542_migrated_i2v_uuid_frames.py) (pre-registered readings in module docstring).

## Question

On `flow.google.com`, `gflow video i2v` (CLI and MCP `gflow_generate_video`) currently refuses any `start_image_ref_id` or `end_image_ref_id` with exit 36 (`FlowHostMigratedError: a frame given by Flow media UUID is not ported yet (#639)`).

Can `flow.google.com`'s Frames picker bind an existing in-project image UUID onto the Start and End chips with **zero upload** — matching picker options by the project grid tile's `/asb/<token>` identity (mirroring `#913`/`#995` on `image i2i`) — and does the outgoing `batchexecute` submit carry the exact media UUID(s) on `eb1hJf` (Start-only) and `nprQif` (Start+End)?

## Baseline reproduction (clean `develop`, $0)

| Surface | Invocation | Observed outcome |
|---|---|---|
| CLI | `gflow video i2v --project <project-uuid> --max-credits 0 <start-uuid> "slow camera push" --json` | **exit 36**, `FlowHostMigratedError`, `charged: "no"`, `retryable: false`, detail: `... a frame given by Flow media UUID is not ported yet (#639)` |
| MCP (`gflow_generate_video`) | `mode="i2v"`, `initial_frame="<start-uuid>"`, `project="<project-uuid>"`, `max_credits=0`, `wait=True` | `status: "failed"`, `error.type: "https://gflow-cli.dev/errors/flow-host-migrated"`, `exit_code: 36`, `charged: "no"`, `retryable: false` (same `_unported_form()` guard via `FlowWorker`) |

## Observed (live `$0` DOM + wire probe)

Target project held **44** rendered grid tiles (`img[data-media-id]`), including Start (`<start-uuid>`, 24-char caption), End (`<end-uuid>`, 33-char caption), and a truncated-ellipsis asset (`<ellipsis-uuid>`, 33-char caption ending in `…`).

| Arm | Measurement | Result |
|---|---|---|
| **Q0 — Catalog rows** | Local `gflow.db` rows for Start, End, and ellipsis UUIDs | All 3 found, `flow_project_id == <project-uuid>`, non-empty `display_name` (ellipsis asset ends in U+2026 `…`) |
| **Q1 — Grid tokens (`_GRID_TOKEN_JS`)** | `img[data-media-id="<uuid>"]` → `/asb/<token>` across 44 grid tiles | Start, End, and ellipsis UUIDs all returned non-empty, **distinct** `/asb/<token>` values; negative control (random UUID4) returned `""` |
| **Q2 — Frame picker options** | Open Start chip (`EMPTY_CHIP.first`), read `button.asset-item[role='option']` unfiltered | **15/15** options carried `<img src="/asb/<token>">`; Start token matched at index `0`, End token matched at index `2` |
| **Q3 — Truncated `…` caption** | Compare raw 33-char query (with trailing `…`) vs stripped 32-char query (trailing `…` removed) | Both returned `found=true`, `index=0`, `offered=1`. Stripping trailing `…`/`.` stays exact (`offered=1`) while avoiding literal-ellipsis mismatches when the server indexes untruncated text |
| **Q4 — Angular search-input reset** | After typing a non-matching query (`before_offered=0`), test 3 clear methods | All 3 restored the full unfiltered list (`after_offered=15`, `found_target_token=true`, `target_index=0`, `input_value_after=""`, `uptedb_after_clear=0`): **`search.fill("")`**, **`Control+A` + `Backspace`**, and **`Escape` + reopen** |
| **Q5 — Start-only route-aborted submit** | Search Start caption → match by `/asb/<token>` (real caption collision: `offered=2`, matched exact `option_index=0`) → confirm `BOUND_CHIP == 1` (`used_confirm=false`) → route-abort submit | **`rpcid="eb1hJf"`** (in both URL and `f.req` body), `model_key="veo_3_1_i2v_lite"`, `carries_start_id=true`, `body_bytes=2969`, `_i2v_body_problem(...) == None`, **`maseQ_uploads=0`** |
| **Q6 — Start+End route-aborted submit** | Clean reload → bind Start by token (`bound_chips=1`, `offered=2`) + bind End by token (`bound_chips=2`, `offered=1`) → route-abort submit | **`rpcid="nprQif"`** (in both URL and `f.req` body), `model_key="veo_3_1_interpolation_lite"`, `carries_start_id=true`, `carries_end_id=true`, `body_bytes=3064`, `_interpolation_body_problem(...) == None`, **`maseQ_uploads=0`** |

## Key takeaways

1. **Zero-upload in-place frame binding works end-to-end on the wire.** Both `eb1hJf` (Start-only) and `nprQif` (Start+End interpolation) carried the exact pre-existing media UUIDs with `maseQ_uploads == 0` and passed the existing `_i2v_body_problem` and `_interpolation_body_problem` guards verbatim.
2. **Token matching is load-bearing, not theoretical.** Q5 encountered a real 2-option caption collision (`offered=2`) in the target project; `/asb/<token>` identity disambiguated the exact asset without relying on picker order or display labels (100% locale-invariant).
3. **Unfiltered picker reach is 15 recent items; caption search extends reach across the grid.** Q2 showed 15 unfiltered options out of 44 grid tiles. Searching by sanitized caption narrows to the target asset; if a caption was renamed in Flow, `search.fill("")` reliably resets Angular's filter client-side (`0` extra `UpteDb` calls) to check the unfiltered 15 options by exact `/asb/<token>`.
4. **No new `batchexecute` RPC ids were observed.** `eb1hJf`, `nprQif`, `UpteDb`, and `maseQ` are already documented in [`docs/FLOW_WIRE_RPCS.md`](../../FLOW_WIRE_RPCS.md).

## NOT measured

- A billed end-to-end generation (`jwpduf` / `as29s` poll + MP4 download) — deferred to live verification after unit tests and CI gates are green.
- A non-English UI locale (`pt-BR`, `vi`, `de-DE`) — all selectors used (`EMPTY_CHIP`, `BOUND_CHIP`, `PICKER`, `PICKER_SEARCH`, `PICKER_OPTION`, `PICKER_CONFIRM`, `img[data-media-id]`, `/asb/<token>`) are structural or opaque URL tokens, zero `has-text(...)`.
- An older asset scrolled past the rendered project grid (`img[data-media-id]`) — expected to exhaust `EXISTING_REF_WAIT_S` and fail loud with `ReferenceNotFoundError` (exit 32), identical to `#995` on `image i2i`.
- A cohort where `PICKER_OPTION` click requires clicking `PICKER_CONFIRM` (`button.detail-add-to-prompt-btn`, `#792`) — reusing `_confirm_frame_bound` preserves the existing `#792` confirm fallback automatically.
- `labs.google` — no profile here is served `labs.google` (`labs.google/fx/tools/flow` returns HTTP 308); the labs path never reads `*_in_project` and remains unchanged.
