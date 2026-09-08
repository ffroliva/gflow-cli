# Migrated image generation uses `ogiZ0b` and returns the completed image inline

**Date:** 2026-09-08 · **Issue:** #639 · **Profile:** permanently migrated `arjhibe`
**Probe:** [`scripts/dev/spike_migrated_image_submit.py`](../../../scripts/dev/spike_migrated_image_submit.py)
**Cost:** two real image-quota submissions (T2I and local-file I2I); aborted controls cost $0
**Raw evidence:** `scripts/dev/_spike_out/migrated_image_submit_*.json` (gitignored,
token- and signed-query-redacted)

## What was observed

The account landed directly on `https://flow.google.com/project/<id>`. It had persisted
Agent mode (`button.agent-mode-chip[aria-pressed=true]`) and an expanded
`flow-agent-panel`; closing the panel and switching that structural control off made the
classic `.settings-trigger-button` visible. Treating a hidden settings button as readiness
would have timed out on a healthy editor.

The settings pane then rendered Image mode as a hit-testable radio:

```text
mode:   [image* / videocam]
aspect: [crop_16_9* / crop_landscape / crop_square / crop_9_16]
count:  [x1* / x2 / x3 / x4]
model:  GEM_PIX_2 (button text rendered as Nano Pro)
```

For both T2I and local-file I2I the page submitted on **rpcid `ogiZ0b`**. Nothing was
replayed by the probe. The request included a fresh reCAPTCHA token minted by Flow's own
page. The T2I request carried no reference slot; the I2I request carried the exact media id
returned by the page's existing `maseQ` upload and represented by a
`.mention-chip[data-reference-type=media]` in the composer.
The `ogiZ0b` response arrived after generation completed (about 26–40 seconds in the
measured runs) and carried, in one payload:

- media id;
- workflow id;
- seed and submitted prompt;
- signed `https://flow-content.google/image/<media-id>?...` URL;
- dimensions (`1376 × 768` in both samples);
- sibling workflow record with display name and project id.

No status polling RPC was needed to obtain the generated image. Downloading the T2I URL
with redirects disabled returned HTTP 200, **492,358 bytes**, beginning
`ff d8 ff e0 00 10 4a 46 49 46` (JPEG/JFIF). The I2I result appeared as a new
`flow-image-tile` at `1376 × 768`; its response contained the uploaded reference id.

## Consequences for the implementation

1. Extend the existing migrated composer rather than create or replay a new RPC client.
2. Route image generation before `FlowApiClient._mint_recaptcha_token`: the page owns the
   migrated token and submit, while the old client mint is labs-path work performed too early.
3. Parse `ogiZ0b` as its own image record. The video `CAE` parser is intentionally not
   widened: image and video have different invariants and URL fields.
4. Assert Image mode and every requested setting before submit. For I2I, assert every
   uploaded media id is in the outgoing `ogiZ0b` body before accepting success.
5. Close/collapse persisted Agent mode before using the classic migrated composer.

## What was not measured

- UUID/name-only image references and character references on the migrated image path.
- Every image model. `GEM_PIX_2` was observed; model-menu labels/keys for NARWHAL and
  IMAGEN_3_5 still require either a zero-cost menu read or the first live run.
- A content-policy rejection or an out-of-quota image response.
- A labs-host control on this account: the rollout is permanent, so the old host cannot be
  reached with this profile.
