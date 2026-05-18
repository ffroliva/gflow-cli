# Design: Video Generation via UiAutomationTransport

**Date:** 2026-05-18
**Status:** Revised post-council — wire format verified against committed captures
**Branch:** `feat/ui-automation-onboarding-bypass` (captures + this revision on `chore/video-wire-captures`)

---

## 0. Revision note (2026-05-18)

This spec was revised after a 5-dimension council review and after capturing and
verifying the real wire format. The original draft made two foundational errors:

1. It assumed all three video modes shared one `batchAsyncGenerateVideoText`
   endpoint. **Wrong** — each mode has its own endpoint (§2.1).
2. It proposed sending images via a `videoGenerationImageInputs` request array.
   **Wrong** — that array is a response-only echo; requests carry mode-specific
   `startImage`/`endImage`/`referenceImages` fields (§2.3).

The status enum, error shape, and upload wire were also corrected. Every wire
claim below is now backed by a sanitized capture committed under
`samples/captured/`:

| Capture | Mode / purpose |
|---|---|
| `02_batchAsyncGenerateVideoText.json` | T2V generate |
| `08_batchAsyncGenerateVideoStartAndEndImage.json` | I2V generate |
| `09_batchAsyncGenerateVideoReferenceImages.json` | R2V generate |
| `10_batchCheckAsyncVideoGenerationStatus_successful.json` | terminal SUCCESSFUL |
| `11_batchCheckAsyncVideoGenerationStatus_failed.json` | terminal FAILED |
| `01_upload_image.json` | image upload |

Design decisions surfaced by the council are tracked in §10.

---

## 1. Problem Statement

All `aisandbox-pa.googleapis.com` generation endpoints return HTTP 401 when
called from the existing HTTP transports (evaluate_fetch, bearer, sapisidhash).
This was confirmed e2e on 2026-05-18 for both image generation
(`batchGenerateImages`) and video generation. The `UiAutomationTransport` is the
sole working path for generation — it drives the real browser so requests carry
correct auth context. Currently it only handles image generation; video
generation is unimplemented.

---

## 2. Wire Format (verified)

### 2.1 Three mode-specific endpoints

The three modes do **not** share one endpoint. Each has its own route under
`https://aisandbox-pa.googleapis.com/v1/`:

| Mode | Endpoint | `videoModelKey` | Capture |
|---|---|---|---|
| T2V | `video:batchAsyncGenerateVideoText` | `veo_3_1_t2v_{tier}_{aspect}` | `02` |
| I2V (Frames) | `video:batchAsyncGenerateVideoStartAndEndImage` | `veo_3_1_interpolation_lite` | `08` |
| R2V (Elementos) | `video:batchAsyncGenerateVideoReferenceImages` | `veo_3_1_r2v_lite` | `09` |

Status polling: `video:batchCheckAsyncVideoGenerationStatus` (all modes).
Image upload: `v1/flow/uploadImage`.

### 2.2 Shared request envelope

All three generate endpoints take the same envelope; only `requests[0]` differs.
Content-type is `text/plain;charset=UTF-8` (not `application/json`) — same quirk
as the image transports.

```jsonc
{
  "mediaGenerationContext": {
    "batchId": "<uuid>",
    "audioFailurePreference": "BLOCK_SILENCED_VIDEOS"
  },
  "clientContext": {
    "projectId": "<uuid>",
    "tool": "PINHOLE",
    "userPaygateTier": "PAYGATE_TIER_ONE",
    "sessionId": "<session marker>",
    "recaptchaContext": { "token": "<recaptcha>", "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB" }
  },
  "requests": [ { /* see 2.3 */ } ],
  "useV2ModelConfig": true
}
```

### 2.3 Per-request image fields — sent, not echoed

`requests[0]` always carries `aspectRatio`
(`VIDEO_ASPECT_RATIO_PORTRAIT|LANDSCAPE`), `textInput.structuredPrompt.parts[].text`,
`videoModelKey`, `metadata: {}`, and `seed` (int). The image inputs are
**mode-specific** — there is no `videoGenerationImageInputs` array in a request:

```jsonc
// T2V — no image field

// I2V  (capture 08)
"startImage": { "mediaId": "<uuid>", "cropCoordinates": { "top": 0.0, "left": 0.0, "bottom": 1.0, "right": 1.0 } },
"endImage":   { "mediaId": "<uuid>", "cropCoordinates": { ... } }   // optional; start-only validity is unconfirmed (§10 Q5)

// R2V  (capture 09)
"referenceImages": [ { "mediaId": "<uuid>", "imageUsageType": "IMAGE_USAGE_TYPE_ASSET" } ]
```

`cropCoordinates` are normalized floats (0.0–1.0). A programmatic caller that
does not crop must still send full-frame `{top:0,left:0,bottom:1,right:1}`.

**`videoGenerationImageInputs` is a response-only artifact.** It appears under
`media[].mediaMetadata.requestData.videoGenerationRequestData` as the server's
normalized echo — never in a request. Note the request enum
`IMAGE_USAGE_TYPE_ASSET` is echoed back normalized to `IMAGE_USAGE_TYPE_ASSET_IMAGE`.

### 2.4 uploadImage

`POST v1/flow/uploadImage`, content-type `text/plain;charset=UTF-8`:

```jsonc
// request
{ "clientContext": { "projectId": "<uuid>", "tool": "PINHOLE" },
  "imageBytes": "<base64>", "isUserUploaded": true, "isHidden": false,
  "mimeType": "image/png", "fileName": "<name>" }
// response → the asset id to reference downstream is response.media.name
```

`page.request.post` from the Playwright browser context returns 200 — session
cookies are attached automatically. One observed upload was ~1.5 MB of base64
(see §5.2 for the size-limit requirement).

### 2.5 Status lifecycle

`video:batchCheckAsyncVideoGenerationStatus`, request
`{ "media": [{ "name": "<mediaUuid>", "projectId": "<uuid>" }] }`.

Observed `mediaMetadata.mediaStatus.mediaGenerationStatus` values:

```
PENDING | SCHEDULED  ->  ACTIVE  ->  SUCCESSFUL | FAILED
```

`PENDING` was seen for T2V right after submit, `SCHEDULED` for I2V/R2V — treat
both as "not yet running". The original draft's `QUEUED` and `COMPLETED` do
**not** exist on the wire; the terminal success value is `SUCCESSFUL`.

A `FAILED` media carries a structured reason, and its `visibility` flips from
`PRIVATE` to `FILTERED`:

```jsonc
"mediaStatus": {
  "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_FAILED",
  "error": { "code": 3, "message": "PUBLIC_ERROR_IP_INPUT_IMAGE" },
  "failureReasons": ["IP_PROHIBITED"]
}
```

### 2.6 Generate response & the poll handle

The generate response returns `media[0].name` — the UUID to poll. T2V responses
also include a top-level `operations[]` array; I2V/R2V responses do not (just
`workflows[]` + `media[]`). In all modes **`media[0].name` is the poll handle**.
`remainingCredits` is returned in both generate and status responses.

Generated video bytes are **not** inlined in the status response; download is a
separate `media.getMediaUrlRedirect` call (out of scope — see §3).

---

## 3. Scope

**In:** T2V, I2V (start + optional end), R2V (multiple reference images). All via
`UiAutomationTransport`. Polling blocks until terminal status.

**Out of scope:** voice elements (experimental, different domain model); video
download (a separate `media.getMediaUrlRedirect` call — note this is *not* the
image `fife_url` pattern; the status response carries no video URL); Veo
tier/quality selection beyond Fast/Lite.

Implementation is split into phases — see §10.4.

---

## 4. Domain Changes — `src/gflow_cli/api/video.py`

### 4.1 Mode enum

```python
class Mode(StrEnum):
    T2V = "t2v"
    I2V = "i2v"   # Frames
    R2V = "r2v"   # Elementos / Reference-to-Video
```

### 4.2 `GenerateVideoRequest`

The original draft *derived* `mode` from which fields were populated, which lets
a frozen value object represent contradictory state (e.g. both reference images
and a start frame). Make `mode` explicit and validate in `__post_init__`:

```python
@dataclass(frozen=True)
class CropBox:
    top: float = 0.0
    left: float = 0.0
    bottom: float = 1.0
    right: float = 1.0

@dataclass(frozen=True)
class ImageRef:
    media_id: str            # an uploaded asset UUID (uploadImage -> media.name)
    crop: CropBox = CropBox()

@dataclass(frozen=True)
class GenerateVideoRequest:
    prompt: str
    mode: Mode = Mode.T2V
    aspect: Aspect = Aspect.PORTRAIT
    tier: Tier = Tier.FAST
    seed: int | None = None
    # I2V
    start_image: ImageRef | None = None
    end_image: ImageRef | None = None
    # R2V
    reference_images: tuple[ImageRef, ...] = ()

    def __post_init__(self) -> None:
        if self.mode is Mode.T2V and (self.start_image or self.end_image or self.reference_images):
            raise ValueError("T2V request must not carry image inputs")
        if self.mode is Mode.I2V and self.start_image is None:
            raise ValueError("I2V request requires a start_image")
        if self.mode is Mode.I2V and self.reference_images:
            raise ValueError("I2V request must not carry reference_images")
        if self.mode is Mode.R2V and not self.reference_images:
            raise ValueError("R2V request requires at least one reference image")
```

The legacy `start_asset_uuid` field from the original draft is **dropped** — the
HTTP transports are 401-dead, so "HTTP transport parity" is moot (§10 Q3).

### 4.3 Endpoint + model-key selection

```python
_ENDPOINT = {
    Mode.T2V: "video:batchAsyncGenerateVideoText",
    Mode.I2V: "video:batchAsyncGenerateVideoStartAndEndImage",
    Mode.R2V: "video:batchAsyncGenerateVideoReferenceImages",
}
# captured wire constants — see samples/captured/08, 09
_I2V_MODEL_KEY = "veo_3_1_interpolation_lite"
_R2V_MODEL_KEY = "veo_3_1_r2v_lite"

def model_key(req: GenerateVideoRequest) -> str:
    if req.mode is Mode.I2V:
        return _I2V_MODEL_KEY
    if req.mode is Mode.R2V:
        return _R2V_MODEL_KEY
    return f"veo_3_1_t2v_{req.tier.value}_{req.aspect.value}"
```

The I2V/R2V model keys carry no tier/aspect segment, but `aspect` is still sent
as the separate `aspectRatio` request field and is honored by the server.

### 4.4 `build_generate_body()`

Builds the §2.2 envelope plus the §2.3 mode-specific `requests[0]`:

```python
def _image(ref: ImageRef) -> dict:
    return {
        "mediaId": ref.media_id,
        "cropCoordinates": {
            "top": ref.crop.top, "left": ref.crop.left,
            "bottom": ref.crop.bottom, "right": ref.crop.right,
        },
    }

def _request_entry(req: GenerateVideoRequest) -> dict:
    entry: dict = {
        "aspectRatio": req.aspect.wire,                 # VIDEO_ASPECT_RATIO_*
        "textInput": {"structuredPrompt": {"parts": [{"text": req.prompt}]}},
        "videoModelKey": model_key(req),
        "metadata": {},
    }
    if req.seed is not None:
        entry["seed"] = req.seed
    if req.mode is Mode.I2V:
        entry["startImage"] = _image(req.start_image)
        if req.end_image is not None:
            entry["endImage"] = _image(req.end_image)
    elif req.mode is Mode.R2V:
        entry["referenceImages"] = [
            {"mediaId": r.media_id, "imageUsageType": "IMAGE_USAGE_TYPE_ASSET"}
            for r in req.reference_images
        ]
    return entry
```

`clientContext` (projectId, sessionId, recaptcha token) and
`mediaGenerationContext.batchId` are injected by the transport, which owns the
browser/session — `video.py` stays pure (no I/O, no frameworks).

### 4.5 `VideoStatus` value object

The poll result. A frozen VO mirroring §2.5:

```python
@dataclass(frozen=True)
class VideoStatus:
    media_id: str
    status: str                       # MEDIA_GENERATION_STATUS_*
    failure_reasons: tuple[str, ...] = ()
    error_message: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {"MEDIA_GENERATION_STATUS_SUCCESSFUL",
                               "MEDIA_GENERATION_STATUS_FAILED"}
    @property
    def succeeded(self) -> bool:
        return self.status == "MEDIA_GENERATION_STATUS_SUCCESSFUL"
```

---

## 5. Transport Changes — `UiAutomationTransport`

### 5.1 New public method

```python
async def generate_video(
    self,
    *,
    request: GenerateVideoRequest,
    out_dir: Path | None = None,
    poll_timeout_s: float = 600.0,
) -> VideoStatus:
```

Raises `RuntimeError` if `setup()` was not called. Acquires `_generate_lock`
(shared with `generate_images` — same Page, and image/video calls must serialize
against each other since they drive the same DOM). Returns a terminal
`VideoStatus`, or raises per §7.

### 5.2 Upload helper — `_upload_asset`

```python
@staticmethod
async def _upload_asset(page: Page, project_id: str, image_path: Path) -> str:
    """Upload a local image into the project. Returns the asset UUID (media.name)."""
```

Builds the §2.4 body and POSTs to `v1/flow/uploadImage` via `page.request.post`
with content-type `text/plain;charset=UTF-8`. Returns `response["media"]["name"]`.

**Input validation is mandatory** (council security finding): reuse the bar set
by `client.py:upload_image` — reject non-existent paths, enforce
`MAX_IMAGE_BYTES`, run the magic-byte header check *before* reading the whole
file, and `resolve()` the path. Without this, `_upload_asset` would base64 and
POST any local file under the user's Google session.

### 5.3 Submit path

With the wire verified, the flow is:

1. For I2V/R2V, upload each local image via `_upload_asset` (§5.2) → asset UUIDs.
   (Callers may also pass already-uploaded asset UUIDs directly.)
2. Build the mode-specific generate body (§4.4).
3. Switch the Flow UI into the correct mode (Video tab → Frames/Elementos
   sub-tab) — see §6.
4. Submit, and capture the `batchAsyncGenerateVideo*` response (§5.4).

**Open decision (§10 Q1):** whether step 4 drives the UI's own submit button (and
listens for the response) or POSTs the generate endpoint directly via
`page.request.post`. The original draft's "Strategy B" (direct POST) is no longer
blocked by the disproven DOM-chip theory, but it requires minting a fresh
reCAPTCHA token outside the UI. The council recommends **mirroring the existing
image-generation transport**, which drives the UI and attaches a response
listener — adopt that path unless a planning spike shows the direct POST is
simpler and reliable. The two-strategies-then-delete-one plan is dropped: pick
one path in planning.

### 5.4 Response capture

```python
@staticmethod
def _attach_video_response_listener(page: Page, *, project_id: str | None = None) -> list[dict]:
    """Register page.on('response') for the batchAsyncGenerateVideo* routes.
    Mirrors _attach_batch_response_listener for images. Returns the capture list."""
```

The listener must match all three generate routes (§2.1), filtered by
`project_id` to avoid cross-project capture.

### 5.5 Polling — `_poll_video_status`

```python
@staticmethod
async def _poll_video_status(
    page: Page,
    media_name: str,
    project_id: str,
    *,
    timeout_s: float = 600.0,
    poll_interval_s: float = 5.0,
) -> VideoStatus:
```

**Active polling** is the primary mechanism (council finding): call
`video:batchCheckAsyncVideoGenerationStatus` via `page.request.post` on a fixed
interval. This is deterministic and independent of whether the Flow SPA keeps
firing its own status requests (Chromium throttles timers in backgrounded tabs,
so passive interception cannot be the sole path).

Terminal handling per §2.5: `SUCCESSFUL` → return `VideoStatus`; `FAILED` →
return a `VideoStatus` carrying `failure_reasons`/`error_message` so the caller
(§7) can map it; timeout → raise `TimeoutError` with `media_name`, last observed
status, and elapsed time, plus a debug screenshot.

Default timeout is 600 s (Veo generations can exceed 5 minutes);
env-configurable via `GFLOW_CLI_VIDEO_POLL_TIMEOUT`.

---

## 6. UI Selectors

Mode-switch selectors are needed regardless of the §5.3 decision (the UI must be
in the right mode before submit). Icon-first cascades are preferred (locale-stable):

```python
VIDEO_MODE_TAB_SELECTORS = (
    "button:has(i:text('play_circle'))",
    "[role='tab']:has-text('Video')",
)
FRAMES_SUBTAB_SELECTORS   = ("[role='tab']:has-text('Frames')", "button:has-text('Frames')")
ELEMENTOS_SUBTAB_SELECTORS = ("[role='tab']:has-text('Elements')", "button:has-text('Elements')")
```

`setup()` already forces `locale="en-US"` and `?hl=en`, so Portuguese selector
variants from the original draft are dropped as dead weight.

The catalog/frame-attachment selectors (`Inicial`/`Final`/`+`/upload) are only
needed if images are attached through the UI. If the §5.3 decision is to attach
via the `uploadImage` API + request body, those selectors are unnecessary —
defer them to whichever submit path planning selects.

Selectors in §6 are unverified guesses against the live Flow DOM. Planning must
include a selector-validation smoke script (analogous to
`scripts/smoke_worker_style.py`) run against live Flow before the transport unit
tests are written.

---

## 7. Error Handling

| Condition | Raised error |
|---|---|
| `setup()` not called | `RuntimeError` |
| generate/upload/status → 401 | `AuthExpiredError` |
| generate/upload/status → 403 | `WafRejectionError` |
| Upload non-200 | `WireFormatError` |
| Mode tab not found | `RuntimeError` (debug screenshot to `out_dir`) |
| No terminal status within timeout | `TimeoutError` |
| Status `FAILED` | mapped by `failureReasons` — see below |

A `FAILED` status must **not** be blanket-mapped to `ContentPolicyError`. The
status carries `failureReasons[]` and `error.message` (§2.5); map by reason:

| `failureReasons` value | Raised error | Rationale |
|---|---|---|
| `IP_PROHIBITED` (intellectual-property block on an input image) | `ContentPolicyError` *or* a dedicated `InputImageRejectedError` | Not fixable by softening the prompt — distinct remediation |
| content / safety rejections | `ContentPolicyError` | Prompt-softening remediation applies |
| quota / rate signals | `RateLimitError` | Retry-able (exit 4) |
| anything else / unknown reason | `WireFormatError` with the raw reason in `discovery=` | Don't guess |

Only `IP_PROHIBITED` is observed so far (capture `11`). The full reason
vocabulary is unknown — planning should treat unrecognised reasons as
`WireFormatError` and log the raw `error`/`failureReasons` via structlog so the
taxonomy can be extended from real data.

---

## 8. Testing Strategy

TDD, decomposed into Red→Green→Commit increments (not a big-bang):

1. **`video.py` domain** — `Mode`, `CropBox`, `ImageRef`, `GenerateVideoRequest`
   + `__post_init__` validation, `model_key`, `build_generate_body`,
   `VideoStatus`. Pure, no I/O — must hit the 90% `api/` coverage floor. Assert
   bodies byte-match the captured request shapes in `samples/captured/02,08,09`.
2. **`_upload_asset`** — mock `page.request.post`; verify base64 encoding,
   headers, the §5.2 validation guards, and `media.name` extraction.
3. **`_attach_video_response_listener`** — mirrors
   `test_attach_batch_response_listener`.
4. **`_poll_video_status`** — drive it with the captured status JSON
   (`samples/captured/10`, `11`): SCHEDULED→ACTIVE→SUCCESSFUL happy path,
   FAILED-with-`failureReasons` path, timeout path.
5. **mode switching** — selector-cascade fallback (decompose into
   individually-mockable helpers so unit coverage is reachable).
6. **`generate_video`** — orchestration, pre-`setup()` guard, `_generate_lock`.

**E2E** (`tests/e2e/test_video_ui_automation_e2e.py`, opt-in via
`GFLOW_CLI_E2E_PROFILE`): T2V, I2V (start+end), R2V (≥2 references). E2E
fixtures: a second `test_assets/` image is needed for R2V.

Mark the existing HTTP-transport I2V e2e tests (`tests/e2e/test_video_i2v_e2e.py`)
`xfail` — 401 is confirmed.

> The original "implement both strategies and delete the weaker" plan is
> dropped: a strategy choice that no automated test enforces is not TDD. Pick
> one submit path in planning (§5.3) and test only that.

---

## 9. Files Changed

| File | Change |
|---|---|
| `src/gflow_cli/api/video.py` | `Mode`, `CropBox`, `ImageRef`, rewritten `GenerateVideoRequest` + validation, `_ENDPOINT`, `model_key()`, `build_generate_body()`, `VideoStatus` |
| `src/gflow_cli/api/transports/ui_automation.py` *(or a new `ui_automation_video.py` — see council architecture note: this file is already over the 800-line cap)* | `generate_video()`, `_upload_asset()`, mode switching, `_attach_video_response_listener()`, `_poll_video_status()` |
| `src/gflow_cli/cli_video.py` | R2V/Elementos + end-frame flags, or an explicit follow-up phase (§10.4) |
| `tests/api/test_video.py` | domain unit tests vs captured bodies |
| `tests/api/transports/test_ui_automation.py` | transport unit tests |
| `tests/e2e/test_video_ui_automation_e2e.py` | new e2e file |
| `tests/e2e/test_video_i2v_e2e.py` | mark HTTP-transport tests `xfail` |
| `samples/captured/0[89]_*, 1[01]_*` | committed (this branch) |
| `PLAN.md` | phase entries (§10.4) |
| `KNOWN_ISSUES.md` | already updated (2026-05-18) |

---

## 10. Decisions & Open Questions

### 10.1 Resolved by the captures

- **Endpoints / wire shape** — settled (§2). Each mode has its own endpoint;
  requests carry `startImage`/`endImage`/`referenceImages`, not
  `videoGenerationImageInputs`.
- **Status enum** — `PENDING|SCHEDULED → ACTIVE → SUCCESSFUL|FAILED` (§2.5).
- **`FAILED` reason** — `failureReasons[]` + `error.message` exist; error mapping
  is reason-based (§7).

### 10.2 Council recommendations adopted in this revision

- **Drop "Strategy B as a parallel implementation"** — pick one submit path in
  planning (§5.3), do not ship two.
- **Active polling** is primary, not passive interception (§5.5).
- **Drop `start_asset_uuid`** legacy field (§4.2).
- **`mode` is explicit + validated**, not derived (§4.2).

### 10.3 Open — must be answered before/within planning

1. **Submit path (§5.3):** drive the UI submit + response listener, or direct
   `page.request.post` of the generate endpoint? Hinges on reCAPTCHA-token
   minting outside the UI. Recommendation: mirror the image transport; confirm
   with a spike.
2. **reCAPTCHA token source:** how the image transport obtains/refreshes its
   token, and whether the same path is reusable for the video submit.
3. **Credit-cost guard:** I2V is ~10 credits/video; T2V/R2V costs are
   unconfirmed. Recommendation: echo a one-line Rich cost estimate before submit
   with a `--yes` skip — not a hard block.
4. **`end_image`-only / start-only I2V:** is a start-only I2V request valid, or
   is `endImage` effectively required? Capture `08` had both; needs e2e
   confirmation.
5. **`FAILED` reason vocabulary:** only `IP_PROHIBITED` is observed; the rest of
   the enum is unknown (§7).

### 10.4 Recommended phase split

The council flagged the original single-phase scope as too large. Suggested
split (each gets a `PLAN.md` entry):

- **Phase A — T2V.** `video.py` domain changes + active polling + mode switch +
  `generate_video` for T2V. No uploads, no image inputs. Smallest shippable
  slice.
- **Phase B — I2V + R2V.** `_upload_asset` (with validation), image-input bodies,
  Frames/Elementos UI handling, `cli_video.py` flags + a BDD feature file.
- **Phase C — spike (optional).** Direct-`page.request.post` submit path, only if
  Phase A/B planning shows it is worthwhile.
