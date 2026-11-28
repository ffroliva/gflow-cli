# Spike: where the account's selected video model can be read (#1371 / Task 28)

**Date:** 2026-11-27 · **Profile:** `ffroliva` on `flow.google.com` (build `boq_labs-ai-sandbox-frontend_20261120.00_p0`)
· **Script:** [`scripts/dev/spike_selected_video_model.py`](../../../scripts/dev/spike_selected_video_model.py)
· **Cost:** $0, zero credits, zero image quota
· **Raw evidence:** `scripts/dev/_spike_out/spike_selected_video_model_20261127_*.json` (gitignored)

## Question

Gate-0 evidence for Task 29: bare `gflow video t2v` sends no `--model` (`api/video.py:295`).
The Flow UI composes the generation key from the picker selection, which gflow today only
touches by opening the settings pane and clicking the picker (`_mc_settings_pane.py:150-158`).
No existing code reads the selection without that click.

This spike investigates where the account's selected video model can be read without a click:
can it be read browser-free from batchexecute replies or tRPC queries, or is it present on
the armed mint page in network responses, `localStorage`, or `sessionStorage`?

## Pre-registered readings

Written before any live execution:

| Observation | Reading |
|---|---|
| Carrier in in-page batchexecute or tRPC reply | in-page reply carrier; needs page load |
| Carrier in standalone RPC readable browser-free | browser-free carrier via `flow_wire` |
| Carrier in `localStorage` or `sessionStorage` | page storage carrier; readable without menu clicks |
| No carrier found in the listed sources | not found in the listed sources |

## Method

1. Headless Playwright persistent context launched on profile `ffroliva` via `build_client`.
2. Installed a strict route-abort handler for all billed video submit RPCs
   (`YhhmEf`, `eb1hJf`, `MZZa6b`, `nprQif`).
3. Captured all network responses during project load on `flow.google.com/project/<id>`
   (specifically inspecting `batchexecute` frames, tRPC queries, and preference endpoints).
4. Extracted all `localStorage` and `sessionStorage` key-value pairs via `page.evaluate`.
5. Ground truth check: Opened the settings pane, read `_visible_model_picker` text, and
   dismissed the pane with Escape without clicking any options.
6. Differential persistence test: Switched the selected model in the settings pane between
   Veo 3.1 Lite and Omni 1.2 Flash, observed storage mutation, reloaded the page to verify
   cross-reload persistence, and restored the original selection.

## Results

### 1. Carrier identified: `localStorage['flow-prompt-box-settings']`

The account's selected model is carried directly in `localStorage` under the key
`flow-prompt-box-settings`. When parsed as JSON, it contains:

```json
{
  "aspectRatio": "LANDSCAPE",
  "imageModelFamilyId": "nano_banana_pro",
  "videoModelFamilyId": "veo_3_1_lite",
  "mode": "IMAGE",
  "outputCount": 1,
  "videoDuration": 4,
  "videoResolution": 4
}
```

The key fields are:
- `videoModelFamilyId`: String identifying the selected video model family (e.g. `"veo_3_1_lite"`
  or `"macaron_cookie"`).
- `videoDuration`: Integer duration in seconds (e.g. `4`).
- `videoResolution`: Integer resolution enum/tier (e.g. `4` representing 360p).
- `aspectRatio`: Aspect ratio string (e.g. `"LANDSCAPE"`).

### 2. Differential test and persistence

- **Initial state:** `videoModelFamilyId` was `"veo_3_1_lite"`.
- **UI Switch:** When switched to Omni 1.2 Flash via the settings pane, `localStorage['flow-prompt-box-settings']`
  was immediately updated to `{"videoModelFamilyId": "macaron_cookie", ...}`.
- **Persistence across reload:** Calling `page.reload(wait_until="domcontentloaded")` preserved
  `videoModelFamilyId: "macaron_cookie"`.
- **Restoration:** Switching back to Veo 3.1 Lite immediately restored
  `videoModelFamilyId: "veo_3_1_lite"`.
- **No network mutation:** Changing the model in the UI emitted no network RPC. The setting is
  maintained entirely in client-side storage by the Angular frontend.

### 3. Network traffic on project load

- **`HTrJv`:** General model catalog listing all available model families, aliases, usages, and
  deprecation dates. Does not carry the active user selection.
- **`yBhWQ`:** Model family capability/availability list (`abra`, `macaron_cookie`, `veo_3_1_quality`,
  `veo_3_1_lite`, `veo_3_1_fast`). Does not carry user selection.
- **`cPZSdc`:** App configuration and experiment flags (`agent_off`, active experiments). Index
  `[1][21]` contains `"macaron_cookie"`, but this is the global featured announcement banner model
  ("Introducing Gemini Omni 1.2 Flash"), not user selection; it did not change when Veo 3.1 Lite was selected.
- **`nzlxg`:** Credits balance and refresh time.
- **`jwpduf` / `as29s`:** Media status and URLs.
- **tRPC:** No tRPC requests occur on `flow.google.com` (tRPC was specific to the legacy labs frontend).

### 4. Browser-free vs. In-page access

Because the setting is stored exclusively in `localStorage` under origin `https://flow.google.com`,
it cannot be read browser-free via `flow_wire` prior to browser launch.

However, the direct transport already arms a headless browser page for the reCAPTCHA mint
(`direct_http._mint` on `flow.google.com/project/<id>`). On that armed mint page, the selection can
be read instantly with:

```python
val = await page.evaluate("() => localStorage.getItem('flow-prompt-box-settings')")
```

This read requires:
- **Zero clicks**
- **Zero DOM traversal or menu opening**
- **Zero additional network requests**
- Execution time: < 5 ms once the mint page is loaded.

### 5. Selection-to-Key mapping

Combined with the route-aborted captures from [`2026-11-27-direct-video-and-headless-mint.md`](2026-11-27-direct-video-and-headless-mint.md)
(section "The default video shape over direct HTTP"), the mapping from `flow-prompt-box-settings` to
wire submit key is:

| `videoModelFamilyId` | Duration / Resolution | Wire submit key | Notes |
|---|---|---|---|
| `macaron_cookie` | 4s, 360p | `macaron_cookie_t2v_4s_360p` | Measured direct & accepted |
| `veo_3_1_lite` | default | `veo_3_1_t2v_lite` | Measured direct & accepted |
| `veo_3_1_fast` | default | `veo_3_1_t2v_fast` | Measured in catalog |
| `veo_3_1_quality` | default | `veo_3_1_t2v_quality` | Measured in catalog |
| `abra` | 4s, 360p | `abra_t2v_4s_360p` | Measured in catalog |

## Reading against the pre-registered table

| Observation | Reading |
|---|---|
| Carrier in `localStorage.flow-prompt-box-settings.videoModelFamilyId` | **page storage carrier; readable without menu clicks** |

## Impact on Task 29

This finding activates the **second branch** of Task 29:
- `choose_video_route` returns `DIRECT` for t2v `model=None` (launching headless).
- The direct transport reads `localStorage.getItem('flow-prompt-box-settings')` on the armed mint
  page without clicks.
- If the parsed `videoModelFamilyId` maps to an entry in `MEASURED_VIDEO_KEYS`, the direct HTTP submit
  proceeds with that key.
- If the read fails, returns invalid JSON, or names an unmeasured model, the transport raises
  `DirectAttemptEnded(NotSent("t2v_key_unresolved"))`, which the retry loop routes to `FallbackUi(HEADED)`.

## Not measured

- Behavior on a profile where `localStorage` has never been initialized by Flow (expected to fall back
  to UI or default model).
- Accounts served on `labs.google` (where `flow.google.com` is not the active host).
