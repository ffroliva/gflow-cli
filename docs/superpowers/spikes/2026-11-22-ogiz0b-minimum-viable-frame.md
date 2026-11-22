# Spike: minimum viable `ogiZ0b` frame for direct image generation

**Date:** 2026-11-22 · **Profile:** `denon82` (Flow served `flow.google.com`)
· **Base:** `chore/spike-direct-image-with-browser-token`
· **Cost:** 1 image (Arm A succeeded immediately; Arms B–D not needed)
· **Script:** [`scripts/dev/spike_ogiz0b_minimum_frame.py`](../../../scripts/dev/spike_ogiz0b_minimum_frame.py)

## Question

The captured frame has 15-slot entries. The test fixture in
`tests/api/transports/_ogiz0b.py` uses 12 slots. Which slots are required?

## Pre-registered arms

| Arm | Change | Expected cost |
|---|---|---|
| A | entry truncated to 12 slots (no [12-14]) | 1 image if accepted, $0 if gRPC 3 |
| B | entry[12-13] = random UUID, entry[14] = 4 | same |
| C | entry[12-13] = null, entry[14] = 4 | same |
| D | entry[3] = 1, entry[4] = 1 | same |

Arms run sequentially; stop after the first generation.

## Results

**Arm A generated on the first try.** Arms B–D were not needed.

| Measure | Value |
|---|---|
| **HTTP status** | 200 |
| **Elapsed** | 19.71 s |
| **Image records** | 1 |
| **Prompt match** | yes (new prompt, different from the captured one) |
| **Dimensions** | 768 x 1376 (portrait) |

## Readings

1. **Slots 12, 13, and 14 are NOT required.** The 12-slot entry format matches
   the test fixture in `_ogiz0b.py` and is sufficient for Flow to accept the frame.
   The extra three slots (two UUIDs and constant `4`) are either client-side hints
   the server ignores, or optional fields that default safely.

2. **The from-scratch builder's failure was not about slot count.** The earlier
   from-scratch build also had 12 slots but was rejected with gRPC code 3. The
   difference was the **project context format**: the from-scratch build used a
   6-element array `[None, 22, None, None, None, project_id]` (from the test
   fixture), while the working template uses an **11-element** array with the
   reCAPTCHA token at position 10. The token's position in the project context —
   not the entry slot count — was the missing piece.

3. **The minimum viable frame for T2I is:**
   ```
   inner = [
       null,
       [[entry]],           # one entry with 12 slots
       1,
       project_ctx,         # 11-element, token at [10]
       "BATCH-UUID"         # any uppercase UUID
   ]

   entry = [
       null, null, null,    # 0-2: T2I (no media refs)
       <session_counter>,   # 3: any int
       <image_count>,       # 4: int (2 observed in browser)
       "BELUGA",            # 5: model name
       null,                # 6
       project_ctx,         # 7: 11-element with token (duplicated)
       [[["prompt"]]],      # 8: triple-nested prompt
       null, null, null     # 9-11
   ]

   project_ctx = [
       null, 22, null, null, null, project_id,
       null, null, null, null,
       [recaptcha_token, 1]
   ]
   ```

## What was not measured

- Whether entry[3] (session counter) and entry[4] (image mode/count) can be
  arbitrary values — Arm D was not reached.
- I2I with media references, character entity references, aspect ratio control.
- On-disk cookies vs live browser headers.

## Next

The from-scratch builder can now be corrected: use the 11-element project context
instead of the 6-element one from the test fixture. This eliminates the need for
the template capture step entirely.
