# Migrated host image and video upscale wire protocol: `SPrCad`, `p0UkFb`, `jwpduf`

**Date:** 2026-09-30 · **Issue:** Refs [#914](https://github.com/ffroliva/gflow-cli/issues/914) · **Cost:** $0 for image (no credits spent); video export tested on existing clip.
**Host:** `flow.google.com` (AiSandboxAngularFrontend) · **Profile:** `jgct99` (Google AI Pro account).

## Question

On accounts Google migrated from `labs.google` to `flow.google.com` (#639), how does upscaling work for images and videos?
The legacy `aisandbox-pa.googleapis.com/v1/flow/upsampleImage` endpoint fails with HTTP 403 on migrated accounts, and `gflow image upscale` bailed at `raise_if_migrated(at="mint_recaptcha_token")` (exit 36).

## What was observed

Empirically probed on 2026-09-30 against project `263ce917-9e5a-4a07-8206-7e56a63bcdd4` (images) and `9f4b4bce-b192-4687-a636-89d4e8c5ba98` (videos).

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
