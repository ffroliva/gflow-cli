# Design: Video Generation via UiAutomationTransport

**Date:** 2026-05-18
**Status:** Revised (rev 2) — verified against the committed captures *and* the existing image-generation transport
**Branch:** `feat/ui-automation-onboarding-bypass` (captures + revisions on `chore/video-wire-captures`)

---

## 0. Revision history

**Rev 0 (original draft).** Awaiting council review.

**Rev 1 (post council review 1).** Corrected the wire format against captured
HARs: three mode-specific endpoints (not one), `videoGenerationImageInputs` is a
response-only echo, real status enum, `failureReasons`-based error mapping.

**Rev 2 (post council review 2) — this revision.** Council re-review found the
rev-1 §4/§5 code did not match the real `video.py`, and that the phase split
treated Phase A as independent of an unresolved submit mechanism. Reading
`src/gflow_cli/api/transports/ui_automation.py` resolved it:

> `UiAutomationTransport` **drives the browser UI**. Flow's own JavaScript
> builds the generate request, sends it, and mints the reCAPTCHA token on every
> prompt submission — `UiAutomationTransport.refresh_auth()` is a documented
> no-op for exactly this reason (`ui_automation.py:865-875`). The transport
> never constructs or POSTs a generate body.

Consequences folded into this revision:

- `video.py:build_generate_body()` / `model_key()` are **HTTP-transport
  machinery** (the `bearer`/`sapisidhash`/`evaluate_fetch` strategies, all
  401-dead for generation). The video feature on `UiAutomationTransport` does
  **not** use them — so they are out of scope here (§3). This dissolves the
  rev-1 defects around `Aspect.wire`, the `model_key` signature, body field
  ownership, and `dict[str, Any]` typing — there is no body to build.
- The submit-path open questions are **resolved**: video generation mirrors
  `generate_images` — drive the UI, capture the response (§5, §10.1).
- `video.py`'s role for this feature narrows to **value objects + response
  parsers** (§4).

Every wire claim is backed by a sanitized capture committed under
`samples/captured/`:

| Capture | Mode / purpose |
|---|---|
| `02_batchAsyncGenerateVideoText.json` | T2V generate |
| `08_batchAsyncGenerateVideoStartAndEndImage.json` | I2V generate |
| `09_batchAsyncGenerateVideoReferenceImages.json` | R2V generate |
| `10_batchCheckAsyncVideoGenerationStatus_successful.json` | terminal SUCCESSFUL |
| `11_batchCheckAsyncVideoGenerationStatus_failed.json` | terminal FAILED |
| `01_upload_image.json` | image upload |

---

## 1. Problem Statement

All `aisandbox-pa.googleapis.com` generation endpoints return HTTP 401 from the
HTTP transports (evaluate_fetch, bearer, sapisidhash). Confirmed e2e on
2026-05-18 for both image and video generation. `UiAutomationTransport` — which
drives a real Chromium browser via Playwright — is the only working generation
path. It currently handles image generation only; video generation is
unimplemented.

---

## 2. Wire Format (verified — for reference)

This section documents the *observed* wire so the response parsers (§4.4) and
e2e assertions are grounded. **The transport does not build these request
bodies** — Flow's JavaScript does (§0). The request shapes are documented for
understanding and for e2e verification; the **response/status shapes are what
this feature actually parses.**

### 2.1 Three mode-specific endpoints

Each mode has its own route under `https://aisandbox-pa.googleapis.com/v1/`:

| Mode | Generate endpoint (fired by Flow's UI) | `videoModelKey` | Capture |
|---|---|---|---|
| T2V | `video:batchAsyncGenerateVideoText` | `veo_3_1_t2v_{tier}_{aspect}` | `02` |
| I2V (Frames) | `video:batchAsyncGenerateVideoStartAndEndImage` | `veo_3_1_interpolation_lite` | `08` |
| R2V (Elementos) | `video:batchAsyncGenerateVideoReferenceImages` | `veo_3_1_r2v_lite` | `09` |

Status polling: `video:batchCheckAsyncVideoGenerationStatus` (all modes).
Image upload (when Flow's catalog uploads a local file): `v1/flow/uploadImage`.

### 2.2 Generate request shape (produced by Flow's UI)

For reference only. All three share an envelope
(`mediaGenerationContext`, `clientContext` with `recaptchaContext.token`,
`useV2ModelConfig: true`); only `requests[0]` differs by mode:

```jsonc
// I2V  (capture 08) — startImage required, endImage optional
"startImage": { "mediaId": "<uuid>", "cropCoordinates": { "top": 0.0, "left": 0.0, "bottom": 1.0, "right": 1.0 } },
"endImage":   { "mediaId": "<uuid>", "cropCoordinates": { ... } }
// R2V  (capture 09)
"referenceImages": [ { "mediaId": "<uuid>", "imageUsageType": "IMAGE_USAGE_TYPE_ASSET" } ]
```

`cropCoordinates` are normalized floats the Flow UI sets when a human drags the
crop box; driven without dragging, the UI applies a default frame. **The CLI
does not model crop** (§10.2). `videoGenerationImageInputs` appears only in
*responses* as a normalized server echo — never in a request.

### 2.3 Status request/response

`video:batchCheckAsyncVideoGenerationStatus` — request body is **just**
`{ "media": [{ "name": "<mediaUuid>", "projectId": "<uuid>" }] }`, with **no**
`clientContext` and **no** `recaptchaContext` (captures 10/11). This matters:
the status endpoint needs no reCAPTCHA token, so it can be polled directly via
`page.request.post` (§5.5).

Observed `mediaMetadata.mediaStatus.mediaGenerationStatus` wire values (all
carry the `MEDIA_GENERATION_STATUS_` prefix):

```
MEDIA_GENERATION_STATUS_PENDING | MEDIA_GENERATION_STATUS_SCHEDULED
   ->  MEDIA_GENERATION_STATUS_ACTIVE
   ->  MEDIA_GENERATION_STATUS_SUCCESSFUL | MEDIA_GENERATION_STATUS_FAILED
```

`PENDING` was seen for T2V right after submit, `SCHEDULED` for I2V/R2V — both
mean "not yet running". `QUEUED` and `COMPLETED` do **not** exist on the wire;
the terminal success value is `SUCCESSFUL`.

A `FAILED` media carries a structured reason; its `visibility` flips
`PRIVATE → FILTERED`:

```jsonc
"mediaStatus": {
  "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_FAILED",
  "error": { "code": 3, "message": "PUBLIC_ERROR_IP_INPUT_IMAGE" },
  "failureReasons": ["IP_PROHIBITED"]
}
```

(`error.code` is an unexplained integer — captured but not consumed by this
design; error mapping keys on `failureReasons`/`error.message`, §7.)

### 2.4 The poll handle

Both generate and status responses carry `media[0].name` — the UUID to poll.
T2V generate responses also carry a top-level `operations[]` array; I2V/R2V do
not. Status responses additionally carry `media[].video.operation.name`. In all
cases `media[0].name` is the handle. `remainingCredits` is returned in generate
and status responses. Generated video bytes are not inlined — download is a
separate `media.getMediaUrlRedirect` call (out of scope, §3).

---

## 3. Scope

**In:** T2V, I2V (start + optional end image), R2V (one or more reference
images) on `UiAutomationTransport` — by driving the Flow video editor UI and
capturing the response, mirroring `generate_images`. Polling blocks until
terminal status.

**Out of scope:**
- `video.py:build_generate_body()` / `model_key()` — HTTP-transport machinery
  for the 401-dead `bearer`/`sapisidhash`/`evaluate_fetch` strategies. The
  committed captures document the correct multi-endpoint shape if that path is
  ever revived, but this feature does not touch it.
- Video download (separate `media.getMediaUrlRedirect` call — note this is
  *not* the image `fife_url` pattern; the status response carries no video URL).
- Crop control (the UI default frame is used — §10.2).
- Reusing pre-existing project assets by UUID (v1 attaches local files only;
  asset reuse is a future extension).
- Voice elements; Veo tier/quality beyond Fast/Lite.

Implementation is split into a spike + two phases — see §10.3.

---

## 4. Domain Changes — `src/gflow_cli/api/video.py`

For this feature `video.py` provides **value objects and pure response
parsers** — no body building. The existing `build_generate_body()`,
`model_key()` and the module wire constants are left untouched (§3).

### 4.1 `Mode` — add `R2V`

The existing enum has `T2V`/`I2V`; add `R2V`:

```python
class Mode(StrEnum):
    T2V = "t2v"
    I2V = "i2v"   # Frames
    R2V = "r2v"   # Elementos / Reference-to-Video
```

### 4.2 `GenerateVideoRequest` — explicit mode, image inputs, validation

The existing class derives `mode` from `start_asset_uuid` and is T2V/I2V-only.
Replace it with an explicit-`mode`, validated value object. Image inputs are
**local file paths** — the transport attaches them through Flow's catalog UI:

```python
@dataclass(frozen=True)
class GenerateVideoRequest:
    prompt: str
    mode: Mode = Mode.T2V
    aspect: Aspect = Aspect.PORTRAIT
    tier: Tier = Tier.FAST            # applies to T2V only — see 4.3
    seed: int | None = None
    start_image: Path | None = None        # I2V
    end_image: Path | None = None          # I2V (optional)
    reference_images: tuple[Path, ...] = ()  # R2V

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("prompt must not be empty")
        if self.mode is Mode.T2V and (self.start_image or self.end_image or self.reference_images):
            raise ValueError("T2V request must not carry image inputs")
        if self.mode is Mode.I2V:
            if self.start_image is None:
                raise ValueError("I2V request requires start_image")
            if self.reference_images:
                raise ValueError("I2V request must not carry reference_images")
        if self.mode is Mode.R2V:
            if not self.reference_images:
                raise ValueError("R2V request requires at least one reference image")
            if self.start_image or self.end_image:
                raise ValueError("R2V request must not carry start/end images")
        if len(self.reference_images) > MAX_REFERENCE_IMAGES:
            raise ValueError(f"at most {MAX_REFERENCE_IMAGES} reference images")
        if self.seed is not None and not (0 <= self.seed <= 2**31 - 1):
            raise ValueError("seed out of range")
```

`MAX_REFERENCE_IMAGES` — a conservative cap (e.g. 3; confirm against Flow's UI
during the §10.3 spike). The legacy `start_asset_uuid` field and the derived
`mode` property are removed. `tier` is retained but only meaningful for T2V
(I2V/R2V keys are fixed `_lite` — §2.1); document this on the field.

> `Tier` currently has `FAST`/`QUALITY`; "Lite" in §2.1 is a *model-key* segment,
> not a tier the caller picks. No `Tier` change needed.

### 4.3 Aspect note

`Aspect` (`PORTRAIT`/`LANDSCAPE`/`SQUARE`) is reused as-is. The transport uses it
to drive the editor's aspect-ratio control (as `generate_images` does via
`_configure_generation_settings`) — not to build a wire string. **`SQUARE` is
unverified for video** (captures show only `PORTRAIT`/`LANDSCAPE`); the §10.3
spike should confirm whether the video editor offers it.

### 4.4 `VideoStatus` value object + status parser

```python
@dataclass(frozen=True)
class VideoStatus:
    media_id: str
    status: str                       # MEDIA_GENERATION_STATUS_*
    failure_reasons: tuple[str, ...] = ()
    error_message: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            "MEDIA_GENERATION_STATUS_SUCCESSFUL",
            "MEDIA_GENERATION_STATUS_FAILED",
        }

    @property
    def succeeded(self) -> bool:
        return self.status == "MEDIA_GENERATION_STATUS_SUCCESSFUL"


def parse_video_status(response_json: dict, *, media_id: str) -> VideoStatus:
    """Pure parser over a batchCheckAsyncVideoGenerationStatus response body.
    Shapes: samples/captured/10 (SUCCESSFUL), 11 (FAILED)."""
```

A second pure helper extracts the poll handle from a generate response:

```python
def media_name_from_generate_response(response_json: dict) -> str:
    """Return media[0].name. Shapes: samples/captured/02, 08, 09."""
```

Both are pure (no I/O) and tested directly against the captured JSON.

---

## 5. Transport Changes

The video methods live in a **new module** `src/gflow_cli/api/transports/ui_automation_video.py`
mixed into `UiAutomationTransport` — `ui_automation.py` is already ~900 lines,
over the 800-line cap, so video logic must not be added inline. The mixin
shares `self._page` and `self._generate_lock` with the host class.

### 5.1 `generate_video`

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
(shared with `generate_images` — same Page, same DOM; image and video calls must
serialize against each other). Mirrors `_generate_images_locked`:

```
_enter_editor(page)
  -> switch to Video mode + Frames/Elementos sub-mode for request.mode   (§5.2)
  -> configure aspect ratio
  -> for I2V/R2V: attach image(s) via the catalog UI                     (§5.3)
  -> _attach_video_response_listener(page, project_id)                   (§5.4)
  -> _send_prompt(page, request.prompt)        # Flow's JS builds+sends+mints reCAPTCHA
  -> _await_captured(...) -> media_name_from_generate_response(...)
  -> _poll_video_status(page, media_name, project_id, ...)               (§5.5)
```

### 5.2 Mode switching

Switch the editor into the video mode before submit: click the Video tab, then
the Frames (I2V) or Elementos (R2V) sub-tab. Selectors in §6.

### 5.3 Image attachment (I2V / R2V)

For I2V/R2V the local image files must be attached through Flow's catalog UI so
that Flow's JS includes them in the generate request it builds. The transport
drives the catalog's file picker (Playwright `file_chooser`) to upload each
`Path`, then confirms selection.

> **Decision deferred to the §10.3 spike:** whether driving the catalog file
> picker is sufficient, or whether a pre-upload via `page.request.post` to
> `v1/flow/uploadImage` (capture `01`) followed by selecting the now-existing
> asset in the catalog is more robust. Both end with the UI referencing the
> asset; the spike picks one. Either way the **submit** is UI-driven (§0).

### 5.4 Response capture

```python
@staticmethod
def _attach_video_response_listener(page: Page, *, project_id: str | None = None) -> list[dict]:
    """Register page.on('response') for all three batchAsyncGenerateVideo* routes
    (§2.1), filtered by project_id. Mirrors _attach_batch_response_listener."""
```

Attached synchronously before `_send_prompt`, for the race reason documented at
`ui_automation.py:833-838`.

### 5.5 Polling — `_poll_video_status`

```python
@staticmethod
async def _poll_video_status(
    page: Page, media_name: str, project_id: str, *,
    timeout_s: float = 600.0, poll_interval_s: float = 5.0,
) -> VideoStatus:
```

**Active polling:** call `video:batchCheckAsyncVideoGenerationStatus` via
`page.request.post` on a fixed interval. This is sound because the status
request needs no reCAPTCHA token (§2.3) and is deterministic regardless of
whether Flow's SPA keeps polling (Chromium throttles background-tab timers, so
passive interception cannot be the sole mechanism). Parse each response with
`parse_video_status` (§4.4).

Terminal handling: `SUCCESSFUL` → return `VideoStatus`; `FAILED` → return a
`VideoStatus` carrying `failure_reasons`/`error_message` (the caller maps it,
§7); timeout → raise `TimeoutError` with `media_name`, last status, elapsed
time, and a debug screenshot. Default timeout 600 s (Veo can exceed 5 min);
env-configurable via `GFLOW_CLI_VIDEO_POLL_TIMEOUT`.

---

## 6. UI Selectors

Mode-switch selectors (needed for every mode):

```python
VIDEO_MODE_TAB_SELECTORS = (
    "button:has(i:text('play_circle'))",
    "[role='tab']:has-text('Video')",
)
FRAMES_SUBTAB_SELECTORS    = ("[role='tab']:has-text('Frames')", "button:has-text('Frames')")
ELEMENTOS_SUBTAB_SELECTORS = ("[role='tab']:has-text('Elements')", "button:has-text('Elements')")
```

`setup()` already forces `locale="en-US"` and `?hl=en`, so Portuguese selector
variants are dropped. Catalog/file-picker selectors for §5.3 depend on the spike
outcome and are specified during Phase B planning.

**All §6 selectors are unverified guesses against the live Flow DOM.** The
§10.3 spike must include a selector-validation pass (analogous to
`scripts/smoke_worker_style.py`) before transport unit tests are written.

---

## 7. Error Handling

| Condition | Raised error |
|---|---|
| `setup()` not called | `RuntimeError` |
| generate / status / upload → HTTP 401 | `AuthExpiredError` |
| generate / status / upload → HTTP 403 | `WafRejectionError` |
| catalog upload non-200 | `WireFormatError` |
| Video mode tab not found | `RuntimeError` (debug screenshot to `out_dir`) |
| no terminal status within `poll_timeout_s` | `TimeoutError` |
| status `MEDIA_GENERATION_STATUS_FAILED` | mapped by `failureReasons` — below |

A `FAILED` status must **not** be blanket-mapped to `ContentPolicyError`. It
carries `failureReasons[]` + `error.message` (§2.3); map by reason:

| `failureReasons` value | Raised error | Rationale |
|---|---|---|
| `IP_PROHIBITED` (IP block on an input image) | `ContentPolicyError` | Observed (capture `11`); not fixable by softening the prompt — message should name the input image |
| content / safety rejections | `ContentPolicyError` | Prompt-softening remediation applies |
| quota / rate signals | `RateLimitError` | Retry-able (exit 4) |
| anything else / unknown | `WireFormatError`, raw `error`/`failureReasons` in `discovery=` | Don't guess |

Only `IP_PROHIBITED` is observed so far; the full reason vocabulary is unknown
(§10.2 Q4). Unrecognised reasons are logged via structlog with the raw payload
so the taxonomy can grow from real data.

---

## 8. Testing Strategy

TDD, decomposed into Red→Green→Commit increments:

1. **`video.py` value objects** — `Mode.R2V`, the rewritten
   `GenerateVideoRequest` + `__post_init__` validation, `VideoStatus`. Pure, no
   I/O — must hit the 90% `api/` coverage floor.
2. **Response parsers** — `parse_video_status`, `media_name_from_generate_response`,
   driven directly by the captured JSON (`samples/captured/02,08,09,10,11`):
   SUCCESSFUL, FAILED-with-`failureReasons`, and each generate-response shape.
3. **`_attach_video_response_listener`** — mirrors
   `test_attach_batch_response_listener`.
4. **`_poll_video_status`** — fed the captured status JSON: SCHEDULED→ACTIVE→
   SUCCESSFUL happy path, FAILED path, timeout path.
5. **Mode switching** — selector-cascade fallback; decompose into individually
   mockable helpers so unit coverage is reachable.
6. **`generate_video`** — orchestration, pre-`setup()` guard, `_generate_lock`.

**E2E** (`tests/e2e/test_video_ui_automation_e2e.py`, opt-in via
`GFLOW_CLI_E2E_PROFILE`): T2V, I2V (start+end), R2V (≥2 references). Committing
the e2e image fixtures (`test_assets/` currently holds only `image_00.png`) is
an explicit Phase B deliverable.

Mark the existing HTTP-transport I2V e2e tests
(`tests/e2e/test_video_i2v_e2e.py`) `xfail` — 401 is confirmed.

---

## 9. Files Changed

| File | Change |
|---|---|
| `src/gflow_cli/api/video.py` | add `Mode.R2V`; replace `GenerateVideoRequest`; add `VideoStatus`, `parse_video_status`, `media_name_from_generate_response`, `MAX_REFERENCE_IMAGES`. `build_generate_body`/`model_key` **unchanged** (§3) |
| `src/gflow_cli/api/transports/ui_automation_video.py` | **new** — `generate_video()`, mode switching, image attachment, `_attach_video_response_listener()`, `_poll_video_status()` (mixin into `UiAutomationTransport`) |
| `src/gflow_cli/api/transports/ui_automation.py` | small — mix in the video module; new selector constants if not in the new module |
| `src/gflow_cli/cli_video.py` | I2V/R2V/end-frame flags — **Phase B** (§10.3) |
| `tests/api/test_video.py` | value-object + parser unit tests vs captured JSON |
| `tests/api/transports/test_ui_automation*.py` | transport unit tests |
| `tests/e2e/test_video_ui_automation_e2e.py` | **new** e2e file |
| `tests/e2e/test_video_i2v_e2e.py` | mark HTTP-transport tests `xfail` |
| `test_assets/` | add e2e image fixtures — **Phase B** |
| `samples/captured/0[12]_*, 0[89]_*, 1[01]_*` | committed (this branch) |
| `PLAN.md` | phase entries (§10.3) |
| `KNOWN_ISSUES.md` | already updated (2026-05-18) |

---

## 10. Decisions & Open Questions

### 10.1 Resolved

- **Wire format** — three mode-specific endpoints; requests carry
  `startImage`/`endImage`/`referenceImages`; `videoGenerationImageInputs` is a
  response-only echo; status enum is `PENDING|SCHEDULED → ACTIVE →
  SUCCESSFUL|FAILED`; `FAILED` carries `failureReasons[]` (§2).
- **Submit path** — video generation mirrors `generate_images`: drive the UI,
  capture the response. Flow's JS builds+sends the body and mints reCAPTCHA
  (`ui_automation.py:865-875`). The transport never POSTs a generate body; the
  rev-0 "Strategy A vs B" framing is gone.
- **reCAPTCHA token** — not handled by the transport; Flow's JS mints it on UI
  submit. The status endpoint needs no token (§2.3), so active polling via
  `page.request.post` is sound.
- **Domain shape** — explicit validated `mode`; `build_generate_body`/`model_key`
  out of scope; `video.py`'s role is value objects + response parsers (§4).
- **Crop** — not modelled; the UI default frame is used.

### 10.2 Open — answer during planning

1. **Image attachment mechanism (§5.3)** — drive the catalog file picker only,
   or pre-upload via `uploadImage` then select. Decide in the §10.3 spike.
2. **Credit-cost guard** — I2V is ~10 credits/video; T2V/R2V costs unconfirmed.
   Recommendation: echo a one-line Rich cost estimate before submit with a
   `--yes` skip — not a hard block.
3. **Start-only I2V** — is an I2V request with `start_image` but no `end_image`
   accepted? Capture `08` had both. Confirm during the spike; if `end_image` is
   required, tighten `__post_init__`.
4. **`FAILED` reason vocabulary** — only `IP_PROHIBITED` observed; the rest of
   the enum is unknown (§7).
5. **`SQUARE` aspect** — confirm the video editor offers it (§4.3).
6. **`MAX_REFERENCE_IMAGES`** — confirm Flow's R2V upper bound (§4.2).

### 10.3 Phase split

- **Phase 0 — submit-mechanism spike.** Drive the video editor to fire one T2V
  `batchAsyncGenerateVideoText` and capture the response, confirming the
  mechanism mirrors `generate_images`. Validate the §6 selectors against live
  Flow and answer §10.2 Q1/Q3/Q5/Q6. Small, but it de-risks every later phase —
  no transport code past the pure-domain layer should be planned until it lands.
- **Phase A — T2V.** `video.py` value objects + parsers (§4), `generate_video`
  for T2V, mode switching, active polling. No image inputs. Builds on Phase 0.
- **Phase B — I2V + R2V.** Catalog image attachment (§5.3), Frames/Elementos
  handling, `cli_video.py` flags, a BDD feature file, and committed e2e image
  fixtures.

Each phase gets its own `PLAN.md` entry (CLAUDE.md: no feature without one).
