# Spike: direct httpx image generation with a browser-minted reCAPTCHA token

**Date:** 2026-11-22 · **Profile:** `denon82` (Flow served `flow.google.com`)
· **Build:** observed at `19:15 UTC` · **Base:** `develop` 2a5c2590
· **Cost:** 1 image (Arm A succeeded on the first attempt; the route-abort capture was $0)
· **Script:** [`scripts/dev/spike_direct_image_with_browser_token.py`](../../../scripts/dev/spike_direct_image_with_browser_token.py)
· **Raw evidence:** `scripts/dev/_spike_out/spike_direct_image_token_20261122_191530.jsonl` (gitignored, redacted)

## Question

Can a reCAPTCHA Enterprise token minted by `grecaptcha.enterprise.execute()` inside a
real browser page be used in a direct HTTP `batchexecute` call (via `httpx`, outside the
browser) for `ogiZ0b` (image generation)? Or does Flow bind the token to the page's HTTP
client and reject the out-of-band request?

## Pre-registered reading

| Outcome | Reading |
|---|---|
| Arm A: httpx POST returns 200 with image URLs | token is NOT client-bound; direct image generation is viable |
| Arm A error frame with reCAPTCHA reasons | escalate to Arm B (in-page fetch) |
| Arm B: in-page fetch accepted | refusal is httpx-bound (UA/fingerprint), not token-bound |
| Arm B also refused | escalate to Arm C (page's own UI submit) to rule out profile refusal |
| HTTP 403 / `PUBLIC_ERROR_UNUSUAL_ACTIVITY` | WAF rejects regardless of a valid token |
| 200 but empty payload | frame structure wrong; fix before concluding |

## Method

The brief's step 3 (`_build_batch_generate_images_body`) was **replaced**. That function
builds a REST-shaped JSON dict for `POST /v1/projects/{id}/flowMedia:batchGenerateImages`;
the migrated host's `ogiZ0b` sends a **positional array** via `batchexecute`. The spike
therefore captures the real frame the page sends (via route-abort, $0) and replays it.

**Phase 1 — CAPTURE ($0):** opened the browser on the profile, navigated to a project,
typed a prompt, clicked Generate, and route-aborted the `ogiZ0b` POST before Flow acted.
Also hooked `grecaptcha.enterprise.execute` to record the action string.

**Phase 2 — ARM A:** minted a fresh reCAPTCHA token with the discovered action
(`IMAGE_GENERATION`), spliced it into the captured frame (replacing every occurrence of
the old token), and POSTed via `httpx` using the same URL, headers and cookies as the
captured browser request.

Arms B and C were not needed — Arm A succeeded.

## Results (one run)

| Measure | Value |
|---|---|
| **HTTP status** | 200 |
| **`ogiZ0b` frames returned** | 1 |
| **RPC errors** | 0 |
| **Image records decoded** | 1 |
| **Prompt match** | yes (`_PROMPT in record.prompt`) |
| **Dimensions** | 768 x 1376 (portrait) |
| **Image URL reachable** | yes, HTTP 200 |
| **Image size** | 860,106 bytes |
| **Image magic bytes** | `ff d8 ff e0` (JPEG/JFIF) |
| **Elapsed (httpx POST to response)** | 19.65 s |
| **Captured body length** | 5,793 bytes (URL-encoded) |
| **Old token length** | 2,510 chars |
| **New (minted) token length** | 2,468 chars |

### Key discovery: reCAPTCHA action is `IMAGE_GENERATION`

The existing `recaptcha.py` mint uses `action="imageGeneration"` (the labs-path value,
visible in `src/gflow_cli/api/recaptcha.py`). The migrated host's page calls
`grecaptcha.enterprise.execute(siteKey, { action: "IMAGE_GENERATION" })` — uppercase,
underscore-separated. Whether `imageGeneration` would also be accepted is **unmeasured**;
the spike used only the page's own action.

### Latency comparison (rough)

The page-driven path (UI automation: type prompt → click Generate → observe reply → download)
typically takes 26–40 s end-to-end (measured in the 2026-09-08 spike). The direct httpx
POST took 19.65 s, which includes:

- Token mint via `page.evaluate` (~0.2 s)
- httpx POST + server generation time + response (~19 s)
- Image download (~0.5 s)

The httpx path removes: settings verification, overlay dismissal, submit-button wait,
the entire UI observation loop, and body-loss recovery. It is structurally faster because
it removes I/O steps that do not contribute to generation time.

## Readings

1. **The reCAPTCHA token is NOT bound to the page's HTTP client.** A token minted inside
   a Playwright-managed Chrome page was accepted by an `httpx` POST that shared no TLS
   session, no TCP connection, and no browser fingerprint with the minting page. The only
   shared material was the cookies (extracted from the browser's request headers) and the
   token itself.

2. **Direct image generation via `batchexecute` is viable.** The `ogiZ0b` frame structure
   (a positional array inside `f.req`) is the same one the page sends. The response is
   decoded by the existing `image_records()` parser. The signed image URL is downloadable
   with no cookies.

3. **A lightweight browser is still needed** — but only to:
   - Mint reCAPTCHA tokens (the `grecaptcha.enterprise` API needs a real page)
   - Discover the site key (from the `<script>` tag)
   - Provide the cookie jar and `at` token for direct calls

   The browser does NOT need to type prompts, click buttons, observe network traffic,
   dismiss overlays, handle agent-mode panels, recover from lost bodies, or manage
   settings panes. It can sit idle between mints.

4. **The `ogiZ0b` frame is a positional array, not a REST JSON dict.**
   `_build_batch_generate_images_body()` builds the REST shape. The direct path must
   either (a) capture a frame from the page and template it, or (b) reverse-engineer
   the positional array format. This spike used (a).

5. **This is one run on one profile.** It shows the call CAN work direct. It does not
   show it stays accepted over time, that throttling treats it like `nzlxg`, or that
   repeated direct calls on the same profile do not trigger WAF escalation.

## What was not measured

- Whether the labs-path action `imageGeneration` (lowercase, camelCase) also works on
  the migrated host's reCAPTCHA Enterprise check.
- Whether I2I (with image references) works direct — the frame would carry media ids
  at positional slot 2.
- Whether character entity references (slot 10) survive a direct send.
- Whether repeated direct sends trigger WAF escalation or throttling.
- Whether a different user-agent (not the browser's own) is accepted.
- Whether the `at` token from the shell GET (`flow_wire._shell_tokens`) works, or only
  the one from the page's own requests.
- Whether the page's cookies read from the on-disk database work, or only the ones
  extracted from a live browser request.

## Next

This finding is evidence that a hybrid architecture is viable: a minimal browser for
token minting and site-key discovery, plus direct `httpx` for all generation traffic.
Shipping it is a transport change, so it starts with `/gflow:predict`. The positional
array format needs reverse-engineering or templating before it can be built without a
capture step.
