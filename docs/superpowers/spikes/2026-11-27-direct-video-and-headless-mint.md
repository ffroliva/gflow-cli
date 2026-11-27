# Spike: direct HTTP video, and a headless reCAPTCHA mint (#1371)

**Date:** 2026-11-27 · **Profile:** one account served `flow.google.com` (build `boq_labs-ai-sandbox-frontend_20261120.00_p0`)
· **Script:** [`scripts/dev/spike_direct_video.py`](../../../scripts/dev/spike_direct_video.py)
· **Cost:** 110 credits across two accounts (228 → 138 and 50 → 30; measured clips, plus 8 for two submits a defective capture handler failed to abort, see Method), roughly 150 images of daily quota, two 1080p upscales at 0 credits
· **Raw evidence:** `scripts/dev/_spike_out/spike_direct_video_20261127_*.jsonl` (gitignored)

## Question

#1371 asks whether billed generation can go over plain HTTP with the browser kept for one job:
minting the reCAPTCHA Enterprise token. Images already do (`direct_http`,
[Gate 0](2026-11-22-direct-image-gate0.md)). This spike asks the same for video, and asks a
second question the answer depends on: does a token minted in a **headless** Chrome get accepted?

## Method

Every request is built by the spike and sent once with `httpx`; nothing is resent. The session
(live-browser cookies, `flow_wire` shell tokens), the mint (`direct_http._mint`, Flow's own
`grecaptcha.enterprise.execute` on the project page) and the request builder are the production
helpers of `direct_http`, so the result describes the request that would ship.

| Arm | RPCs | Cost |
|---|---|---|
| Reads | `as29s` with `[workflow_id]` and with `[workflow_id, null, [1]]` | $0 |
| Upscale | `p0UkFb` (1080p) → `jwpduf` → `as29s` → GET, with a pre-submit control that the `_upsampled` record does not exist | 0 credits |
| Images | 5 × `ogiZ0b` through `direct_http.run_direct_images` | quota |
| Video | `YhhmEf` (t2v) and `maseQ` + `eb1hJf` (i2v), key `macaron_cookie_{t2v,i2v}_4s_360p`, then `jwpduf` → `as29s` → GET | 4 credits each |

The image and video arms ran twice on the same profile: headed (the production launch), then
headless. Credits were read with `nzlxg` before and after each submit.

A route-aborted capture of the UI's own submits was attempted first and failed: its Playwright
route handler took a default argument, Playwright passed the request into it, the handler raised
before `route.abort()`, and both submits reached Flow (8 credits). The frames it was meant to
record are already in [FLOW_WIRE_RPCS.md](../../FLOW_WIRE_RPCS.md) (frame skeletons for `YhhmEf`
and `eb1hJf`). The handler is now built by `_abort_submits()`, which aborts before anything else.

## Results

### Direct video works end to end

| Arm | Browser | Mint | POST | Render (`jwpduf`) | Download | Credits |
|---|---|---|---|---|---|---|
| t2v `YhhmEf` | headed | 173 ms | 4.1 s | 22.0 s | 640x360 mp4, size = `DETAILS[13]` | 220 → 216 |
| i2v `maseQ` + `eb1hJf` | headed | 189 ms | 3.8 s (upload 6.7 s) | 27.5 s | 640x360 mp4, size matches | 216 → 212 |
| t2v `YhhmEf` | **headless** | 230 ms | 3.9 s | 22.0 s | 640x360 mp4, size matches | 212 → 208 |
| i2v `maseQ` + `eb1hJf` | **headless** | 226 ms | 4.5 s (upload 7.0 s) | 28.0 s | 640x360 mp4, size matches | 208 → 204 |
| upscale `p0UkFb` 1080p | headed | 243 ms | 3.5 s | 27.4 s | 1920x1080 mp4, size matches | 228 → 228 |

Frames sent (tokens elided):

- t2v: `[[[[null,null,[[["<prompt>"]]]], "<key>", 2, null, [null,null,null,null,"<uuid>","<uuid>"]]], <ctx>, ["<batch uuid>", 2]]`
- i2v item: `[[null,null,[[["<prompt>"]]]], "<key>", 2, null, [null,"<start media id>",null,null,null,[top,left,bottom,right]], [null×4,"<uuid>","<uuid>"]]`.
  The crop box is the centred crop of the start frame to the video aspect, as fractions, `0` sent
  as `null` and `1` as `1` (a 1200x630 frame into 16:9 sent `[null, 0.033333, 1, 0.966667]`).
- upscale: `[[item], <ctx>, ["<uuid>"]]`, `item` 32 slots: `[0] = [null, workflow_id]`,
  `[2] = 2` (16:9), `[4] = [null, media_id, null, null, "<uuid>"]`, `[6] = 2`,
  `[31] = "veo_3_1_upsampler_1080p"`; sent with `source-path=/project/<id>/edit/<media_id>`.
- `<ctx>` = `[null, 22, null, null, null, "<project>", null, null, null, null, ["<token>", 1]]`.

The reCAPTCHA action the page mints is `VIDEO_GENERATION` for t2v, and `UPLOAD_IMAGE` then
`VIDEO_GENERATION` for i2v (recorded by a hook on `grecaptcha.enterprise.execute`). `p0UkFb`
accepted a `VIDEO_GENERATION` token.

### Headless mints are accepted at the same rate

| Browser | `navigator.userAgent` | Direct images | Direct videos | `UNUSUAL_ACTIVITY` |
|---|---|---|---|---|
| headed | `… Chrome/158.0.0.0 …` | 5/5 (17.6–20.2 s) | 2/2 | 0 |
| headless | `… HeadlessChrome/158.0.0.0 …` | 5/5 (17.5–19.1 s) | 2/2 | 0 |

The headless page announced itself as `HeadlessChrome` and was not patched; `navigator.webdriver`
was `null` in both arms (the existing `--disable-blink-features=AutomationControlled` launch flag).

### `as29s` read forms

| Payload | URLs in the reply |
|---|---|
| `[workflow_id]` | `flow-content.google/video/<workflow_id>` (the original mp4) and `flow-content.google/image/…` |
| `[workflow_id, null, [1]]` (the form the app sends) | only `lh3.googleusercontent.com/asb/…` (the poster; see [#1153](2026-11-05-clip-url-is-a-thumbnail.md)) |

A page-free read with the bare workflow id is enough to download a finished clip.

### Parser and poll findings

- `batchexecute._UUID_RE` is anchored (`$`), so `find_record` does not see a record whose workflow
  id is `<uuid>_upsampled`. A direct upscale needs a record finder keyed on the exact id.
- `jwpduf` for a derived id that does not exist answers a **stub record** (status `4`, no media
  id), not an empty reply. "Exists" must be read as "carries a media id".

## Reading against the pre-registered table

| Observation | Reading |
|---|---|
| t2v and i2v accepted, rendered and downloaded, 2/2 headed and 2/2 headless | **GO** for a hybrid video transport |
| upscale: new `_upsampled` record, 1920x1080, credits unchanged | the whole direct chain works at $0 |
| headless = headed on every arm, 0 refusals | headless minting is viable on this account |

## Follow-up runs: cold starts, a second account, larger N, long sessions

Readings for these arms were added to the script's docstring before they ran.

### Cold start: a fresh browser's first token

Each round launches Chrome on the profile, uses the **first** token the page mints for one direct
image, and closes the browser. Headless and headed alternate which goes first (ABBA).

| Account | Rounds | Headless accepted | Headed accepted | Launch | `UNUSUAL_ACTIVITY` |
|---|---|---|---|---|---|
| A | 10 | 10/10 | 10/10 | 5.5–6.6 s | 0 |
| B | 5 | 5/5 | 5/5 | 5.3–5.7 s | 0 |

### Warm sessions, larger N

| Account | Browser | Direct images | Direct videos | Refusals |
|---|---|---|---|---|
| A | headless | 20/20 | (see soak) | 0 |
| B | headless | 10/10 | t2v accepted; i2v **refused**; i2v accepted (fresh session) | 1 (`PUBLIC_ERROR_UNUSUAL_ACTIVITY`, gRPC 7, 298 ms) |
| B | headed | — | i2v accepted | 0 |

The refused submit was not billed: the balance read before the next (headed) submit equalled the
balance before the refused one. One refusal in three headless video submits on account B, none
in the headed control and none on account A, reads as **flapping**, not as a headless-specific
rejection; N is too small to separate the two.

### Long-lived session, and a suspended machine

The first 90-minute headless soak (account A, one direct image every 5 minutes) was confounded:
two processes on different profiles stalled for identical durations (1160 s, then 2453 s), which
is a system sleep, not Flow. What it did show:

- At page ages 0.3 and 5.3 minutes both images generated, although seven session cookies
  (`SIDCC`, `__Secure-{1,3}PSIDCC`, `__Secure-{1,3}PSIDTS`, `__Secure-{1,3}PSIDRTS`) had rotated
  between them. Re-reading the browser's cookies before each request absorbs the rotation.
- After the machine resumed (page about 70 minutes old, roughly an hour of it suspended), the next
  mint produced a token Flow refused with `UNUSUAL_ACTIVITY`. No headed page was running at the
  time, so this is evidence that a **page resumed from suspension must be re-armed** (reloaded),
  not evidence about headless.

The script now asks Windows not to sleep while it runs and appends each record as it is logged.

### Long-lived session, machine kept awake

The soak was re-run on both accounts at once: account A headless, account B headed (a control on
a different account), one direct image every 5 minutes for 90 minutes, then one t2v and one i2v.

| Account | Browser | Images over 90 min | Videos on the 90-minute-old page |
|---|---|---|---|
| A | headless | 18/18 | t2v **refused** (`UNUSUAL_ACTIVITY`, not billed); i2v accepted, 640x360 |
| B | headed | 18/18 | t2v accepted; i2v accepted |

Session cookies rotated every 5 minutes in both runs (`SIDCC` and `__Secure-{1,3}PSIDCC` on every
interval, the `PSIDTS`/`PSIDRTS` pairs on alternate ones); no request was affected.

### Video A/B on fresh browsers

Six rounds on account A, each round one fresh headless and one fresh headed browser (order
alternating), each using its first token for one direct t2v (`macaron_cookie_t2v_4s_360p`).

| Browser | Accepted | Mint | Credits |
|---|---|---|---|
| headless | 6/6 | 0.48–1.29 s | 4 each |
| headed | 6/6 | 0.48–0.66 s | 4 each |

All video submits of the day: **headless 11/13, headed 11/11**. Both headless refusals came late
in a long warm session (after twelve earlier mints on one page, and on a 90-minute-old page),
none from a fresh browser; with these counts the difference is not significant. Read against the
pre-registered table: headless and headed are refused at comparable rates on equal footing, so
**headless as the default holds**, the not-billed fallback to headed covers the residual, and a
warm page needs a re-arm policy (age and use count) — a design input, not a blocker.

### Concurrency, more models, portrait

| Arm | Result |
|---|---|
| 4 direct images in flight on one headless page (account B) | 4/4 in 20.8 s wall (one image alone takes 17–21 s) |
| 8 in flight | 8/8 in 25.4 s wall (18.9 images/min), no refusal, no 429 |
| `HARBOR_SEAL` (Nano Banana 2 Lite) on direct `ogiZ0b` | generated, 768x1376, 6.4 s |
| `GEM_PIX_2` (Nano Banana Pro) on direct `ogiZ0b` | generated, 768x1376, 16.3 s |
| Portrait t2v: `macaron_cookie_t2v_4s_360p`, aspect `1` | 360x640 mp4, 4 credits |
| Veo Lite t2v: `veo_3_1_t2v_lite`, aspect `2` | 1280x720 mp4, 43.8 s render, 10 credits |

### A burst, a refused page, and idle

The idle arm (account B, headless, one image, 20 minutes idle with the machine awake, one image)
started about 30 s after the 8-in-flight run, which itself followed two model probes and 4 in
flight within about 90 s.

| Time | Page | Result |
|---|---|---|
| +0 | a fresh headless page | **refused** (`UNUSUAL_ACTIVITY`, 2.0 s) |
| +20 min | the same page | **refused** (0.6 s) |
| +20.5 min | a fresh headless page (new process) | generated |
| +20.8 min | a fresh headed page (new process) | generated |

Readings, each from one observation:

- **A burst was followed by a short refusal window** once: fourteen generations in about 90 s
  were followed by a refusal even on a fresh page. A later re-run at a faster pace did not
  reproduce it (see below), so the cause is unexplained; neither 8 in flight nor any cap is shown
  to cause or prevent it. A concurrency cap and pacing remain design inputs, not measured limits.
- **A page whose token was refused kept being refused** after 20 quiet minutes, while fresh pages
  on the same account were accepted at the same moment, in both visibilities. After a refusal,
  the retry must come from a **new page**; the headed fallback's relaunch provides one.
- Whether idle alone needs a re-arm is **unmeasured**: the burst confounded the idle arm.

### Cross-project tokens, and the burst re-run

- **A token minted on a page parked on one project was accepted for a submit to another** (3/3,
  account A, headless; the page never navigated). One armed page can serve every project.
- **The burst did not reproduce.** Two back-to-back runs of 8 in flight on account B (16 images in
  about 55 s, a faster pace than the earlier 14 in 90 s) were 16/16, and fresh headless and headed
  pages started immediately afterwards were 4/4. The earlier refusal window is therefore
  unexplained, not disproven; whether a headed retry helps right after a refusal stays
  **unmeasured**, because no refusal could be provoked.

### A UI-path observation

On account B a "redeem bonus credits" modal (a three-day streak reward) covered the project page.
It does not affect direct requests (they click nothing), and it is the likely cause of a
`create_project` click that was "visible but did not take the click" on that account the same
afternoon. Recorded for the UI transport, not measured further here.

## Not measured

- Concurrency above 8, and several warm pages on one profile.
- End frames (`nprQif`), ingredients (`MZZa6b`), extend, i2v in portrait, 4K upscale, and keys
  other than the three measured.
- macOS and Linux; accounts served labs.google (none here can reach it).
