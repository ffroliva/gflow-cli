# Design: Video Generation via UiAutomationTransport

**Date:** 2026-05-18  
**Status:** Awaiting council review  
**Branch:** `feat/ui-automation-onboarding-bypass`

---

## 1. Problem Statement

All `aisandbox-pa.googleapis.com` generation endpoints return HTTP 401 when called from
the existing HTTP transports (evaluate_fetch, bearer, sapisidhash). This was confirmed
e2e on 2026-05-18 for both image generation (`batchGenerateImages`) and video generation
(`batchAsyncGenerateVideoText`). The `UiAutomationTransport` is the sole working path for
generation — it drives the real browser so requests carry correct auth context. Currently
it only handles image generation; video generation is unimplemented.

---

## 2. Wire Format Evidence

Three distinct video modes confirmed from HAR captures (labs.google8–10.har):

| Mode | Model key | `videoGenerationMode` | Capability | Image input type |
|---|---|---|---|---|
| T2V | `veo_3_1_t2v_{tier}_{aspect}` | `TEXT_TO_VIDEO` | — | none |
| I2V (Frames) | `veo_3_1_interpolation_lite` | `IMAGE_TO_VIDEO` | `START_AND_END_IMAGE` | `START_IMAGE` / `END_IMAGE` |
| R2V (Elementos) | `veo_3_1_r2v_lite` | `REFERENCE_TO_VIDEO` | `MULTI_REFERENCE` | `ASSET_IMAGE` (array) |

All three share the same `batchAsyncGenerateVideoText` endpoint. Image inputs are always in
`videoGenerationImageInputs: [{mediaGenerationId, imageUsageType, mediaId}]` where
`mediaGenerationId == mediaId` (both set to the asset UUID).

`uploadImage` returns 200 when called from the Playwright browser context
(`page.request.post`) — auth is carried by the browser's session cookies. This is the
upload mechanism for attaching local files.

Status lifecycle: `QUEUED → ACTIVE → COMPLETED | FAILED`.

---

## 3. Scope

**In:** T2V, I2V (Frames — start + optional end), R2V (Elementos — multiple reference
images). All via `UiAutomationTransport`. Polling blocks until terminal status.

**Out of scope:** voice elements (experimental, different domain model), video download
(caller responsibility, same pattern as image `fife_url`), Veo tier/quality selection
beyond Fast/Lite (future knob).

---

## 4. Domain Changes — `src/gflow_cli/api/video.py`

### 4.1 New Mode enum value

```python
class Mode(StrEnum):
    T2V = "t2v"
    I2V = "i2v"   # Frames
    R2V = "r2v"   # Elementos / Reference-to-Video
```

### 4.2 Extended `GenerateVideoRequest`

```python
@dataclass(frozen=True)
class GenerateVideoRequest:
    prompt: str
    aspect: Aspect = Aspect.PORTRAIT
    tier: Tier = Tier.FAST
    # Frames mode (I2V)
    start_frame_id: str | None = None   # asset UUID → IMAGE_USAGE_TYPE_START_IMAGE
    end_frame_id: str | None = None     # asset UUID → IMAGE_USAGE_TYPE_END_IMAGE
    # Elementos mode (R2V)
    reference_image_ids: list[str] = dataclasses.field(default_factory=list)
    # Legacy — kept for HTTP transport parity, maps to start_frame_id internally
    start_asset_uuid: str | None = None

    @property
    def mode(self) -> Mode:
        if self.reference_image_ids:
            return Mode.R2V
        if self.start_frame_id or self.end_frame_id or self.start_asset_uuid:
            return Mode.I2V
        return Mode.T2V
```

### 4.3 Updated `model_key()`

```python
def model_key(mode: Mode, tier: Tier, aspect: Aspect) -> str:
    if mode == Mode.I2V:
        return "veo_3_1_interpolation_lite"
    if mode == Mode.R2V:
        return "veo_3_1_r2v_lite"
    return f"veo_3_1_{mode.value}_{tier.value}_{aspect.value}"
```

### 4.4 Updated `build_generate_body()`

Add `videoGenerationImageInputs` construction:

```python
def _image_inputs(req: GenerateVideoRequest) -> list[dict]:
    inputs = []
    start = req.start_frame_id or req.start_asset_uuid
    if start:
        inputs.append({"mediaGenerationId": start, "imageUsageType": "IMAGE_USAGE_TYPE_START_IMAGE", "mediaId": start})
    if req.end_frame_id:
        inputs.append({"mediaGenerationId": req.end_frame_id, "imageUsageType": "IMAGE_USAGE_TYPE_END_IMAGE", "mediaId": req.end_frame_id})
    for uid in req.reference_image_ids:
        inputs.append({"mediaGenerationId": uid, "imageUsageType": "IMAGE_USAGE_TYPE_ASSET_IMAGE", "mediaId": uid})
    return inputs
```

Wire: include `"videoGenerationImageInputs": _image_inputs(req)` in `requests[0]` when
non-empty. Add `"videoModelCapabilities"` to `videoModelControlInput` based on mode:
- I2V: `["VIDEO_MODEL_CAPABILITY_START_AND_END_IMAGE"]`
- R2V: `["VIDEO_MODEL_CAPABILITY_MULTI_REFERENCE"]`

---

## 5. Transport Changes — `UiAutomationTransport`

### 5.1 New public method

```python
async def generate_video(
    self,
    *,
    request: GenerateVideoRequest,
    out_dir: Path | None = None,
    poll_timeout_s: float = 300.0,
) -> VideoStatus:
```

Raises `RuntimeError` if setup() not called. Acquires `_generate_lock` (shared with
`generate_images` — same Page, same serialization requirement).

Returns `VideoStatus` with `status == "MEDIA_GENERATION_STATUS_COMPLETED"` or raises
`TimeoutError` / `ContentPolicyError` (FAILED status).

### 5.2 Upload helper

```python
@staticmethod
async def _upload_asset(page: Page, project_id: str, image_path: Path) -> str:
    """Upload a local image into the project via page.request.post.
    Returns the asset media UUID (media.name).
    """
```

Reads the file, base64-encodes it, POSTs to `aisandbox-pa.googleapis.com/v1/flow/uploadImage`
via `page.request.post(url, data=json_body, headers={"content-type": "text/plain;charset=UTF-8"})`.
The browser's session cookies are automatically attached. Returns `response_body["media"]["name"]`.

### 5.3 Video mode navigation

**Two strategies — both implemented, verified live, weaker one removed:**

**Strategy A — UI-driven attachment** (primary):
```
_enter_editor(page)
→ click Video tab
→ click Frames or Elementos sub-tab  
→ for each image: click Inicial/Final/+ → catalog opens → upload or select
→ _send_prompt(page, prompt)
```
Relies on Playwright file_chooser for uploads inside the catalog dialog. Fragile if
selectors change but requires no knowledge of internal React state.

**Strategy B — HTTP-upload + prompt-area injection** (candidate):
```
_enter_editor(page)
→ _upload_asset(page, project_id, image_path) → uuid
→ click Video tab + sub-tab
→ _send_prompt(page, prompt)  [without image chip; rely on batchAsyncGenerateVideoText body]
```
Skip the catalog entirely; the video body is built programmatically with the uploaded
asset UUIDs. The UI prompt area shows no image chip, but the network request body is
correct. **Only valid if Flow server accepts the request regardless of the UI chip state.**
Must be verified e2e — if the server requires the chip to be in the DOM before the submit
button fires the correct body, Strategy B fails silently.

### 5.4 Response capture

```python
@staticmethod
def _attach_video_response_listener(page: Page, *, project_id: str | None = None) -> list[dict]:
    """Register page.on('response') for batchAsyncGenerateVideoText.
    Same pattern as _attach_batch_response_listener for images.
    Returns the shared capture list.
    """
```

### 5.5 Polling

```python
@staticmethod
async def _poll_video_status(
    page: Page,
    media_name: str,
    project_id: str,
    *,
    timeout_s: float = 300.0,
    poll_interval_s: float = 5.0,
) -> VideoStatus:
```

Intercepts browser's own `batchCheckAsyncVideoGenerationStatus` responses (passive) OR
calls the endpoint via `page.request.post()` (active). Both are implemented; the passive
approach is preferred since the Flow UI already polls and we just listen.

Terminal conditions: `MEDIA_GENERATION_STATUS_COMPLETED` → return `VideoStatus`.
`MEDIA_GENERATION_STATUS_FAILED` → raise `ContentPolicyError`. Timeout → raise `TimeoutError`.

---

## 6. New Selectors Required

```python
# Mode switcher
VIDEO_MODE_TAB_SELECTORS = (
    "button:has(i:text('play_circle'))",
    "button:has-text('Vídeo')",
    "button:has-text('Video')",
    "[role='tab']:has-text('Vídeo')",
    "[role='tab']:has-text('Video')",
)

FRAMES_SUBTAB_SELECTORS = (
    "button:has-text('Frames')",
    "[role='tab']:has-text('Frames')",
)

ELEMENTOS_SUBTAB_SELECTORS = (
    "button:has-text('Elementos')",
    "button:has-text('Elements')",
    "[role='tab']:has-text('Elementos')",
)

# Image attachment in Frames mode
INITIAL_FRAME_SELECTORS = (
    "button:has-text('Inicial')",
    "button:has-text('Initial')",
    "button:has-text('Start')",
)

FINAL_FRAME_SELECTORS = (
    "button:has-text('Final')",
    "button:has-text('End')",
)

# Add element button in Elementos mode (the "+" chip adder)
ADD_ELEMENT_SELECTORS = (
    "button[aria-label*='Add']",
    "button:has(i:text('add'))",
)

# Catalog upload button
CATALOG_UPLOAD_SELECTORS = (
    "button:has-text('Faça upload')",
    "button:has-text('Upload')",
    "[aria-label*='upload' i]",
)
```

---

## 7. Error Handling

| Condition | Raised error |
|---|---|
| `setup()` not called | `RuntimeError` |
| `batchAsyncGenerateVideoText` → 401 | `AuthExpiredError` |
| `batchAsyncGenerateVideoText` → 403 | `WafRejectionError` |
| Status → `FAILED` | `ContentPolicyError` |
| No response within timeout | `TimeoutError` |
| Upload fails (non-200) | `WireFormatError` |
| Mode tab not found | `RuntimeError` (debug screenshot written to `out_dir`) |

---

## 8. Testing Strategy

**Unit tests** (in `tests/api/transports/test_ui_automation.py`):
- `_upload_asset` — mock `page.request.post`, verify base64 encoding + headers
- `_switch_to_video_mode` — selector fallback cascade
- `_attach_video_response_listener` — mirrors `test_attach_batch_response_listener`
- `_poll_video_status` — QUEUED→ACTIVE→COMPLETED happy path; FAILED path; timeout path
- `generate_video` protocol conformance — signature, pre-setup guard

**E2E tests** (in `tests/e2e/test_video_ui_automation_e2e.py`):
- T2V: enter editor → switch to video → submit prompt → poll → `VideoStatus.succeeded`
- I2V Frames: upload test image → attach as Inicial → submit → poll
- R2V Elementos: upload 2 test images → attach as references → submit → poll
- Strategy A vs Strategy B verification (both run; weaker one identified and removed)

All e2e tests marked `@pytest.mark.e2e`, opt-in via `GFLOW_CLI_E2E_PROFILE`.

---

## 9. Files Changed

| File | Change |
|---|---|
| `src/gflow_cli/api/video.py` | Add `Mode.R2V`, extend `GenerateVideoRequest`, update `model_key()` and `build_generate_body()` |
| `src/gflow_cli/api/transports/ui_automation.py` | Add `generate_video()`, `_upload_asset()`, `_switch_to_video_mode()`, `_attach_video_response_listener()`, `_poll_video_status()`, new selector constants |
| `tests/api/transports/test_ui_automation.py` | Unit tests for all new methods |
| `tests/e2e/test_video_ui_automation_e2e.py` | New e2e test file |
| `tests/e2e/test_video_i2v_e2e.py` | Mark HTTP CV1/CV2/CV3 tests `xfail` (401 confirmed) |
| `PLAN.md` | Add phase entry for this feature |
| `KNOWN_ISSUES.md` | Already updated (2026-05-18) |

---

## 10. Open Questions for Council Review

1. **Strategy A vs B precedence**: Should the transport try Strategy B first (faster, no UI
   navigation) and fall back to Strategy A on failure — or always use A?
2. **Polling method**: Passive interception of browser-fired status requests vs active
   `page.request.post()` polling — both need e2e verification before deciding.
3. **`start_asset_uuid` deprecation**: The legacy field on `GenerateVideoRequest` is kept
   for HTTP transport parity. Should it be deprecated with a warning, or silently mapped?
4. **Credit cost guard**: Frames (I2V) costs 10 credits per video (confirmed from HAR).
   R2V and T2V costs differ. Should the CLI warn before submitting?
