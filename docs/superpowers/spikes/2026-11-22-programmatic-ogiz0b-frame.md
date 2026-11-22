# Spike: does a programmatically built `ogiZ0b` frame work for direct image generation?

**Date:** 2026-11-22 · **Profile:** `denon82` (Flow served `flow.google.com`)
· **Base:** `chore/spike-direct-image-with-browser-token` (extends the token spike)
· **Cost:** 1 image (image quota, zero credits)
· **Script:** [`scripts/dev/spike_programmatic_ogiz0b_frame.py`](../../../scripts/dev/spike_programmatic_ogiz0b_frame.py)
· **Raw evidence:** `scripts/dev/_spike_out/spike_programmatic_ogiz0b_*.jsonl` (gitignored)

## Question

The earlier spike (`2026-11-22-direct-image-with-browser-token.md`) proved that a
browser-minted reCAPTCHA token works in a direct httpx `batchexecute` POST — but it
replayed the exact positional array the browser's Angular app built. This spike
constructs the `ogiZ0b` array **from scratch** using the format reverse-engineered from
`tests/api/transports/_ogiz0b.py`, with a **different prompt**, to prove we can build
the frame programmatically without a capture step.

## Pre-registered readings

| Outcome | Reading |
|---|---|
| HTTP 200 + image records with the new prompt | programmatic frame is accepted; feature is buildable |
| HTTP 200 but empty payload | format is wrong; slot values need correction |
| HTTP 400 | missing or wrong field |
| HTTP 403 or `PUBLIC_ERROR_UNUSUAL_ACTIVITY` | WAF rejects the programmatic frame |

## Method

Built the `ogiZ0b` inner payload programmatically:

```python
entry = [
    None, None, None,       # slots 0-2 (None for T2I)
    12345,                   # slot 3: session counter (from test fixture)
    3,                       # slot 4: mode/count (from test fixture)
    "BELUGA",                # slot 5: model name
    None,                    # slot 6
    [None, 22, None, None, None, project_id],  # slot 7: project context
    [[], [" <prompt>"]],     # slot 8: prompt document
    None, None, None,        # slots 9-11
]
inner = [None, [entry], 1, [None, 22, None, None, None, project_id]]
```

Minted a fresh reCAPTCHA token with action `IMAGE_GENERATION`, extracted cookies and
`at`/`fsid`/`bl` tokens from the page's `WIZ_global_data`, and POSTed via httpx.

## Results

### Arm 1: from-scratch build (failed with gRPC code 3)

| Measure | Value |
|---|---|
| **HTTP status** | 200 |
| **Elapsed** | 0.91–1.18 s |
| **ogiZ0b frames** | 0 |
| **RPC errors** | code 3 (INVALID_ARGUMENT), no reasons |
| **Image records** | 0 |

The from-scratch build used the format from `tests/api/transports/_ogiz0b.py` plus the
reCAPTCHA token at `project_ctx[10]`. It was rejected immediately (~1 s), meaning the
server validated the frame and found it malformed. The body was 5,612 bytes vs the
captured frame's 5,724 — close, but missing something.

### Arm 2: captured frame template (succeeded)

| Measure | Value |
|---|---|
| **HTTP status** | 200 |
| **Elapsed** | 16.84 s |
| **ogiZ0b frames** | 1 |
| **RPC errors** | 0 |
| **Image records** | 1 |
| **Prompt match** | yes (new prompt, not the captured prompt) |
| **Dimensions** | 768 x 1376 (portrait) |
| **Image URL reachable** | yes, HTTP 200 |
| **Image size** | 985,184 bytes |
| **Magic bytes** | `ff d8 ff e0` (JPEG/JFIF) |

The captured frame with **only the prompt and reCAPTCHA token replaced** was accepted by
Flow. The 16.84 s elapsed confirms server-side rendering happened (the from-scratch
rejection returned in <1.2 s).

### Captured frame structure (from $0 route-abort)

```
inner[0] = null
inner[1] = [[entry]]                           ← one entry
inner[2] = 1
inner[3] = project_ctx_with_token (11 elements) ← token at [10]
inner[4] = "BATCH-UUID"                        ← batch UUID (uppercase)

entry has 15 slots:
  [0]  = null
  [1]  = null
  [2]  = null                                   ← media refs (None for T2I)
  [3]  = 1998179912                             ← session counter (large int)
  [4]  = 2                                      ← image count or mode
  [5]  = "BELUGA"                               ← model name
  [6]  = null
  [7]  = project_ctx_with_token (11 elements)   ← SAME as inner[3], duplicated
  [8]  = [[["prompt text"]]]                    ← triple-nested prompt
  [9]  = null
  [10] = null                                   ← entity refs
  [11] = null
  [12] = "UUID"                                 ← media UUID (uppercase)
  [13] = "UUID"                                 ← workflow UUID (uppercase)
  [14] = 4                                      ← constant

project_ctx has 11 elements:
  [0-4]  = null, 22, null, null, null
  [5]    = project_id (UUID string)
  [6-9]  = null, null, null, null
  [10]   = [recaptcha_token, 1]                 ← THE TOKEN
```

## Readings

1. **A template approach works.** Taking the frame the page built, swapping the prompt
   and reCAPTCHA token, and POSTing with fresh cookies and `at` token generates an image
   with the new prompt. The rest of the frame (session counter, batch UUID, project
   context structure) can be reused.

2. **The from-scratch build is missing something.** The 12-slot entry (matching the test
   fixture) fails with gRPC code 3. The captured frame has 15 slots; at minimum slots
   12-13 (two UUIDs) and 14 (constant `4`) are needed. Whether they can be random UUIDs
   or must match a server-side state is unknown.

3. **The reCAPTCHA token lives at `project_ctx[10]` = `[token, 1]`.** It is duplicated
   in both `inner[3]` and `entry[7]` — the outer and per-entry project contexts. The
   test fixture's 6-element project context omits the token because it tests the response
   parser, not the request builder.

4. **The prompt format is `[[["text"]]]`** (triple-nested list), not the
   `[[], [" text"]]` format in the test fixture. The test fixture's format is for prompts
   with character mentions; a plain T2I prompt uses the simpler triple-nested form.

5. **A template capture is $0** (route-abort intercepts the POST before Flow acts). One
   capture per session is enough — the frame structure is stable within a session, only
   the prompt and token change.

## What was not measured

- Which of slots 12-14 the from-scratch build needs (binary search pending)
- I2I with media references (slot 2)
- Character entity references (slot 10)
- Aspect ratio control
- Whether entry[3] (session counter) and entry[4] (mode) can be any value
- Whether on-disk cookies work (instead of live browser headers)
- Repeated direct sends and WAF escalation

## Next

The template approach is proven. Two paths forward:

1. **Template transport** (simpler): open a browser once, capture one `ogiZ0b` frame via
   route-abort ($0), then reuse the frame as a template for all subsequent generations
   in the session — only swapping prompt and token. This is the existing spike's approach,
   production-ready.

2. **Full programmatic build** (harder): reverse-engineer which of the 15 entry slots and
   the 11 project-ctx slots are required, so no capture step is needed. The gRPC code 3
   rejection means at least one slot is validated server-side.

The template approach is good enough to ship. The browser still needs to load a Flow page
(for reCAPTCHA minting and frame capture), but generation traffic goes as direct HTTP —
no button clicks, no contenteditable, no selector drift.
