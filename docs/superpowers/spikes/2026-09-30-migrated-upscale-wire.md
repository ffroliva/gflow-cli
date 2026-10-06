# Migrated host image and video upscale wire protocol: `SPrCad`, `p0UkFb`, `jwpduf`

**Date:** 2026-09-30 · **Issue:** Refs [#914](https://github.com/ffroliva/gflow-cli/issues/914) · **Cost:** $0 for image (no credits spent); video export tested on existing clip.
**Host:** `flow.google.com` (AiSandboxAngularFrontend) · **Profile:** a contributor's Google AI Pro account (name redacted).

## Question

For accounts Flow serves `flow.google.com` rather than `labs.google` (#639), how does upscaling work for images and videos?
The legacy `aisandbox-pa.googleapis.com/v1/flow/upsampleImage` endpoint failed with HTTP 403 for the account probed here, and `gflow image upscale` bailed at `raise_if_migrated(at="mint_recaptcha_token")` (exit 36).

## What was observed

Empirically probed on 2026-09-30 against project `00000000-0000-4000-8000-000000000001` (images) and `00000000-0000-4000-8000-000000000003` (videos) (ids redacted).

### 1. Image Upscale: the `SPrCad` batchexecute RPC

The migrated Angular editor renders image detail views with a download menu containing:
- **1K (Tamanho original)**: original resolution (no RPC needed)
- **2K (Aprimorada)**: enabled on Pro/Plus accounts
- **4K (Aprimorada)**: disabled (`disabled="true"`, `class="mat-mdc-menu-item-disabled"`) with a "Fazer upgrade" CTA on Pro accounts, clickable on Ultra accounts.

Clicking **2K** issues a single synchronous batchexecute call:
```text
POST https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=SPrCad
```
Request payload carries `[media_id, 1, clientContext]`.

Response envelope (`)]}'\n...`):
```json
[["wrb.fr", "SPrCad", "[[\"metadata\"], \"<base64_encoded_jpeg>\"]"]]
```
- The response returns base64 JPEG bytes directly inside frame `[1]`.
- Measured: decoded 3,376,477 bytes, yielding a valid JPEG of dimensions `1792x2400` from an original `896x1200` image (exact 2x scale).
- Zero credits spent.

### 2. Video Export and Upscale: `p0UkFb`, `jwpduf`, and Blob Stream

On video detail views, the download menu renders:
- **720p (Tamanho original)**: original MP4 download
- **1080p (Aprimorada)**: Full HD enhanced export
- **270p (GIF animado)**: animated GIF export

Clicking **1080p**:
1. Issues submit RPC:
   ```text
   POST .../data/batchexecute?rpcids=p0UkFb
   ```
2. Frontend polls:
   ```text
   POST .../data/batchexecute?rpcids=jwpduf
   ```
   with payload requesting status for `f"{media_id}_upsampled"`.
3. When the upsampled video is ready, the frontend creates a blob stream via `URL.createObjectURL(blob)`.
- Intercepting the object URL and reading the blob yielded a `6,345,586` byte MP4.
- Frame analysis with OpenCV confirmed: `1080x1920` resolution at `24.0 FPS`, 10.00s duration (Full HD vertical video).

### 3. Tier Detection Discipline

- When an upscale option (4K image or 1080p video) is present and marked `disabled`: raise `UpscaleUnavailableError` (exit code 22).
- When a selector fails to find the menu item entirely: raise `UiSelectorDriftError` (exit code 23), distinguishing UI drift from plan limits.

## 2026-10-07 maintainer measurements

Probe: [`scripts/dev/spike_upscale_menu_anchors.py`](../../../scripts/dev/spike_upscale_menu_anchors.py)
(DOM dump of every download-menu item; `--export-1080p` for the cost arm). en-locale
account, flow.google.com.

### Menu anchors — what is locale-free

- Every item, image and video, is a bare `button[role=menuitem][mat-menu-item]` with
  children `span` + `div`. **No per-option attribute and no icon ligature** distinguishes
  one option from another.
- Image menu order (en): `1K`, `2K`, `4K` (`disabled=true`). Video menu order (en):
  `270p`, `720p`, `1080p`, `4K` (disabled).
- The 2026-09-30 pt capture above listed the video items as 720p, 1080p, 270p, with
  translated words ("2K (Aprimorada)", "Tamanho original"). So **position is not stable
  and the words are translated** — neither is an anchor.
- The **resolution token** (`1K`/`2K`/`4K`/`270p`/`720p`/`1080p`) is identical in en and pt.
  The transports therefore pick `[role="menuitem"]` items whose text contains the token as
  a whole token (`(?<![0-9A-Za-z])2K(?![0-9A-Za-z])`, case-insensitive), via
  `locator.filter(has_text=...)`. Present-but-disabled stays exit 22; absent is exit 23.

### 1080p export cost

Balance 700 before; the contributor's video e2e (1080p export) PASSED in 62.8 s; a
10-credit veo-lite control generation followed; balance after: 690. The control accounts
for the whole drop, so **the 1080p export spent 0 credits** (1 observation). The MCP tool
`gflow_upscale_video` is therefore not in `_SPEND_TOOLS`; one observation is not a
guarantee, which is why its e2e arm still sits behind `GFLOW_CLI_E2E_RUN_VIDEO=1`.
