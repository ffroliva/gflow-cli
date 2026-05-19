# Design: Video Generation via UiAutomationTransport

**Date:** 2026-05-18
**Status:** Revised (rev 4) — council consensus reached (review round 4); ready to plan
**Branch:** `feat/ui-automation-onboarding-bypass` (captures + revisions on `chore/video-wire-captures`)

---

## 0. Revision history

**Rev 0 (original draft).** Awaiting council review.

**Rev 1 (post council review 1).** Corrected the wire format against captured
HARs: three mode-specific endpoints, `videoGenerationImageInputs` is a
response-only echo, real status enum, `failureReasons`-based error mapping.

**Rev 2 (post council review 2).** Reframed around the real transport mechanism:
`UiAutomationTransport` drives the browser UI; Flow's own JavaScript builds the
generate request, sends it, and mints reCAPTCHA on every prompt submission
(`UiAutomationTransport.refresh_auth()` is a documented no-op for this reason —
`ui_automation.py:865-875`). The transport never constructs or POSTs a generate
body.

**Rev 3 (post council review 3) — this revision.** Round 3 found two blockers:

1. The §2.3 status example showed `mediaStatus` at the wrong nesting depth.
   Fixed — the real path is `media[i].mediaMetadata.mediaStatus` (§2.3, §4.4).
2. Rev 2 claimed `build_generate_body()`/`model_key()` were "unchanged / out of
   scope" while §4.2 *replaced* the `GenerateVideoRequest` they consume — a real
   compile break. Resolved: the 401-dead HTTP video path is **retired** as part
   of this work (§3, §9, §10.1). This is honest scope, not creep: you cannot
   replace a value object and leave its consumers dangling.

Also: the new transport module's mixin typing, the full blast radius of the
type replacement, and several wording fixes are addressed below.

**Rev 4 (post council review 4) — this revision.** Round 4 reached consensus —
all four reviewers approve, no blockers or majors. Applied the residual minor
fixes: §9 now lists `api/dto.py` and `api/__init__.py` in the blast radius, the
mode-switch test seams are estimated (§8), and the §10.2 questions are tagged by
how each is resolved (Phase 0 spike vs planning).

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
unimplemented. The existing `cli_video.py` commands route through the 401-dead
HTTP path and are non-functional for generation today.

---

## 2. Wire Format (verified — for reference)

This section documents the *observed* wire so the response parsers (§4.4) and
e2e assertions are grounded. **The transport does not build the request
bodies** — Flow's JavaScript does (§0). Request shapes are documented for
understanding; the **response/status shapes are what this feature parses.**

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

For reference only. All three share an envelope (`mediaGenerationContext`,
`clientContext` with `recaptchaContext.token`, `useV2ModelConfig: true`); only
`requests[0]` differs by mode:

```jsonc
// T2V (capture 02) — requests[0] carries NO image input
// I2V (capture 08) — startImage required, endImage optional
"startImage": { "mediaId": "<uuid>", "cropCoordinates": { "top": 0.0, "left": 0.0, "bottom": 1.0, "right": 1.0 } },
"endImage":   { "mediaId": "<uuid>", "cropCoordinates": { ... } }
// R2V (capture 09)
"referenceImages": [ { "mediaId": "<uuid>", "imageUsageType": "IMAGE_USAGE_TYPE_ASSET" } ]
```

`cropCoordinates` are normalized floats the Flow UI sets when a human drags the
crop box; driven without dragging, the UI applies a default frame. **The CLI
does not model crop** (§10.2). `videoGenerationImageInputs` appears only in
*responses* as a normalized server echo — never in a request.

### 2.3 Status request/response

`video:batchCheckAsyncVideoGenerationStatus` — request body is **just**
`{ "media": [{ "name": "<mediaUuid>", "projectId": "<uuid>" }] }`, with **no**
`clientContext` and **no** `recaptchaContext` (captures 10/11). This is
load-bearing: the status endpoint needs no reCAPTCHA token, so it can be polled
directly via `page.request.post` (§5.5).

The response status lives at `media[i].mediaMetadata.mediaStatus` — note the
`mediaMetadata` wrapper:

```jsonc
// response_body_parsed (capture 11 — FAILED)
{ "media": [
  { "name": "<mediaUuid>", "projectId": "<uuid>", "workflowId": "...",
    "mediaMetadata": {
      "mediaStatus": {
        "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_FAILED",
        "error": { "code": 3, "message": "PUBLIC_ERROR_IP_INPUT_IMAGE" },
        "failureReasons": ["IP_PROHIBITED"]
      },
      "visibility": "FILTERED"   // PRIVATE on success
    } } ] }
```

Observed `mediaGenerationStatus` values (all carry the
`MEDIA_GENERATION_STATUS_` prefix):

```
MEDIA_GENERATION_STATUS_PENDING | MEDIA_GENERATION_STATUS_SCHEDULED
   -> MEDIA_GENERATION_STATUS_ACTIVE
   -> MEDIA_GENERATION_STATUS_SUCCESSFUL | MEDIA_GENERATION_STATUS_FAILED
```

`PENDING` was seen for T2V right after submit, `SCHEDULED` for I2V/R2V — both
mean "not yet running". (In a generate response, T2V additionally surfaces
`PENDING` in a top-level `operations[0].status`; the parser reads the
`mediaMetadata.mediaStatus` form, not `operations[]`.) `QUEUED` and `COMPLETED`
do **not** exist on the wire; the terminal success value is `SUCCESSFUL`.
`error.code` is an unexplained integer — captured but not consumed; error
mapping keys on `failureReasons`/`error.message` (§7).

### 2.4 The poll handle

Both generate and status responses carry `media[0].name` — the UUID to poll.
For I2V/R2V this is verified (captures 08/09 generate `media[0].name` and the
10/11 status request `name` are the same asset). **For T2V it is not yet
verified** — capture `02` redacts every UUID to `<UUID>`, so the spec cannot
prove `media[0].name` (vs `operations[0].operation.name` or
`workflows[0].metadata.primaryMediaId`) is the value the status endpoint
accepts. This is a §10.2 open question for the Phase 0 spike.
`remainingCredits` is returned in generate and status responses. Generated video
bytes are not inlined — download is a separate `media.getMediaUrlRedirect` call
(out of scope, §3).

---

## 3. Scope

**In:** T2V, I2V (start + optional end image), R2V (one or more reference
images) on `UiAutomationTransport` — by driving the Flow video editor UI and
capturing the response, mirroring `generate_images`. Polling blocks until
terminal status.

**Retired by this work** (consequence of replacing `GenerateVideoRequest`, §4.2):
the 401-dead HTTP video path — `video.py:build_generate_body()`, `model_key()`,
the module wire constants, `FlowApiClient.generate_video()`, the `VideoOperation`
DTO, and their tests. The committed captures are the documented wire record if
that path is ever revived. See §9 for the full file list.

**Out of scope:**
- Video download (separate `media.getMediaUrlRedirect` call — *not* the image
  `fife_url` pattern; the status response carries no video URL).
- Crop control (the UI default frame is used — §10.2).
- Reusing pre-existing project assets by UUID. The current `cli_video.py` I2V
  command accepts an asset UUID (`start_asset_uuid`); the reworked design takes
  **local file paths** instead (§4.2). Since the current command is 401-broken,
  no working capability is lost — but the CLI surface changes; asset-UUID reuse
  is a future extension.
- Voice elements; Veo tier/quality beyond Fast/Lite.

Implementation is split into a spike + two phases — see §10.3.

---

## 4. Domain Changes — `src/gflow_cli/api/video.py`

For this feature `video.py` provides **value objects and pure response
parsers** — no body building. The HTTP-path machinery (`build_generate_body`,
`model_key`, wire constants) is removed (§3, §9), not extended.

### 4.1 `Mode` — add `R2V`

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
    tier: Tier = Tier.FAST            # meaningful for T2V only — see 4.3
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

`__post_init__` validates **structure only** — it does NOT check that image
paths exist on disk (that is I/O; the domain layer is pure). Path
existence/readability is validated by the transport at the boundary (§5.3).

`MAX_REFERENCE_IMAGES` is a module constant; its value is a **Phase 0 spike
output** (Flow's R2V upper bound is unconfirmed — §10.2 Q6) — planning must not
hardcode a guess.

The legacy `start_asset_uuid` field and the derived `mode` property are removed.
`tier` is retained but only meaningful for T2V — I2V/R2V model keys are fixed
(§2.1); document this on the field. `Tier` itself is unchanged (`FAST`/`QUALITY`).

### 4.3 Aspect note

`Aspect` (`PORTRAIT`/`LANDSCAPE`/`SQUARE`) is reused as-is. The transport uses it
to drive the editor's aspect-ratio control (as `generate_images` does via
`_configure_generation_settings`) — not to build a wire string, so `Aspect.wire()`
is not on this feature's path. **`SQUARE` is unverified for video** (captures
show only `PORTRAIT`/`LANDSCAPE`); the §10.3 spike confirms whether the video
editor offers it.

### 4.4 `VideoStatus` value object + pure parsers

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
```

`parse_video_status(response_json, *, media_id) -> VideoStatus` — selects the
`response_json["media"][i]` entry whose `name == media_id`, then reads
`mediaMetadata.mediaStatus.{mediaGenerationStatus, failureReasons, error.message}`
(§2.3). Shapes: captures 10 (SUCCESSFUL), 11 (FAILED).

`media_name_from_generate_response(response_json) -> str` — returns
`response_json["media"][0]["name"]`. Shapes: captures 02, 08, 09. (T2V's extra
top-level `operations[]` is ignored; whether `media[0].name` is the correct T2V
poll handle is verified by the §10.3 spike — §2.4.)

Both parsers are pure (no I/O) and tested directly against the captured JSON.

---

## 5. Transport Changes

The video methods live in a **new module**
`src/gflow_cli/api/transports/ui_automation_video.py` — `ui_automation.py` is
already ~900 lines, over the 800-line cap, so video logic must not be added
inline.

### 5.0 Mixin typing

The video methods form a mixin mixed into `UiAutomationTransport`. To satisfy
`pyright --strict`, the module declares a `Protocol` for the host state the
mixin reaches into:

```python
class _VideoHost(Protocol):
    _page: Page | None
    _generate_lock: asyncio.Lock
    _setup_done: bool
```

Mixin methods annotate `self` against `_VideoHost`; `UiAutomationTransport` is
the concrete implementer. `self._page` narrowing uses the same
`# type: ignore[assignment]` pattern as `_generate_images_locked`
(`ui_automation.py:821`).

### 5.1 `generate_video`

```python
async def generate_video(
    self: _VideoHost,
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
  -> for I2V/R2V: validate image paths exist, then attach via catalog UI (§5.3)
  -> _attach_video_response_listener(page, project_id)                   (§5.4)
  -> _send_prompt(page, request.prompt)   # Flow's JS builds+sends+mints reCAPTCHA
  -> _await_captured(...) -> media_name_from_generate_response(...)
  -> _poll_video_status(page, media_name, project_id, ...)               (§5.5)
```

### 5.2 Mode switching

Click the Video tab, then the Frames (I2V) or Elementos (R2V) sub-tab. Selectors
in §6.

### 5.3 Image attachment (I2V / R2V)

Local image `Path`s are validated at the boundary here (exist, are files,
readable) before use, then attached through Flow's catalog UI so Flow's JS
includes them in the request it builds. The transport drives the catalog's file
picker (Playwright `file_chooser`) to upload each file, then confirms selection.

> **Decision deferred to the §10.3 spike:** whether driving the catalog file
> picker is sufficient, or whether a pre-upload via `page.request.post` to
> `v1/flow/uploadImage` (capture `01`) followed by selecting the now-existing
> asset is more robust. Either way the **submit** is UI-driven (§0).

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
`page.request.post` on a fixed interval — sound because the status request needs
no reCAPTCHA token (§2.3) and is deterministic regardless of whether Flow's SPA
keeps polling (Chromium throttles background-tab timers). Parse each response
with `parse_video_status` (§4.4).

Terminal handling: `MEDIA_GENERATION_STATUS_SUCCESSFUL` → return `VideoStatus`;
`MEDIA_GENERATION_STATUS_FAILED` → return a `VideoStatus` carrying
`failure_reasons`/`error_message` (the caller maps it, §7); timeout → raise
`TimeoutError` with `media_name`, last status, elapsed time, and a debug
screenshot. Default timeout 600 s (Veo can exceed 5 min); env-configurable via
`GFLOW_CLI_VIDEO_POLL_TIMEOUT`.

---

## 6. UI Selectors

> **Corrected by the Phase 0 spike (§10.4).** The mode switch is a **2-step
> dropdown**, not a visible tab bar, and Flow renders in the **account's
> language** (the test account is pt-BR) regardless of `locale="en-US"` /
> `?hl=en`. Text selectors therefore carry both en and pt-BR variants;
> structural selectors (icon ligature, `aria-controls`) are locale-invariant
> and preferred.

**Mode switching is a two-step interaction.** A `button[aria-haspopup='menu']`
trigger — it shows the current mode (`Vídeo`/`Imagem` + crop icon) — opens a
`role='menu'` whose `role='tablist'` holds the Imagem/Vídeo `role='tab'`s. The
tabs are **not in the DOM until the menu opens**, so the transport must click
the trigger first, then the tab:

```python
MODE_SWITCH_TRIGGER_SELECTORS = (
    "button[aria-haspopup='menu']:has-text('Vídeo')",
    "button[aria-haspopup='menu']:has-text('Video')",
    "button[aria-haspopup='menu']:has-text('Imagem')",
    "button[aria-haspopup='menu']:has-text('Image')",
    "button[aria-haspopup='menu']",                       # last-resort fallback
)
VIDEO_TAB_IN_MENU_SELECTORS = (
    "[role='menu'] [role='tab'][aria-controls*='VIDEO']",
    "[role='menu'] [role='tab']:has(i:text('play_circle'))",
    "[role='tab'][aria-controls*='VIDEO']",
    "[role='menu'] [role='tab']:has-text('Vídeo')",
    "[role='menu'] [role='tab']:has-text('Video')",
)
```

Frames/Elementos sub-mode tabs and the Frames start/end slots. §10.4 found the
slots are `<div type="button" aria-haspopup="dialog">Inicial</div>` / `Final` —
`<div>`, not `<button>`, and `aria-haspopup="dialog"` means clicking one opens
an **in-page catalog dialog** (relevant to §5.3 / Q1):

```python
FRAMES_SUBTAB_SELECTORS = (
    "[role='tab']:has-text('Frames')",    "[role='tab']:has-text('Quadros')",
    "button:has-text('Frames')",          "button:has-text('Quadros')",
)
ELEMENTOS_SUBTAB_SELECTORS = (
    "[role='tab']:has-text('Elementos')", "[role='tab']:has-text('Elements')",
    "button:has-text('Elementos')",       "button:has-text('Elements')",
)
START_FRAME_SELECTORS = (
    "div[type='button'][aria-haspopup='dialog']:has-text('Inicial')",
    "div[type='button'][aria-haspopup='dialog']:has-text('Start')",
)
```

Catalog/file-picker selectors for §5.3 depend on the spike outcome and are
specified during Phase B planning.

**§6 status:** the 2-step-dropdown *pattern* is spike-confirmed (§10.4); the
exact mode-switch selector strings are confirmed once a spike run passes the
mode switch. The sub-tab, aspect, and catalog selectors remain unverified
guesses pending the next spike runs. The §10.3 Phase 0 spike completes the
selector-validation pass before transport unit tests are written.

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
| `IP_PROHIBITED` (IP block on an input image) | `ContentPolicyError` | Observed (capture `11`); not fixable by softening the prompt |
| content / safety rejections | `ContentPolicyError` | Prompt-softening remediation applies |
| quota / rate signals | `RateLimitError` | Retry-able (exit 4) |
| anything else / unknown | `WireFormatError`, raw `error`/`failureReasons` in `discovery=` | Don't guess |

`error.message` is an enum-style token (e.g. `PUBLIC_ERROR_IP_INPUT_IMAGE`), not
human-readable prose — the CLI formats a friendly message from it. Only
`IP_PROHIBITED` is observed so far; the full reason vocabulary is unknown
(§10.2 Q4). Unrecognised reasons are logged via structlog with the raw payload
so the taxonomy can grow from real data.

---

## 8. Testing Strategy

TDD, decomposed into Red→Green→Commit increments. Markers per
`CONTRIBUTING.md` (`unit` / `integration` / `e2e`) noted per increment.

1. **Retire the dead HTTP video path** [`unit`] — remove `build_generate_body`,
   `model_key`, the wire constants, `FlowApiClient.generate_video`,
   `VideoOperation`, and their tests; stub `cli_video.py`'s video commands with a
   clear "not yet available on the working transport" message (they are
   401-broken today). Keeps the tree green for the type swap in increment 2.
2. **`video.py` value objects** [`unit`] — `Mode.R2V`, the rewritten
   `GenerateVideoRequest` + `__post_init__` validation, `VideoStatus`,
   `MAX_REFERENCE_IMAGES`. Pure — must hit the 90% `api/` floor. The I2V
   `end_image`-optional rule is **provisional**: if the Phase 0 spike finds
   `end_image` is required (§10.2 Q3), increment 2's I2V validation gets a
   one-line tightening + test update in Phase B.
3. **Response parsers** [`unit`] — `parse_video_status`,
   `media_name_from_generate_response`, driven by the captured JSON
   (`samples/captured/02,08,09,10,11`): SUCCESSFUL, FAILED-with-`failureReasons`,
   each generate-response shape.
4. **`_attach_video_response_listener`** [`unit`] — mirrors
   `test_attach_batch_response_listener`.
5. **`_poll_video_status`** [`unit`] — fed captured status JSON:
   SCHEDULED→ACTIVE→SUCCESSFUL happy path, FAILED path, timeout path (full
   `MEDIA_GENERATION_STATUS_*` wire values).
6. **Mode switching** [`unit`] — selector-cascade fallback; decompose into
   individually mockable helpers (expect ≥3 seams — video-tab click, sub-tab
   click, generic selector-cascade — named precisely in the Phase A plan) so the
   90% `api/` floor on `ui_automation_video.py` is reachable.
7. **`generate_video`** [`unit`] — orchestration, pre-`setup()` guard,
   `_generate_lock`.

**E2E** [`e2e`] (`tests/e2e/test_video_ui_automation_e2e.py`, opt-in via
`GFLOW_CLI_E2E_PROFILE`): T2V, I2V (start+end), R2V (≥2 references). Committing
the e2e image fixtures (`test_assets/` currently holds only `image_00.png`) is
an explicit Phase B deliverable.

Mark the existing HTTP-transport I2V e2e tests
(`tests/e2e/test_video_i2v_e2e.py`) `xfail` — 401 is confirmed.

---

## 9. Files Changed

| File | Change |
|---|---|
| `src/gflow_cli/api/video.py` | add `Mode.R2V`; replace `GenerateVideoRequest`; add `VideoStatus`, `parse_video_status`, `media_name_from_generate_response`, `MAX_REFERENCE_IMAGES`; **remove** `build_generate_body`, `model_key`, wire constants |
| `src/gflow_cli/api/client.py` | remove `FlowApiClient.generate_video()` (HTTP video path, `client.py:587`) |
| `src/gflow_cli/api/dto.py` | remove the `VideoOperation` dataclass (and its `from_generate_response`) — orphaned once the HTTP path is retired |
| `src/gflow_cli/api/__init__.py` | drop the `VideoOperation` import + `__all__` entry; the `GenerateVideoRequest` re-export stays (the class survives, §4.2) |
| `src/gflow_cli/api/transports/ui_automation_video.py` | **new** — `_VideoHost` Protocol, `generate_video()`, mode switching, image attachment, `_attach_video_response_listener()`, `_poll_video_status()` |
| `src/gflow_cli/api/transports/ui_automation.py` | small — mix in the video module |
| `src/gflow_cli/cli_video.py` | increment 1: stub video commands; **Phase B**: rewire `t2v`/`i2v`/`batch` to the UI transport, add R2V/end-frame flags (image inputs become file paths, not asset UUIDs — §3) |
| `scripts/smoke_e2e.py` | remove the video block (`smoke_e2e.py:49-50`) — the retired HTTP path |
| `README.md` | update the video-usage section (`README.md:192` references the old flow) |
| `tests/api/test_video.py` | replace HTTP-body tests with value-object + parser tests vs captured JSON |
| `tests/api/test_client_generate_video.py` | remove (HTTP video path retired) |
| `tests/api/transports/test_ui_automation*.py` | transport unit tests |
| `tests/e2e/test_video_ui_automation_e2e.py` | **new** e2e file |
| `tests/e2e/test_video_i2v_e2e.py` | mark HTTP-transport tests `xfail` |
| `test_assets/` | add e2e image fixtures — **Phase B** |
| `samples/captured/0[12]_*, 0[89]_*, 1[01]_*` | committed (this branch) |
| `PLAN.md` | phase entries (§10.3) |
| `KNOWN_ISSUES.md` | already updated (2026-05-18) |

A planning task confirms no other live caller of the retired symbols remains
(`grep` for `generate_video`, `build_generate_body`, `model_key`,
`VideoOperation`, `start_asset_uuid`).

---

## 10. Decisions & Open Questions

### 10.1 Resolved

- **Wire format** — three mode-specific endpoints; requests carry
  `startImage`/`endImage`/`referenceImages`; `videoGenerationImageInputs` is a
  response-only echo; status enum is `PENDING|SCHEDULED → ACTIVE →
  SUCCESSFUL|FAILED`; `mediaStatus` is nested under `media[].mediaMetadata`;
  `FAILED` carries `failureReasons[]` (§2).
- **Submit path** — video generation mirrors `generate_images`: drive the UI,
  capture the response. Flow's JS builds+sends the body and mints reCAPTCHA.
  The rev-0 "Strategy A vs B" framing is gone.
- **reCAPTCHA token** — not handled by the transport. The status endpoint needs
  no token (§2.3), so active polling via `page.request.post` is sound.
- **HTTP video path retired** — replacing `GenerateVideoRequest` cannot leave
  `build_generate_body`/`model_key`/`client.generate_video`/`VideoOperation`
  dangling; they are 401-dead (§1) and removed (§3, §9).
- **Domain shape** — explicit validated `mode`; `video.py`'s role is value
  objects + pure response parsers; `__post_init__` is pure (no path I/O).
- **Crop** — not modelled; the UI default frame is used.

### 10.2 Open — answer during the Phase 0 spike or planning

1. **Image attachment mechanism (§5.3)** — drive the catalog file picker only,
   or pre-upload via `v1/flow/uploadImage` then select. Phase 0 spike.
2. **Credit-cost guard** — I2V is ~10 credits/video; T2V/R2V costs unconfirmed.
   Recommendation: echo a one-line Rich cost estimate before submit with a
   `--yes` skip — not a hard block. Planning decision.
3. **Start-only I2V** — is an I2V request with `start_image` but no `end_image`
   accepted? Capture `08` had both. Phase 0 spike; affects increment 2
   validation (§8).
4. **`FAILED` reason vocabulary** — only `IP_PROHIBITED` observed (§7). Not
   spike-blocking — the taxonomy grows from production data (planning).
5. **`SQUARE` aspect** — confirm the video editor offers it (§4.3). Phase 0 spike.
6. **`MAX_REFERENCE_IMAGES`** — confirm Flow's R2V upper bound (§4.2). Phase 0 spike.
7. **T2V poll handle** — confirm `media[0].name` (not `operations[0].operation.name`
   or `primaryMediaId`) is the value `batchCheckAsyncVideoGenerationStatus`
   accepts for T2V (§2.4). Phase 0 spike.

### 10.3 Phase split

- **Phase 0 — submit-mechanism spike.** Drive the video editor to fire one T2V
  `batchAsyncGenerateVideoText` and capture the response, confirming the
  mechanism mirrors `generate_images`. Validate the §6 selectors against live
  Flow. Answers §10.2 Q1, Q3, Q5, Q6, Q7. Small, but it de-risks every later
  phase — no transport code past the pure-domain layer is planned until it lands.
- **Phase A — T2V.** Increment 1 (retire the dead HTTP path) + value objects +
  parsers (§4) + `generate_video` for T2V + mode switching + active polling. No
  image inputs.
- **Phase B — I2V + R2V.** Catalog image attachment (§5.3), Frames/Elementos
  handling, `cli_video.py` rewired to the UI transport with R2V/end-frame flags,
  a BDD feature file, committed e2e image fixtures. **Blocked on Phase 0 Q1** —
  the §6 catalog selectors and the §5.3 mechanism choice come from the spike.

Each phase gets its own `PLAN.md` entry (CLAUDE.md: no feature without one).

### 10.4 Phase 0 partial findings (2026-05-18 — spike run vs live Flow)

The first `scripts/smoke_video_editor.py` runs reached the editor (`spike_editor_ready`) and surfaced two findings that **invalidate §6 assumptions**:

- **The Flow UI renders in the account's language, not `?hl=en`.** The test account's Flow is **Portuguese** (`Vídeo`, `Imagem`, `Inicial`, `Final`, `Criar`, "O que você quer criar?") despite `locale="en-US"` + `?hl=en`. §6's decision to drop the Portuguese selector variants is **wrong** — selectors must carry pt-BR text (and en, for other accounts).
- **Mode switching is a dropdown menu, not a visible tab bar.** A trigger `button[aria-haspopup="menu"]` (shows the current mode — e.g. `Vídeo` + crop icon + `1x`) opens a `role="menu"` containing a `role="tablist"` with `button[role="tab"]` for Imagem/Vídeo (`id="radix-…-trigger-VIDEO"`, `aria-controls="…-content-VIDEO"`, child `<i>play_circle</i>`). Switching mode is a **two-step interaction** (open dropdown → click the tab), not a single click — which is why `VIDEO_MODE_TAB_SELECTORS` missed (the tabs aren't in the DOM until the menu opens). The editor defaults to Video mode. Frames sub-tab slots are `<div type="button" aria-haspopup="dialog">Inicial</div>` / `Final` (a third element pattern).

**Action for §6 + Phase A planning:** rewrite the spike's mode-switch (`_drive_spike` Task-2 block) as a 2-step dropdown interaction; restore Portuguese selector variants in §6; re-derive the Frames/Elementos sub-tab, aspect, and catalog selectors against the live pt-BR DOM. Suggested: `MODE_SWITCH_TRIGGER = button[aria-haspopup='menu']`; video tab = `[role='menu'] [role='tab'][aria-controls*='VIDEO']` / `[role='tab']:has(i:text('play_circle'))`. §10.2 Q5/Q6/Q7 remain **unanswered** — the spike did not reach them.

**Auth (resolved):** the spike requires a profile authenticated via `gflow auth login --browser chrome`; `main()` fails fast otherwise. The `?hl=en` URL does not override account language.
