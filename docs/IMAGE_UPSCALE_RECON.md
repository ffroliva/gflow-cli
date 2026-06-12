# Image Upscale — Reverse-Engineering Recon

> Status: **recon complete, pre-implementation**. Wire empirically captured 2026-06-11
> (denon82 Pro account, project `ffb768fb…`). Spikes: `scripts/dev/spike_image_upscale_drive.py`
> (UI drive + capture) and `scripts/dev/spike_image_upscale_rest_probe.py` (schema + REST probe).

## Feature

Flow's image editor download menu offers **1K Original / 2K Upscaled / 4K Upscaled**. gflow
today saves only the 1K original (the pre-signed `fifeUrl` from the generation response). This
feature adds `gflow image upscale <mediaId> --scale 2k|4k` to fetch the upscaled variant.

Scope: **platform-generated images only** (referenced by `mediaId`). Uploaded images are not
supported by this endpoint. **4K is Ultra-tier-gated**; Pro accounts see "Upgrade" in the UI.

## Wire protocol

### Endpoint
```
POST https://aisandbox-pa.googleapis.com/v1/flow/upsampleImage
```
Same host + auth class as image generation (`batchGenerateImages`).

### Request body
```json
{
  "mediaId": "<source-image-uuid>",
  "targetResolution": "UPSAMPLE_IMAGE_RESOLUTION_2K",
  "clientContext": {
    "recaptchaContext": {
      "token": "<reCAPTCHA-Enterprise-token>",
      "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB"
    },
    "projectId": "<project-that-owns-the-media>",
    "sessionId": ";<epoch_ms>",
    "tool": "PINHOLE",
    "userPaygateTier": "PAYGATE_TIER_ONE"
  }
}
```
- `targetResolution` enum: `UPSAMPLE_IMAGE_RESOLUTION_2K` | `UPSAMPLE_IMAGE_RESOLUTION_4K`.
  1K = original (no API call — gflow already has it).
- **The FULL `clientContext` is required.** A minimal `{recaptchaContext}` body returns
  **403 even with a valid token** (confirmed by live smoke). All five fields are needed;
  `projectId` (the project owning the media) is load-bearing.
- `sessionId` format is `;<epoch_ms>` (e.g. `;1781190457842`), same as image generation.
- `userPaygateTier` is **where the account tier surfaces on the wire** (`PAYGATE_TIER_ONE`
  on a Pro account). It is client-reported telemetry — the server enforces the real tier
  independently (a non-Ultra 4K request 403s regardless), so it is not a security control.

### reCAPTCHA action — `IMAGE_GENERATION`
The token MUST be minted with action **`IMAGE_GENERATION`** (uppercase). reCAPTCHA Enterprise
scores by action; the guessed `"upsampleImage"` scored low and 403'd. Captured live by hooking
`grecaptcha.enterprise.execute` (`scripts/dev/spike_image_upscale_recaptcha_action.py`). The
site key is page-discovered, so it auto-matches; minting on the bootstrap page is fine — the
action was the only gap. **Live-verified 2026-06-11**: a 2K upscale wrote a 3.8 MB JPEG.

### Response
```json
{ "encodedImage": "<base64>" }
```
- Single field. ~3.8–5.1 MB base64 observed (varies by source image). Decode → write image bytes.
- Synchronous: one call returns the result. **No async poll/status loop** (unlike video gen).

## Transport decision — browser required, REST is dead

| Approach | Viable? | Evidence |
|---|---|---|
| Pure REST / Bearer-only | ❌ No | Bearer `ya29` + **no** reCAPTCHA token → **403** (probed with and without `clientContext`) |
| Browser reCAPTCHA-mint + POST | ✅ Yes | UI drive returns 200; reuses the existing `batchGenerateImages` transport |
| Hybrid (browser submit + Bearer poll) | ⚠️ N/A | No poll/download phase exists — response is synchronous inline base64 |

**Conclusion:** reCAPTCHA token is **mandatory**. Reuse the existing image-generation reCAPTCHA
minting path; there is no Bearer-only slice to peel off. See [REST-path capability matrix] — a
generative-class op stays reCAPTCHA-gated.

## Tier gating (4K = Ultra)

- 4K is Ultra-only. On Pro, the UI renders "Fazer upgrade" (Upgrade) instead of a clickable 4K.
- Account tier is **NOT** in `GET /fx/api/auth/session` (only `user` / `expires` / `access_token`).
  It surfaces via the upsell recommendations (`fetchUserRecommendations` onramp list) and is
  enforced server-side. A 4K request on a non-Ultra account is expected to 403 (permission), which
  must be distinguished from a WAF/reCAPTCHA 403.
- Session staleness is real: an idle session returns `error: ACCESS_TOKEN_REFRESH_NEEDED` and
  re-auths before the call succeeds — so a freshly-upgraded account may need a session refresh
  before 4K unlocks.

## UI navigation facts (for the spike/transport)

- **Cold-loading `/project/<pid>/edit/<mediaId>` 500s** ("Algo deu errado"). Must land on the
  gallery `/project/<pid>` then click the `a[href*=<mediaId>]` tile (in-app SPA routing).
- A "What's new" changelog modal overlays the gallery but does **not** block the href tile click.
- Working selectors (locale leaks into the UI — match language-neutral tokens):
  - Download button: `button:has(i.google-symbols:text-is('download'))`
  - Scale menu item: `[role='menuitem']:has-text('2K')` (and `'4K'`)
  - pt labels are 1K="Tamanho original", 2K/4K="Aumentada" — **never** match on "Aumentada".

## Implementation sketch (from `/gflow:predict` GO, 7.5/10)

- `api/routes.py`: `UPSAMPLE_IMAGE = f"{FLOW_API_BASE}/flow/upsampleImage"`.
- `api/image.py`: frozen `UpsampleImageRequest(media_id, target_resolution, recaptcha_token="")`;
  `TargetResolution` enum with `from_cli("2k"|"4k")`.
- `api/client.py`: `upsample_image()` — mint reCAPTCHA (existing path), POST, validate + decode
  `encodedImage` with `MAX_UPSAMPLE_B64_LEN ≈ 50 MB` cap + `del` + PNG/JPEG magic-byte check
  (mirror `concatenate_scene`).
- `cli_image.py`: `gflow image upscale <mediaId> --scale 2k|4k [--out]`.
- `errors.py`: `UpscaleUnavailableError` (exit 22), distinct from WAF/reCAPTCHA 403; **no auto-retry**
  on a 4K 403.
- Never log `encodedImage`. Record the output via `OperationRecorder` honoring the redaction gate.
- Discover mediaIds via the existing `gflow data list images`.

## Credit cost

Image operation → expected **credit-free** (only video generation spends credits). Not yet
stress-confirmed; treat as free but do not batch aggressively (per-profile WAF heat).
