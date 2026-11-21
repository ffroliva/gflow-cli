# Spike: moving a media item to Flow's Trash and restoring it (#1363)

**Date:** 2026-11-21 · **Host served:** `flow.google.com` · **Cost:** $0
(0 generations; one existing cataloged image moved to Trash and restored cleanly in 50.2 s) · **Script:**
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
| **1a. Direct-capable `batchexecute` RPC** | A single `batchexecute` RPC (e.g. `pGCYOe`) is sent in the `trash` window (and absent from `idle_before_trash`), replies HTTP 200 with no RPC error, carries `<media_id>` / `<project_id>` with no `<tok:N>` slot, and the item moves to Trash and restores cleanly. | Media trash is a `batchexecute` mutation and a candidate for a direct page-free write verification pass. | **CONFIRMED**. `pGCYOe` was sent for trash (HTTP 200, no RPC errors), carrying `<media_id>` and `<project_id>` with no `<tok:N>` slot (`has_token_slot: false`). The same RPC `pGCYOe` was sent for restore, carrying `<media_id>`, `""`, and `<project_id>` with `collection_id`. Trashed and restored cleanly in 50.2 s total elapsed time. |
| **1b. Token-gated `batchexecute` RPC** | Same as 1a, except the request frame contains a `<tok:N>` string slot (or follows a `recaptcha/enterprise/reload` call). | Media trash uses `batchexecute` but requires a browser-minted token or page-driven click path. | **REFUTED**. `has_token_slot` was `false` on both `pGCYOe` request frames. No reCAPTCHA reload or token mint occurred for `pGCYOe`. |
| **2. Multi-RPC sequence** | The `trash` window sends a primary media mutation plus a secondary RPC (such as `o8DA4` resetting `thumbnail_media_key` or `Zzl0ze` refreshing the listing). | Media trash requires or conditionally triggers a companion project update; inspect the secondary RPC's field mask to determine whether it is thumbnail-conditional. | **OBSERVED (conditional)**. `pGCYOe` was the sole functional mutation RPC for trash (`rpc_ids_sent_attributed: ["pGCYOe"]`). In restore, telemetry `WuwhI` was sent for grid filter and page view events, but no secondary project metadata mutation (like `o8DA4` thumbnail reset) was required because the item was not the designated project thumbnail. |
| **3. REST endpoint on `aisandbox-pa.googleapis.com`** | No `batchexecute` write fires in `trash`; an `aisandbox-pa.googleapis.com` request carries the media or workflow id and moves the item to Trash. | Media trash is served by the aisandbox REST API rather than `batchexecute`. | **REFUTED**. Zero requests went to `aisandbox-pa.googleapis.com` (`aisandbox_requests: []`). |
| **4. No network call in action window** | Positive control (`Zzl0ze` HTTP 200 on project load) holds, the item moves in the UI, and zero network requests fire during `trash`. (If the positive control fails or the item does not move, the result is **unmeasured**.) | Action is client-buffered or uses a non-HTTP channel; if the item did not move, the run is unmeasured. | **REFUTED**. `pGCYOe` fired and answered HTTP 200 in both trash and restore windows. |
| **5. Structural selector miss** | 0/N structural selectors match on the grid tile, confirmation dialog, Trash nav link, or Trash view restore action. | Evidence about the candidate selector list only, never about the feature. Use the captured `dom_inventory` in `summary.json` to identify the rendered custom element or ligature. | **RESOLVED**. Initial attempts failed because `img[data-media-id]` in Flow's grid renders the *workflow id*, not the *media id*, and Angular CDK virtual scrolling did not render off-screen tiles. Once the workflow id was resolved via the catalog asset row and the left-nav media filter (`image`) was applied, structural anchors matched cleanly. |

## Results

| Step | RPC ids sent (attributed) | RPC ids aborted | Request shape & ID positions | Reply shape | Structural anchor matched | Observed state |
|---|---|---|---|---|---|---|
| Project open (positive control) | `Zzl0ze`, `UpteDb`, `ngNC2` | none | `Zzl0ze`: `["projects/<project_id>", null, null, null, [1]]` | Generation records listing | `aisandbox-root`, `cdk-virtual-scroll-viewport` | Positive control held (`Zzl0ze` HTTP 200). Media present in project listing and top grid. |
| (a)-(b) Move to Trash + confirm | `pGCYOe` | none | `[[["<media_id>", null, null, [null, null, 1], "<project_id>"]], [["<s:17>"]]]`<br>Positions: `media_id` at `[0][0][0]`, `project_id` at `[0][0][4]` | `[[["<media_id>", null, null, ["<s:35>", ["<n>", "<n>"], true, null, "<workflow_id>", "<uuid>", ["<n>", "<n>"]], "<project_id>"]]]`<br>Slot `[0][0][3][2]` is `true`, `workflow_id` at `[0][0][3][4]` | Trigger: `flow-grid-tile-container:has([data-media-id="<workflow_id>"]) button[aria-haspopup='menu']:has(mat-icon:text-is('more_vert'))`<br>Action: `.cdk-overlay-pane [role='menuitem']:has(mat-icon:text-is('delete'))` | No confirm dialog displayed. Tile immediately disappeared from grid (`observed_gone_from_grid: true`). |
| (c) Navigate to Trash view | none (client route) | none | none | none | `a[href$='/trash']` | Navigated to `/project/<project_id>/trash`. Tile present in Trash view (`observed_in_trash_view: true`). |
| (d) Restore from Trash | `pGCYOe`, `WuwhI` | none | `[[["<media_id>", "", null, [], "<project_id>"]], [["<s:17>", "collection_id"]]]`<br>Positions: `media_id` at `[0][0][0]`, `""` at `[0][0][1]`, `project_id` at `[0][0][4]` | `[[["<media_id>", null, null, ["<s:35>", ["<n>", "<n>"], null, null, "<workflow_id>", "<uuid>", ["<n>", "<n>"]], "<project_id>"]]]`<br>Slot `[0][0][3][2]` reverted from `true` to `null` | Action: `flow-grid-tile-container:has([data-media-id="<workflow_id>"]) button:has(mat-icon:text-is('undo'))` | Restored cleanly. Tile disappeared from Trash view (`observed_gone_from_trash: true`). |
| (e) Verify back in project grid | none | none | none | none | `button:has(mat-icon:text-is('arrow_back'))` | Navigated back to `/project/<project_id>`. Tile verified present in project grid (`observed_back_in_grid: true`, `restored_cleanly: true`). |

## Failed-Run Lessons & Virtual Scroller Reality

Earlier attempts (`spike_1363_20261121_012345`, `_013535`, `_013626`, `_020844`) and the tile inventory run (`_015351`) exposed two critical architectural facts about Flow's frontend:

1. **DOM Attribute Mismatch (`workflow_id` vs `media_id`):**
   In Flow's project grid, `img[data-media-id]` does **not** carry the `flow_media_id`. It carries the `flow_workflow_id` (the generation workflow identifier). Querying `[data-media-id="<flow_media_id>"]` matches 0 elements. To locate a tile in the DOM by `media_id`, the client must first resolve the catalog row's `flow_workflow_id` or query the project listing (`Zzl0ze`).
2. **Angular CDK Virtual Scroller:**
   The grid container is a `cdk-virtual-scroll-viewport`. Flow renders only the tiles within the active viewport window. Tiles located further down the project grid are unmounted from the DOM. Synthetic wheel events and programmatic `scrollTop` mutations failed to trigger Angular CDK's scroll cycle reliably. Applying the left-nav media filter (`image` / `videocam`) narrowed the item set, but items outside the top viewport remained unreachable via DOM selectors.
3. **UI vs Wire Implication:**
   Automating media trash via Playwright UI clicks is fundamentally fragile and cannot reliably reach items situated deep in a project's history. By contrast, the wire RPC `pGCYOe` operates strictly on `<media_id>` and `<project_id>`. It requires *zero* DOM presence, *no* `workflow_id` resolution, and *no* virtual scroller interaction.

## The 17-Character Field Mask Redaction

In the captured requests:
- **Trash:** `[[["<media_id>", null, null, [null, null, 1], "<project_id>"]], [["<s:17>"]]]`
- **Restore:** `[[["<media_id>", "", null, [], "<project_id>"]], [["<s:17>", "collection_id"]]]`

The string at `[1][0][0]` was redacted to `<s:17>` because the spike harness strips non-enum strings to protect against accidental PII logging. In Protobuf field-mask semantics, this string is a standard proto field path (e.g., `delete_state_info`, `is_trashed_state`, or similar), not a secret or user credential.

Capturing the exact literal string is designated as **Task 1** of the implementation plan. The spike script [`scripts/dev/spike_1363_trash.py`](../../../scripts/dev/spike_1363_trash.py) has been updated with a `--show-mask` flag that extracts and prints only unredacted field mask string(s) from `pGCYOe` frames when executed.

## Verdict & Architectural Implications

- **Observed:**
  Moving an item to Trash sends `batchexecute` RPC `pGCYOe` with shape `[[[media_id, null, null, [null, null, 1], project_id]], [[<17-char mask>]]]`. The response echoes the media record with `true` at `[0][0][3][2]` and `workflow_id` at `[0][0][3][4]`.
  Restoring an item sends `pGCYOe` with shape `[[[media_id, "", null, [], project_id]], [[<17-char mask>, "collection_id"]]]`. The response echoes the media record with slot `[0][0][3][2]` reverted to `null`.
  Both operations are unbilled ($0, zero credits spent), require no reCAPTCHA token (`has_token_slot: false`), and require no companion project metadata update when the item is not the project thumbnail.
- **Architectural Implications:**
  1. **Wire-First Transport:** Implement `pGCYOe` directly via `FlowApiClient` (and page-free `flow_wire.py` where fresh session cookies permit, falling back to browser context evaluation when cookies are stale). Do not attempt UI automation clicks for media trash.
  2. **Catalog ID Resolution:** `gflow media trash <id>` and `gflow media restore <id>` should accept either a `flow_media_id` or a local catalog ID, resolved safely via `DataRepository`. Cross-profile matches must be rejected unless `--profile` is specified.
  3. **Safety Contract:** Because moving to trash is destructive (though restorable), the CLI must require confirmation unless `--yes` is supplied. Both subcommands must support `--json` output reporting the media ID, project ID, and recovery instructions.
  4. **Dual Surface (MCP & CLI):** Mirror `gflow media trash` and `gflow media restore` with MCP twin tools (`media_trash`, `media_restore`) accepting the same arguments.
