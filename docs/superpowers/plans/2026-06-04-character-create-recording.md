# Character-Create Recording Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make gflow's REAL `character create` flow recordable as a browser-only video (for the gflow-cli-remotion promo) without leaking any recording concern into core code.

**Architecture:** The `FlowApiClient` owns the single Playwright browser context that the UI-automation transport shares (client-owned path: `transport.setup(page=self._page)`). Therefore the context that must be recorded is the *client's*, launched in `FlowApiClient._enter_setup` (`client.py:280`). We make exactly one behavior-preserving change to core — extract that `launch_persistent_context(**kwargs)` call's kwargs into an overridable `_persistent_context_kwargs()` seam (no new behavior, no env reads, no recording concept). All recording lives in dev-scoped infra (`scripts/dev/`): a `RecordingFlowApiClient(FlowApiClient)` subclass overrides only that seam to add Playwright `record_video_dir`, and a recorder driver runs the genuine `character_create` service through it. Image generation is free (only video costs credits), so a real recorded run costs nothing.

**Tech Stack:** Python 3.13, Playwright (async, `record_video_dir`), pytest, ruff/pyright, ffmpeg (webm→mp4). gflow services: `services.character_create.character_create`, `api.client.FlowApiClient`, `api.character.CharacterImageRequest`, `data.recorder.OperationRecorder`.

**Process:** Lean — this plan + `/gflow:branch-review` before PR. Skip the 5-persona `/gflow:predict` (behavior-preserving refactor + inert subclass, not a new transport/auth change).

**Why NOT the transport seam:** in the client-owned path the transport reuses the client's page and never calls its own `launch_persistent_context` (`ui_automation.py:574-580`). Recording the transport's context would record a context that is never used. The client's context is the real one.

---

## ⚠️ COUNCIL REVISION (2026-06-04) — READ FIRST; OVERRIDES CONFLICTING TASK TEXT BELOW

A full grounded LLM council reviewed this plan against the real source. **Verdict: GO-WITH-FIXES.** The
architecture is verified sound (single client-owned persistent context; the Task-1 seam is a
value-for-value faithful reproduction of `client.py:280-289`; `concurrency` defaults to 1 → exactly one
`.webm`). Apply the corrections below; where they conflict with the task sections further down, **these win.**

**Base:** `chore/character-create-recording` off `origin/develop` (`7563789`). On this base
`scripts/dev/record_flow_capture.py` does **not** exist (it lived only on `bugfix/character-editor-title`),
so Task 3 **CREATES** the driver from scratch (incl. `_transcode()` + argparse `main()` + no-ffmpeg
fallback) — it is NOT a "rewrite".

**MUST-FIX (blockers):**
1. Task 3 driver: `from gflow_cli.config import Settings` (NOT `gflow_cli.settings` — that module does not
   exist; `Settings` is at `config.py:81`). Build `Settings()` AFTER setting `os.environ["GFLOW_CLI_DB_PATH"]`
   (intentionally bypasses the lru-cached `get_settings()` singleton).
2. Task 1: promote `from gflow_cli.browser_manager import channel_for_profile` to **module scope** (top of
   `client.py`) and DELETE the orphaned function-local import at `client.py:278` — else ruff F401 fails CI.
   Exactly one import site.
3. Locale/#153 → resolved by new **Task 0** below (land-first).

**Decisions (user, 2026-06-04):**
- **Locale:** fix #153 FIRST as **Task 0** (normalize BCP-47→short inside `character_editor_url`); the
  recorder then passes the genuine CLI default `en-US` and stays language-agnostic.
- **Live T3:** proceed WITHOUT a separate `/gflow:predict` (the council already covered the predict-level
  risks); harden T3 instead (below). **Pause for explicit user confirmation before the live run.**

**T3 hardening (fold into Task 3):**
- Isolate `output_dir` too (a temp dir via `GFLOW_CLI_OUTPUT_DIR`), not only the DB — the generated image
  must not land in the user's real gallery dir.
- Force `concurrency=1` and FAIL loudly if >1 `.webm` is produced.
- Add a structlog-event assertion proving the IMAGE path ran (`character` create completed +
  `image_generated` + a `batchGenerateImages` 200 with **no** `batchAsyncGenerateVideo` call) — `refs=1`
  alone does not prove image-not-video.
- `recorder.close()` in a `try/finally` (mirror `cli_character.py`).
- Confirm `ProjectInfo.project_id` field name before `proj.project_id`; or accept `--project` to mirror the
  CLI 1:1 (reuse promo-denon82 id `f5d0d08b-0617-40ea-a5b3-1d716c60d07f`).
- `_transcode()`: `shutil.which("ffmpeg")` guard → keep/copy the `.webm` + warn rather than fail.

**Task 1 test hardening:** also assert the call site routes THROUGH the seam (spy that
`launch_persistent_context` was called with exactly the dict `_persistent_context_kwargs()` returns) — the
pin test alone is tautological. Optionally assert `kwargs["channel"] is None` for a marker-less tmp_path.

**Type-gate note:** `scripts/dev/*` is NOT covered by `pyright src` (include = `[src, tests]`); run pyright
over the new dev files explicitly or accept they're outside the gate.

---

## Task 0: Fix #153 — normalize locale in `character_editor_url` (land-first bugfix)

**Files:**
- Modify: `src/gflow_cli/api/routes.py:123-132` (`character_editor_url`)
- Test: `tests/api/test_routes_character.py` (extend)

- [ ] **Step 1 (RED):** add tests asserting BCP-47 inputs are shortened, and that short inputs are unchanged:
  - `character_editor_url("en-US", "p", "e")` → ends `/fx/en/tools/flow/project/p/character/e`
  - `character_editor_url("pt-BR", "p", "e")` → contains `/fx/pt/`
  - `character_editor_url("EN-us", "p", "e")` → `/fx/en/` (lower-cased)
  - existing short-locale tests (`en`/`pt`/`de`/`ja`) still pass (idempotent).
- [ ] **Step 2:** run `tests/api/test_routes_character.py` → the `en-US`/`pt-BR`/`EN-us` cases FAIL (verbatim interpolation today).
- [ ] **Step 3 (GREEN):** normalize before interpolation — `segment = locale.split("-", 1)[0].lower()`,
  interpolate `{segment}`. Update the docstring to note BCP-47 inputs are reduced to the short Flow URL
  segment (fixes #153).
- [ ] **Step 4:** run `tests/api/test_routes_character.py` → all green.
- [ ] **Step 5:** `pyright src` → 0; `ruff check` + `ruff format --check` clean.
- [ ] **Step 6:** commit `fix(routes): normalize BCP-47 locale to short Flow URL segment (#153)`.

---

## File Structure

- **Core (1 behavior-preserving change):**
  - `src/gflow_cli/api/client.py` — extract launch kwargs (`_enter_setup`, ~L280) into `_persistent_context_kwargs(self) -> dict[str, Any]`; call site becomes `launch_persistent_context(**self._persistent_context_kwargs())`.
- **Tests (core):**
  - `tests/api/test_client_launch_kwargs.py` (new) — pins the seam's returned dict so the refactor is provably behavior-preserving and the seam stays stable.
- **Dev/test recording infra (out of core):**
  - `scripts/dev/_recording_client.py` (new) — `RecordingFlowApiClient(FlowApiClient)` overriding only `_persistent_context_kwargs` to add `record_video_dir`/`record_video_size`.
  - `scripts/dev/record_flow_capture.py` (REWRITE — replaces the current standalone re-implementation) — driver that runs the real `character_create` through the recording client and transcodes the webm to mp4.
  - `tests/dev/test_recording_client.py` (new) — asserts the subclass injects recording kwargs and preserves all base kwargs.
- **Integration (separate repo, final task):**
  - `gflow-cli-remotion`: `public/captures/character-master.mp4`, `src/remotion/Root.tsx` defaultProps.

---

## Task 1: Core seam — extract `_persistent_context_kwargs()` (behavior-preserving)

**Files:**
- Modify: `src/gflow_cli/api/client.py:280-289` (inside `_enter_setup`)
- Test: `tests/api/test_client_launch_kwargs.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_client_launch_kwargs.py
from pathlib import Path

from gflow_cli.api.client import FlowApiClient


def test_persistent_context_kwargs_are_unchanged(tmp_path: Path) -> None:
    """The seam returns exactly the kwargs the client launched with before
    the refactor — proves the extraction changed no behavior."""
    client = FlowApiClient(profile_dir=tmp_path, headless=True)
    kwargs = client._persistent_context_kwargs()  # noqa: SLF001
    assert kwargs["user_data_dir"] == str(tmp_path)
    assert kwargs["headless"] is True
    assert kwargs["viewport"] == {"width": 1280, "height": 720}
    assert kwargs["locale"] == "en-US"
    assert kwargs["extra_http_headers"] == {"Accept-Language": "en-US,en;q=0.9"}
    assert kwargs["ignore_default_args"] == [
        "--enable-automation",
        "--no-sandbox",
        "--password-store=basic",
    ]
    assert kwargs["args"] == ["--disable-blink-features=AutomationControlled"]
    # channel is profile-derived; just assert the key is present.
    assert "channel" in kwargs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/api/test_client_launch_kwargs.py -v`
Expected: FAIL — `AttributeError: 'FlowApiClient' object has no attribute '_persistent_context_kwargs'`

- [ ] **Step 3: Implement the seam (behavior-preserving)**

In `src/gflow_cli/api/client.py`, replace the inline launch (currently `client.py:280-289`):

```python
        self._context = await self._pw.chromium.launch_persistent_context(
            **self._persistent_context_kwargs()
        )
```

Add the method just above `_enter_setup` (keep `from gflow_cli.browser_manager import channel_for_profile` available — move it to module scope or import inside the method):

```python
    def _persistent_context_kwargs(self) -> dict[str, Any]:
        """Keyword args for the persistent browser context launch.

        Extracted as an overridable seam so out-of-core tooling (e.g. a
        dev-scoped recording subclass) can augment the launch — without any
        recording/test concern living in this core path. Behavior here is
        identical to the previous inline call.
        """
        from gflow_cli.browser_manager import channel_for_profile

        return {
            "user_data_dir": str(self.profile_dir),
            "headless": self.headless,
            "viewport": {"width": 1280, "height": 720},
            "locale": "en-US",
            "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9"},
            "channel": channel_for_profile(self.profile_dir),
            "ignore_default_args": [
                "--enable-automation",
                "--no-sandbox",
                "--password-store=basic",
            ],
            "args": ["--disable-blink-features=AutomationControlled"],
        }
```

Ensure `Any` is imported (`from typing import Any`) — it almost certainly already is; verify.

- [ ] **Step 4: Run test + the existing client suite to verify no behavior change**

Run: `.venv\Scripts\python.exe -m pytest tests/api/test_client_launch_kwargs.py tests/api -q`
Expected: PASS (new test + existing client tests green).

- [ ] **Step 5: Type-check the whole tree (CI gate)**

Run: `.venv\Scripts\python.exe -m pyright src`
Expected: 0 errors. (See memory: pyright must run on the whole `src` tree.)

- [ ] **Step 6: Commit**

```bash
git add src/gflow_cli/api/client.py tests/api/test_client_launch_kwargs.py
git commit -m "refactor(client): extract _persistent_context_kwargs seam (behavior-preserving)"
```

---

## Task 2: `RecordingFlowApiClient` subclass (dev-scoped, out of core)

**Files:**
- Create: `scripts/dev/_recording_client.py`
- Test: `tests/dev/test_recording_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/dev/test_recording_client.py
import sys
from pathlib import Path

_DEV = Path(__file__).resolve().parents[2] / "scripts" / "dev"
if str(_DEV) not in sys.path:
    sys.path.insert(0, str(_DEV))

from _recording_client import RecordingFlowApiClient  # noqa: E402


def test_recording_client_injects_video_and_preserves_base(tmp_path: Path) -> None:
    rec_dir = tmp_path / "rec"
    client = RecordingFlowApiClient(
        profile_dir=tmp_path, headless=True, record_video_dir=rec_dir
    )
    kwargs = client._persistent_context_kwargs()  # noqa: SLF001
    # Recording kwargs injected:
    assert kwargs["record_video_dir"] == str(rec_dir)
    assert kwargs["record_video_size"] == {"width": 1280, "height": 720}
    # Base kwargs preserved:
    assert kwargs["user_data_dir"] == str(tmp_path)
    assert kwargs["args"] == ["--disable-blink-features=AutomationControlled"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/dev/test_recording_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named '_recording_client'`

- [ ] **Step 3: Implement the subclass**

```python
# scripts/dev/_recording_client.py
"""Dev-scoped FlowApiClient subclass that records the browser context.

NOT imported by the gflow_cli package. Adds Playwright video recording to the
client's persistent context via the core _persistent_context_kwargs() seam, so
no recording concern lives in core. Used only by scripts/dev recorders/tests.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from gflow_cli.api.client import FlowApiClient  # noqa: E402

_VIDEO_SIZE = {"width": 1280, "height": 720}


class RecordingFlowApiClient(FlowApiClient):
    """FlowApiClient that records its browser context to ``record_video_dir``."""

    def __init__(self, *args: Any, record_video_dir: Path, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._record_video_dir = record_video_dir

    def _persistent_context_kwargs(self) -> dict[str, Any]:
        kwargs = super()._persistent_context_kwargs()
        kwargs["record_video_dir"] = str(self._record_video_dir)
        kwargs["record_video_size"] = dict(_VIDEO_SIZE)
        return kwargs
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/dev/test_recording_client.py -v`
Expected: PASS

- [ ] **Step 5: Lint**

Run: `.venv\Scripts\python.exe -m ruff check scripts/dev/_recording_client.py tests/dev/test_recording_client.py && .venv\Scripts\python.exe -m ruff format --check scripts/dev/_recording_client.py tests/dev/test_recording_client.py`
Expected: clean (run `ruff format` if needed).

- [ ] **Step 6: Commit**

```bash
git add scripts/dev/_recording_client.py tests/dev/test_recording_client.py
git commit -m "chore(dev): RecordingFlowApiClient subclass (records context via core seam)"
```

---

## Task 3: Rewrite the recorder driver to run gflow's REAL creation flow

**Files:**
- Modify (REWRITE): `scripts/dev/record_flow_capture.py` (currently a standalone re-implementation that mis-fired into video — replace it)

Reuses `_spike_common.resolve_profile_dir/step`, the `RecordingFlowApiClient`, and mirrors `cli_character.py:159-185` wiring. Isolates the data store to a temp DB so the real catalog is untouched.

- [ ] **Step 1: Replace the driver body**

Key wiring (mirrors the CLI command, but through the recording client):

```python
# scripts/dev/record_flow_capture.py  (driver core — full file replaces the old one)
from __future__ import annotations

import argparse, asyncio, os, shutil, subprocess, sys, tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _recording_client import RecordingFlowApiClient  # noqa: E402
from _spike_common import default_out_path, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api.character import CharacterImageRequest  # noqa: E402
from gflow_cli.data.recorder import OperationRecorder  # noqa: E402
from gflow_cli.services.character_create import character_create  # noqa: E402
from gflow_cli.settings import Settings  # noqa: E402  (verify import path)

_DEFAULT_FACE = "a woman with short dark hair, round glasses, navy sweater, soft studio portrait"


async def _run(*, profile: str, profile_dir: Path, face_prompt: str,
               locale: str, out_path: Path) -> int:
    rec_dir = out_path.parent / "_rec_tmp"
    rec_dir.mkdir(parents=True, exist_ok=True)
    # Isolate the data store so the real gflow.db is untouched.
    db_path = Path(tempfile.mkdtemp(prefix="rec-db-")) / "catalog.db"
    os.environ["GFLOW_CLI_DB_PATH"] = str(db_path)
    settings = Settings()  # picks up the temp DB path

    face = CharacterImageRequest(prompt=face_prompt, model="nano2", image_reference_index=0)

    async with RecordingFlowApiClient(
        profile_dir=profile_dir, headless=False, settings=settings, record_video_dir=rec_dir,
    ) as client:
        proj = await client.create_project(title="gflow character — live demo")
        recorder = OperationRecorder.open(settings)
        step("rec", f"project={proj.project_id} — running real character_create", prefix="rec")
        await character_create(
            client, recorder,
            profile_name=profile, profile_dir=profile_dir,
            project_id=proj.project_id, name="Marina", face=face,
            locale=locale,  # short Flow URL segment handled by gflow; pass e.g. "pt"
        )
    # Context closed here -> webm finalized. Transcode newest webm to mp4.
    webms = sorted(rec_dir.glob("*.webm"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not webms:
        step("ERR", "no .webm produced", prefix="rec"); return 1
    _transcode(webms[0], out_path)
    shutil.rmtree(rec_dir, ignore_errors=True)
    step("done", f"recorded -> {out_path}", prefix="rec")
    return 0
```

Keep the existing `_transcode()` (webm→mp4 via ffmpeg, with no-ffmpeg fallback) and `main()` argparse (`--profile`, `--face-prompt`, `--locale` default `pt`, `--out`). Drop the old `_mint_entity`/`_type_prompt`/`_click_generate`/`_short_locale` re-implementation — `character_create` does the real UI now.

> **Verify import paths during implementation:** `Settings` location (`gflow_cli.settings` vs `gflow_cli.config`), `OperationRecorder.open` signature, and that `character_create`'s `locale` expects BCP-47 vs short code (it forwards to `character_editor_url`; confirm against #153 — pass whatever produces `/fx/pt/...`).

- [ ] **Step 2: Lint + format**

Run: `.venv\Scripts\python.exe -m ruff check scripts/dev/record_flow_capture.py && .venv\Scripts\python.exe -m ruff format scripts/dev/record_flow_capture.py`
Expected: clean.

- [ ] **Step 3: Live verification run (FREE — image gen only)**

Run:
```
$env:GFLOW_CLI_PROFILE='promo-denon82'
.venv\Scripts\python.exe scripts\dev\record_flow_capture.py --profile promo-denon82 --locale pt --out scripts\dev\_spike_out\flow-create.mp4
```
Expected console: `character_create.entity_created` → `prompt_submitted` → **`batchGenerateImages` 200** → `image_generated` → `character_create.completed`.

- [ ] **Step 4: Assert it created a CHARACTER (image, free), NOT a video**

Run: `gflow character list --project <the created project id>`
Expected: one character with **`refs=1`** (a face was generated). If `refs=0` or any video tile appears, STOP — the flow regressed to the general composer; do not proceed.

- [ ] **Step 5: Assert the video shows the creation (not black, not bouncing)**

Run: `ffmpeg -y -ss 6 -i scripts\dev\_spike_out\flow-create.mp4 -frames:v 1 frame.png` and inspect: empty editor → prompt typed → generated face appears; no scroll-bounce.
Expected: real editor footage of the creation.

- [ ] **Step 6: Commit**

```bash
git add scripts/dev/record_flow_capture.py
git commit -m "chore(dev): record gflow's real character-create flow (image/free) via RecordingFlowApiClient"
```

---

## Task 4: Integrate the capture into the promo (gflow-cli-remotion)

**Files (separate repo `C:\development\github\gflow-cli-remotion`, branch `feature/character-promo`):**
- `public/captures/character-master.mp4`, `src/remotion/Root.tsx`

- [ ] **Step 1:** Copy `flow-create.mp4` → `public/captures/character-master.mp4`.
- [ ] **Step 2:** Set `Root.tsx` defaultProps `captureFile: "captures/character-master.mp4"` (replace the placeholder restored in `8c9c1cb`).
- [ ] **Step 3:** Render: `pnpm exec remotion render CharacterCapturePromo out/character-capture/character-capture-9x16.mp4`
- [ ] **Step 4:** Extract a capture-window frame (~12s) and confirm it shows the creation footage.
- [ ] **Step 5:** Gates: `pnpm lint && pnpm exec tsc --noEmit && pnpm test:ci` (all green).
- [ ] **Step 6:** Commit + push `feature/character-promo`.

---

## Task 5: Gates, review, PR (gflow-cli)

- [ ] **Step 1:** `/gflow:check` (ruff fix + format, pyright `src`, pytest changed dirs).
- [ ] **Step 2:** `/gflow:branch-review` (lean council) on the recording branch; address must-fixes.
- [ ] **Step 3:** Open PR (plain-string body per CLAUDE.md MCP rule). Reference #153 (the locale normalization the recorder dogfoshes) and the gflow-cli-remotion PR #1.

---

## Self-Review

- **Spec coverage:** core seam (T1) ✓; out-of-core recording subclass (T2) ✓; record gflow's REAL flow not a re-implementation (T3) ✓; image/free guard via refs=1 assertion (T3.S4) ✓; no-bounce/creation-visible (T3.S5) ✓; clean separation — only core change is a behavior-preserving seam (T1) ✓; promo integration (T4) ✓; lean process plan+branch-review (T5) ✓; language-agnostic locale (#153) noted in T3.
- **Open verification (flagged inline, resolve at implementation):** (a) `Settings` import path; (b) `OperationRecorder.open` exact signature; (c) whether `character_create(locale=...)` wants BCP-47 or short code to yield `/fx/pt/...`; (d) confirm the recording client's single context is the one used (no second context) — the live run's `refs=1` + a non-black video is the end-to-end proof.
- **Type consistency:** `_persistent_context_kwargs()` name identical in T1 (define) and T2 (override); `RecordingFlowApiClient(record_video_dir=...)` ctor matches T2 test and T3 driver usage.
