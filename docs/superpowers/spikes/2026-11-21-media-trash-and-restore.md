# Spike: moving a media item to Flow's Trash and restoring it (#1363)

**Date:** 2026-11-21 · **Host served:** pending run · **Build:** pending run · **Cost:** $0
(0 generations; one existing cataloged image moved to Trash and restored) · **Script:**
[`scripts/dev/spike_1363_trash.py`](../../../scripts/dev/spike_1363_trash.py)

## Question

Issue #1363 proposes a command to move a generated Flow item to Flow's restorable Trash.
In the earlier UI-action survey ([`2026-11-10-rpc-map-ui-actions.md`](2026-11-10-rpc-map-ui-actions.md),
recorded in [`docs/FLOW_WIRE_RPCS.md`](../../FLOW_WIRE_RPCS.md)), clicking the `delete` icon on a
clip page queued a candidate `batchexecute` RPC (`pGCYOe`, a field-mask update with shape
`[[[<media id>, null, null, [null, null, 1], <project id>]], [[<s:17>]]]`, followed by `o8DA4`
resetting `thumbnail_media_key`), but both requests were aborted by the strict write guard
("seen, not sent") and a reload confirmed the clip was still in the project. Character deletion
uses `POST /v1/flow:batchDeleteAssets`, which is a separate surface and not evidence for media
Trash.

Before designing a transport method or CLI/MCP contract for #1363, four questions require live
measurement on `flow.google.com`:

1. **Wire RPC and payload shape:** Which `batchexecute` RPC id(s) — or `aisandbox-pa.googleapis.com`
   REST endpoint(s) — are actually sent and accepted when an item is moved to Trash and when it is
   restored from the Trash view?
2. **Identifier contract:** Which identifier(s) (`flow_media_id`, `flow_project_id`,
   `flow_workflow_id`) appear at which positional slots in the Trash and Restore request and reply
   frames?
3. **Token requirement:** Does either request frame carry an opaque page-minted token slot
   (`<tok:N>`), or is the frame free of reCAPTCHA tokens (like `jHPbke`, `o8DA4`, and `QI2zvc`)?
4. **Structural DOM anchors:** Which locale-invariant structural selectors (Material Symbols
   ligatures in `mat-icon` or `i.google-symbols`, ARIA roles, custom-element boundaries, and `href`
   attributes — never display text or `[role=menuitem]` positional order) open the item menu,
   trigger Trash, confirm any dialog, navigate to `/project/<id>/trash`, and trigger Restore?

## Method

[`scripts/dev/spike_1363_trash.py`](../../../scripts/dev/spike_1363_trash.py) reuses the payload
shape redaction and frame parser from
[`scripts/dev/spike_rpc_map_1226.py`](../../../scripts/dev/spike_rpc_map_1226.py) and opens the
target project through `FlowApiClient` (holding the profile lease).

1. **Zero-generation target selection:** The script generates nothing. It requires an explicit
   disposable image `--media-id` from the local catalog plus `--yes-trash-this` (refusing non-image
   catalog rows by default, and listing the newest cataloged images with exit 2 when `--media-id`
   is omitted).
2. **Targeted route guard:** Playwright context routes are registered exclusively on
   `**/batchexecute**` and `https://aisandbox-pa.googleapis.com/**` (avoiding a catch-all route).
   All billed generation and upscale RPCs (`YhhmEf`, `eb1hJf`, `MZZa6b`, `nprQif`, `ogiZ0b`,
   `fZytfe`, `SPrCad`, `p0UkFb`), project-create/delete RPCs (`jHPbke`, `QI2zvc`, `maseQ`), and
   billed REST generation endpoints are unconditionally aborted. All other requests pass through
   and are logged to gitignored `scripts/dev/_spike_out/spike_1363_<ts>/` (`summary.json` and
   `wire.jsonl`) with values redacted to structural shapes and target ID positions tagged as
   `<media_id>`, `<project_id>`, and `<workflow_id>`.
3. **Five-step UI cycle with restore safeguard:**
   - **(a)** Open the project grid, verify the positive control (`Zzl0ze` response and visible tile
     for `media_id`), record a 3 s `idle_before_trash` window, open the tile's action surface
     structurally, and click the `delete` / `delete_outline` action.
   - **(b)** If a confirmation dialog (`[role='dialog']`, `[role='alertdialog']`,
     `mat-dialog-container`) appears, record its button inventory and click its structural primary
     confirm control.
   - **(c)** Observe the tile gone from the project grid, then navigate to the Trash view via its
     structural navigation link (`a[href$='/trash']` or `delete` nav icon, with `/project/<id>/trash`
     fallback) and observe the tile present in Trash.
   - **(d)** Record an `idle_before_restore` window, then trigger the Restore action in the Trash
     view using structural ligature selectors (`restore_from_trash`, `restore`, `undo`, `history`,
     `replay`).
   - **(e)** Return to the project grid and verify the tile is present again. If any step raises
     after the Trash click, a `finally` block runs an emergency restore attempt and prints the
     `media_id` and Trash URL if manual recovery is still required.

## Pre-registered Readings

| Case | Pre-registered condition | Reading | Observed |
|---|---|---|---|
| **1a. Direct-capable `batchexecute` RPC** | A single `batchexecute` RPC (e.g. `pGCYOe`) is sent in the `trash` window (and absent from `idle_before_trash`), replies HTTP 200 with no RPC error, carries `<media_id>` / `<project_id>` with no `<tok:N>` slot, and the item moves to Trash and restores cleanly. | Media trash is a `batchexecute` mutation and a candidate for a direct page-free write verification pass. | pending run |
| **1b. Token-gated `batchexecute` RPC** | Same as 1a, except the request frame contains a `<tok:N>` string slot (or follows a `recaptcha/enterprise/reload` call). | Media trash uses `batchexecute` but requires a browser-minted token or page-driven click path. | pending run |
| **2. Multi-RPC sequence** | The `trash` window sends a primary media mutation plus a secondary RPC (such as `o8DA4` resetting `thumbnail_media_key` or `Zzl0ze` refreshing the listing). | Media trash requires or conditionally triggers a companion project update; inspect the secondary RPC's field mask to determine whether it is thumbnail-conditional. | pending run |
| **3. REST endpoint on `aisandbox-pa.googleapis.com`** | No `batchexecute` write fires in `trash`; an `aisandbox-pa.googleapis.com` request carries the media or workflow id and moves the item to Trash. | Media trash is served by the aisandbox REST API rather than `batchexecute`. | pending run |
| **4. No network call in action window** | Positive control (`Zzl0ze` HTTP 200 on project load) holds, the item moves in the UI, and zero network requests fire during `trash`. (If the positive control fails or the item does not move, the result is **unmeasured**.) | Action is client-buffered or uses a non-HTTP channel; if the item did not move, the run is unmeasured. | pending run |
| **5. Structural selector miss** | 0/N structural selectors match on the grid tile, confirmation dialog, Trash nav link, or Trash view restore action. | Evidence about the candidate selector list only, never about the feature. Use the captured `dom_inventory` in `summary.json` to identify the rendered custom element or ligature. | pending run |

## Results

| Step | RPC ids sent (attributed) | RPC ids aborted | Request shape & ID positions | Reply shape | Structural anchor matched | Observed state |
|---|---|---|---|---|---|---|
| Project open (positive control) | pending run | pending run | pending run | pending run | pending run | pending run |
| (a)-(b) Move to Trash + confirm | pending run | pending run | pending run | pending run | pending run | pending run |
| (c) Navigate to Trash view | pending run | pending run | pending run | pending run | pending run | pending run |
| (d) Restore from Trash | pending run | pending run | pending run | pending run | pending run | pending run |
| (e) Verify back in project grid | pending run | pending run | pending run | pending run | pending run | pending run |

## Verdict

- **Observed:** pending run.
- **Not measured:** pending run.
