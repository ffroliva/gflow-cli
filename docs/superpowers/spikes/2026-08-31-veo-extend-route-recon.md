# Spike evidence — Veo *extend* route, model keys, and tier gating

**Date:** 2026-08-31 · **Verdict: EXTEND IS REAL, REACHABLE, and CHEAPER than
feared — and the third-party model map is wrong in three independent ways.**
Flow sends `veo_3_1_extension_lite`, a key in a family that map never mentions.

Triggered by evaluating [`hurara210/google-flow-cli`](https://github.com/hurara210/google-flow-cli),
which hardcodes:

```python
EXTEND_MODEL_MAP = {
    "landscape": "veo_3_1_extend_fast_landscape_ultra",
    "portrait":  "veo_3_1_extend_fast_portrait_ultra",
    "square":    "veo_3_1_extend_fast_square_ultra",
}
```

Every one of those is wrong for this account: `square` does not exist, `_ultra`
is `SERVICE_TIER_ADVANCED`-only, and the whole `extend_fast_*` family is not
what Flow's own UI actually sends.

This repo had **never** captured an extend request (`samples/captured/` has no
extend file), so the claim was untested hearsay. It is now captured, live.

## Method

Four instruments. Two failed, and the failures are the load-bearing part.

| # | Instrument | Cost | Result |
|---|---|---|---|
| 1 | Bundle-grep, landing page | 0 | **INCONCLUSIVE** — positive control failed |
| 2 | Bundle-grep, mounted editor | 0 | **INCONCLUSIVE** — control failed again |
| 3 | Response logger over editor load + bundle source-slice | 0 | Control passed; model table + route name |
| 4 | **Drive Flow's own UI to extend a clip**, log request + response | **10 credits** | Decisive |

The positive control is `veo_3_1_lite` / `_fast` / `_quality` — keys we provably
send in production. Runs 1–2 harvested 41 bundles / 14.5 MB and found **none**
of them, which per the #539 rule is instrument failure, not absence.

That double failure is itself a finding: **Flow's video model keys are
server-supplied, not client-hardcoded.** Bundle-grep can never answer "what is
model key X". Source of truth is
`GET labs.google/fx/api/trpc/flow.projectInitialData` — **a route this repo
already calls** (`client.py:2454`) and whose 64-model capability list we discard.

Run 4 let **Flow's own UI** compose the request rather than guessing from the
table, which is the only way to learn which key Flow picks. That mattered: the
answer was not derivable from the table.

Raw captures (gitignored): `scripts/dev/_spike_out/spike_extend_route_capture_*.json`,
`initialdata_raw_*.json`, `spike_extend_submit_capture_*.json`.

## Result — the full extend family

Eight orderable models, **two families**. An earlier pass of this spike listed
only the first six because it filtered on the substring `extend` — which does
not match `extension`. The family Flow actually uses was invisible to that
filter. Corrected:

| Key | Aspect | Gen | ADVANCED | INTERMEDIATE | ENTRY |
|---|---|---|---|---|---|
| `veo_3_1_extend_fast_landscape` | LANDSCAPE | 330s | UNAVAILABLE | 20 | 20 |
| `veo_3_1_extend_fast_landscape_ultra` | LANDSCAPE | 330s | 10 | UNAVAILABLE | UNAVAILABLE |
| `veo_3_1_extend_fast_portrait` | PORTRAIT | 330s | UNAVAILABLE | 20 | 20 |
| `veo_3_1_extend_fast_portrait_ultra` | PORTRAIT | 330s | 10 | UNAVAILABLE | UNAVAILABLE |
| `veo_3_1_extend_landscape` | LANDSCAPE | 270s | 100 | 100 | 100 |
| `veo_3_1_extend_portrait` | PORTRAIT | 270s | 100 | 100 | 100 |
| **`veo_3_1_extension_lite`** | **LANDSCAPE + PORTRAIT** | **110s** | **5** | **10** | **10** |
| `veo_3_1_extension_lite_low_priority` | LANDSCAPE + PORTRAIT | 110s | 0 | UNAVAILABLE | UNAVAILABLE |

All are 8s output with `requirements: [[VIDEO_REQUIREMENT_TEXT,
VIDEO_REQUIREMENT_EXTENSION]]` and `inputSpec.maxInputV2vVideoDuration: 8`.

- **Flow's UI chose `veo_3_1_extension_lite`** — cheapest available at this
  tier, fastest, and the only aspect-agnostic entry. Not the `extend_fast_*`
  family at all.
- **`_ultra` is a tier selector** meaning `SERVICE_TIER_ADVANCED`, not decoration.
- **No SQUARE anywhere** in either family.
- `veo_3_1_extend_*_relaxed` variants live only in a display-name lookup with no
  `creditMapping` — a cross-cohort superset. Not orderable; not models.
- The UI label is **not** the key. The menu renders "Extend (Veo 3.1 - Lite)",
  and the six `extend_*` keys carry no displayName at all. Never map label→key.
  (Flow also leaks a raw `Extend ({{modelName}})` i18n placeholder in that menu.)

This account: `serviceTier: SERVICE_TIER_INTERMEDIATE`,
`paygateTier: PAYGATE_TIER_ONE`, 1025 credits before the run.

## Result — the wire

`POST https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoExtendVideo`

```jsonc
{
  "mediaGenerationContext": {
    "batchId": "<uuid>",
    "audioFailurePreference": "RETURN_SILENCED_VIDEOS",
    "sceneContext": { "sceneId": "<uuid>", "position": 1 }
  },
  "clientContext": {
    "projectId": "<uuid>", "tool": "PINHOLE",
    "userPaygateTier": "PAYGATE_TIER_ONE",
    "sessionId": ";1788200574949",
    "recaptchaContext": { "token": "...", "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB" }
  },
  "requests": [{
    "aspectRatio": "VIDEO_ASPECT_RATIO_LANDSCAPE",
    "textInput": { "structuredPrompt": { "parts": [{ "text": "<extension prompt>" }] } },
    "videoModelKey": "veo_3_1_extension_lite",
    "seed": 2164,
    "metadata": { "sceneId": "<uuid>" },
    "videoInput": { "mediaId": "<source uuid>", "startFrameIndex": 1, "endFrameIndex": 24 }
  }],
  "useV2ModelConfig": true
}
```

Response `200`, same shape as the video routes we already parse:

```jsonc
{
  "remainingCredits": 1015,
  "workflows": [{ "name": "<workflow uuid>", "metadata": { "primaryMediaId": "...", "batchId": "..." } }],
  "media": [{
    "name": "<new media uuid>", "workflowId": "...", "workflowStepId": "CAE",
    "mediaMetadata": {
      "mediaStatus": { "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_SCHEDULED" },
      "requestData": { "videoGenerationRequestData": { "videoModelControlInput": {
        "videoModelName": "models/veo-3.1-lite-generate-002;backend_beyond",
        "videoGenerationMode": "VIDEO_GENERATION_MODE_VIDEO_EXTENSION",
        "videoModelCapabilities": ["VIDEO_MODEL_CAPABILITY_EXTEND"]
      }}}
    },
    "video": { "generatedVideo": { "model": "veo_3_1_extension_lite", "seed": 2164 } }
  }]
}
```

~~Poll `name` through the existing `batchCheckAsyncVideoGenerationStatus`.~~
**CORRECTION (predict council, same day): there is no such poller.** Only the
constant (`routes.py:37`) and a parser (`api/video.py:609`) exist — grep shows
**zero consumers** in `src/`. The production video poller
(`ui_automation_video.py:1131`) passively scans *Flow's own* captured status
traffic and assumes the SPA is on-screen polling for us; a direct-wire submit
gives Flow's UI no reason to poll our media id, so that poller would time out
at 600 s having seen nothing. `PLAN.md` ADR-14 retired the HTTP status path as
"401-dead" alongside `generate_video`. **A direct-wire extend requires writing
a poller from scratch** (shape: copy `_poll_concat_until_done`, `client.py:1817`).
This was the single biggest omission in this doc's first draft.

**Cost confirmed by observation, not inference: 1025 → 1015 = exactly 10 credits.**

### Verified rendered — and the output is a *Scene*, not a clip

Re-opening the project after the run shows a new library tile:
`Untitled Scene 08-31 18:22:25` carrying a **2**-clip badge (timestamp matches
the request). The extend completed, and **Flow materialised the result as a
Scene containing [original clip, extension]** — which is what
`sceneContext.position: 1` was declaring.

That closes the design loop: **extend produces a scene; turning a scene into one
continuous .mp4 is the credit-free server-side concat we already ship**
(`client.py:1895`, `gflow scene`). A long-video feature is chained extends
feeding the existing concat, not a new rendering path.

Incidental confirmation while probing: a raw `fetch` to
`batchCheckAsyncVideoGenerationStatus` from page context with
`credentials: 'same-origin'` returns **401 CREDENTIALS_MISSING**. aisandbox-pa
takes a Bearer and ignores cookies — exactly as `api/client.py`'s module
docstring and `transports/experimental/evaluate_fetch.py` already state. Poll
through the client's own auth path, never a hand-rolled fetch.

## What this changes

1. **Do not port the third-party map.** Resolve the key at runtime from
   `projectInitialData`: filter `requirements` for `VIDEO_REQUIREMENT_EXTENSION`,
   intersect with the account's `serviceTier`, pick by `creditMapping` cost.
   That reproduces Flow's own choice and survives cohort/tier differences.
2. **`videoInput` is a frame range, not a clip.** `startFrameIndex: 1`,
   `endFrameIndex: 24` — the extend seeds from a window of the source media.
   Any long-video design must carry frame indices, not just a mediaId.
3. **Extends are scene-anchored.** `sceneContext.{sceneId,position}` plus
   `metadata.sceneId`. This rides on our existing scene layer, not beside it.
4. **The economics are ~4× better than the earlier estimate in this doc.**
   10 credits and 110s per 8s segment, not 20 and 330s. A 40s video is
   4 extends ≈ 40 credits and ~7 min serial, on a 1015 balance.
5. **`PAYGATE_TIER_ONE` confirmed live.** Flow's own request carries the same
   value `image_upscale.py:44` hardcodes. Our constant is right.
6. ~~**WAF tension stands.** Chained extends are bursty.~~ **CORRECTED by the
   predict council (Security, Performance and Devil's Advocate, independently):
   chained extends are the *safest* multi-submit surface in the product.** 110 s
   of generation per segment physically floors the submit interval at the top of
   the measured-safe band — a 15-segment run is ~5.5 submits/10 min against the
   measured 403 trigger of ~14/10 min (#241), and #241's *passing* band was
   45–120 s apart. The residual risks are (a) **regularity**, not rate — near-exact
   110 s intervals are a cleaner machine fingerprint than random ones, and
   `chain.py:283` defaults `jitter=0.0`; and (b) the **poll** loop, where a 2 s
   default would fire ~825 requests at a WAF-scored host over 15 segments instead
   of ~75.

## RESOLVED — our own stack CAN submit extends (2026-08-31, +10 credits)

`scripts/dev/spike_extend_ourstack_verify.py --variant scene --submit` replayed
the captured body through **`client._post_json`** with a token from **our own
`TokenMinter`** (`action="VIDEO_GENERATION"`). Strict one-variable A/B: identical
body, identical media, identical scene position — only the transport and the
token source differed.

**Result: HTTP 200.** `remainingCredits` 1015 → 1005 (10 credits),
`mediaGenerationStatus: MEDIA_GENERATION_STATUS_SCHEDULED`,
`videoGenerationMode: VIDEO_GENERATION_MODE_VIDEO_EXTENSION`,
`videoModelKey: veo_3_1_extension_lite` echoed back. Flow does **not** cross-check
the model key against the session tier in a way that rejects a tier-legal key.

### This corrects `[[rest-path-capability-matrix]]`

`docs/CHARACTER.md` records "**generation is never browser-free**; Bearer fixed
the 401, not the reCAPTCHA wall" — generalised from a single 403 on
`batchGenerateImages` (2026-06-02). The wall is **route-specific, not universal**:

| Route | Self-assembled POST + minted token | Date |
|---|---|---|
| `batchGenerateImages` | **403** | 2026-06-02 |
| `upsampleImage` | **200** (shipped, live) | — |
| `batchAsyncGenerateVideoExtendVideo` | **200** | 2026-08-31 |

Two of three generative routes accept our minted token. The blanket claim should
be narrowed to the route it was measured on. Worth a separate cheap re-test of
`batchGenerateImages` — if that wall has also lifted since June, UI automation
may no longer be required on the image hot path either. **Not chased here.**

## Seam quality VERIFIED — and the frame indices are decoded (2026-09-01)

The whole value proposition is that the join is invisible. It is.

Concatenated the 3-clip test scene through the existing **credit-free**
`client.concatenate_scene` → 23.02s, 1280x720, **24 fps**, h264 + aac.

- **Video:** frames at 7.9s and 8.1s (either side of a true extend seam) are
  continuous — same sun position, sky, beach texture and grade, with the wave
  *advanced* rather than restarted. A continuation, not a cut.
- **Audio:** mean volume 5s −31.0 dB · 7s −28.8 · **8s (seam) −25.8** · 10s −30.5.
  No dropout, no gap; the seam window is loudest because the wave breaks there.
- Caveat: ambient ocean noise is forgiving. Narration or music across a seam is
  untested.

### `startFrameIndex: 1, endFrameIndex: 24` = exactly 1.0 second

The clip is **24 fps** (`r_frame_rate=24/1`), so the captured 1..24 window is
precisely one second — not an opaque integer. An 8s clip is 192 frames, so the
request seeds from a **1-second window**, not the whole clip. Still open: whether
index 1 is measured from the head or the tail. The unit, however, is settled.

> ⚠️ Reading this test scene: positions 1 and 2 are two *alternative* extensions
> of the SAME source clip (one from Flow's UI, one from our stack), not a chain.
> Only the **8s seam** is a true extend seam; the 16s one is a testing artifact.

## Still unresolved
- How `startFrameIndex`/`endFrameIndex` should be chosen when chaining beyond
  the first extend (extend the tail of the new clip, or of the original?).
- The `VIDEO_EDITOR_EXTEND` surface vs the `SCENE_BUILDER_EXTEND_SUBMITTED` one
  captured here — two entry points, possibly two body shapes.
