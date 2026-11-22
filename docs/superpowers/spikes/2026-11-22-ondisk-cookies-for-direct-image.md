# Spike: do on-disk cookies work for direct `ogiZ0b` image generation?

**Date:** 2026-11-22 · **Profile:** `denon82` (Flow served `flow.google.com`)
· **Base:** `chore/spike-direct-image-with-browser-token`
· **Cost:** 1 image (image quota, zero credits)
· **Script:** [`scripts/dev/spike_ondisk_cookies_ogiz0b.py`](../../../scripts/dev/spike_ondisk_cookies_ogiz0b.py)
· **Raw evidence:** `scripts/dev/_spike_out/spike_ondisk_cookies_ogiz0b_*.jsonl` (gitignored)

## Question

The existing direct wire path (`flow_wire.py`) reads cookies from the Chrome profile's
SQLite database via `browser_cookie3` for `nzlxg` (credits) and `pGCYOe` (trash). These
complete in <5 s. Does `ogiZ0b` (image generation, 15–20 s server-side render) also
accept those same on-disk cookies — or does it need cookies from a live browser session?

## Pre-registered readings

| Outcome | Reading |
|---|---|
| HTTP 200 + image records | on-disk cookies work; browser needed ONLY for reCAPTCHA |
| HTTP 200 + code 7 (UNUSUAL_ACTIVITY) | cookies ok but reCAPTCHA needs same session |
| HTTP 401 | on-disk cookies rejected |
| HTTP 400 / code 3 | cookies ok but frame or tokens mismatched |

## Method

1. Read 26 cookies from the Chrome profile's SQLite DB via `read_cookies_for()` —
   the same function `flow_wire._execute_direct_rpc` uses.
2. Fetched shell tokens (`at`, `fsid`, `bl`) with a `GET https://flow.google.com/`
   using those on-disk cookies — HTTP 200, tokens extracted.
3. Opened a browser ONLY for reCAPTCHA minting (`IMAGE_GENERATION` action), then
   closed it.
4. Loaded the captured frame template, swapped prompt and token.
5. POSTed via `httpx` using **only** the on-disk cookies and shell-derived `at` token.
   The browser's live cookies were never extracted for the POST.

## Results (one run)

| Measure | Value |
|---|---|
| **Disk cookies read** | 26 |
| **Shell GET status** | 200 |
| **HTTP status (ogiZ0b POST)** | 200 |
| **Elapsed** | 15.29 s |
| **ogiZ0b frames** | 1 |
| **RPC errors** | 0 |
| **Image records** | 1 |
| **Prompt match** | yes (new prompt, different from the template's) |
| **Dimensions** | 768 x 1376 |
| **Image download** | 200, 985,184 bytes, JPEG |

## Readings

1. **On-disk cookies work for `ogiZ0b`.** The same `read_cookies_for()` path that
   serves credits reads and media mutations also serves image generation. The browser
   is not needed for cookies — only for reCAPTCHA minting.

2. **The shell GET works with on-disk cookies too.** The `at`, `fsid`, and `bl` tokens
   were obtained from a plain `httpx.get("https://flow.google.com/")` with disk cookies,
   not from the browser's `WIZ_global_data`. This means the entire auth chain
   (cookies → shell tokens → batchexecute POST) works without a browser.

3. **The browser's only remaining role is reCAPTCHA.** `grecaptcha.enterprise.execute()`
   needs a real browser page — it evaluates JS in the page context. Everything else
   (cookies, `at` token, the POST itself) works from disk + httpx.

## What was not measured

- Whether the reCAPTCHA token can be minted in a **minimal** page (e.g. a blank page
  that loads only the reCAPTCHA Enterprise script, without Flow's full Angular app).
- Whether the token is bound to the same profile whose cookies are used (it was here,
  but cross-profile is untested).
- Repeated sends, WAF escalation, and cookie freshness window (the existing memory
  `direct-read-window-is-psidts-age` says the window is ~1.5–2.5 h after a browser visit).
- Whether this works on macOS (where `read_cookies_for` is skipped because browser_cookie3
  can't decrypt the macOS Keychain).

## Architecture implication

The full direct image generation path is now proven end-to-end:

```
[on-disk cookies] → GET flow.google.com/ → at/fsid/bl tokens
[browser page]    → grecaptcha.enterprise.execute() → reCAPTCHA token
[captured frame]  → swap prompt + token → httpx POST → image
```

The browser is needed only for the reCAPTCHA mint. No selector interaction, no
contenteditable, no button clicks for the generation itself.
