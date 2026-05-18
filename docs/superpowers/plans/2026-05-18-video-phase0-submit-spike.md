# Video Phase 0: Submit-Mechanism Spike — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify against live Flow that video generation can be driven through the editor UI the way `generate_images` already is, and answer the open questions (spec §10.2 Q1, Q3, Q5, Q6, Q7) that gate Phase A and Phase B.

**Architecture:** A standalone diagnostic script `scripts/smoke_video_editor.py`, modeled on the existing `scripts/smoke_worker_style.py`. It drives a real authenticated Flow session, probes the video-editor selectors, fires one T2V generation, captures the `batchAsyncGenerateVideoText` response, and polls its status. This is a **spike** — the script is diagnostic tooling like the other `scripts/smoke_*.py` / `scripts/debug_*.py`, so (consistent with those files) it carries **no unit tests**. There are **no `src/` changes** in Phase 0. Verification is operator observation of structured logs against live Flow.

**Tech Stack:** Python 3.11+, Playwright (async), structlog, `uv`. The harness is reused verbatim from `scripts/smoke_worker_style.py`.

---

## Prerequisites & cost — READ FIRST

- Requires a **live, authenticated Google AI Ultra/Pro Flow account**. The script opens a headed Chromium against `--profile-dir`; the operator signs in once, manually, in that window (the script polls until detected — no stdin).
- Task 4 fires **one real T2V generation — this spends Veo credits**. Task 6's optional I2V run spends more. Do not run repeatedly without reason.
- This spike **cannot run in CI** (needs a real session). It is operator-run.
- All runtime output (screenshots, captured JSON) goes under `tmp/` per the repo output-path rule.

## File Structure

| File | Responsibility |
|---|---|
| `scripts/smoke_video_editor.py` | **New.** Diagnostic spike script — drives the Flow video editor, probes selectors, fires + polls one T2V generation. Built incrementally across Tasks 1-6. |
| `docs/superpowers/specs/2026-05-18-ui-automation-video-generation-design.md` | **Modified** in Task 6 — §10.2 questions Q1/Q3/Q5/Q6/Q7 marked resolved with the spike's answers. |

The harness functions are reused verbatim from `scripts/smoke_worker_style.py`; only the video-specific drive logic is new.

---

## Task 1: Scaffold the diagnostic script harness

**Files:**
- Create: `scripts/smoke_video_editor.py`

- [ ] **Step 1: Create the file with the reused harness**

Create `scripts/smoke_video_editor.py`. Copy these symbols **verbatim** from `scripts/smoke_worker_style.py` (they are unchanged): the imports block (`argparse`, `asyncio`, `time`, `pathlib.Path`, `httpx`→drop, `structlog`, `playwright.async_api` — keep `BrowserContext, Page, Response, async_playwright`), `log`, `FLOW_URL`, `PROMPT_INPUT_SELECTORS`, `SUBMIT_BUTTON_SELECTORS`, `_check_logged_in`, `_ensure_logged_in_to_flow`, `_enter_editor`, `_send_prompt`, `run`, and `main`. Add `import json` to the imports. In `run`, replace the `_drive(...)` call with `_drive_spike(...)`.

Then add the new module docstring at the top and a stub drive function:

```python
"""Spike — drive the Flow VIDEO editor and answer the Phase 0 open questions.

Diagnostic tooling (like scripts/smoke_worker_style.py) for the video-generation
spike. Modeled on smoke_worker_style.py: launch_persistent_context, manual
sign-in poll, gallery -> editor. Then probes the video-mode selectors, fires one
T2V generation, captures batchAsyncGenerateVideoText, and polls the result.

SPENDS CREDITS (one T2V generation). Requires a live Flow account.

Usage::

    uv run python scripts/smoke_video_editor.py \\
        --profile-dir ~/gflow-video-spike \\
        --prompt "a calm forest at dawn, cinematic"
"""


async def _drive_spike(context: BrowserContext, prompt_text: str, out_dir: Path) -> None:
    page = context.pages[0] if context.pages else await context.new_page()
    await _ensure_logged_in_to_flow(page, out_dir)
    await _enter_editor(page, out_dir)
    project_id = page.url.split("/project/")[1].split("?")[0]
    log.info("spike_editor_ready", project_id=project_id, url=page.url)
```

- [ ] **Step 2: Run it against live Flow**

Run: `uv run python scripts/smoke_video_editor.py --profile-dir $HOME/gflow-video-spike`
Sign in inside the Chromium window when prompted.
Expected: log line `spike_editor_ready` with a `project_id` and a `/project/<uuid>` URL.

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke_video_editor.py
git commit -m "chore(spike): scaffold video-editor diagnostic script"
```

---

## Task 2: Probe the video-mode tab and sub-tabs

**Files:**
- Modify: `scripts/smoke_video_editor.py`

- [ ] **Step 1: Add the selector-probe helper and video selectors**

Add to `scripts/smoke_video_editor.py`:

```python
# Spec §6 — unverified guesses; this spike confirms which (if any) match.
VIDEO_MODE_TAB_SELECTORS = (
    "button:has(i:text('play_circle'))",
    "[role='tab']:has-text('Video')",
)
FRAMES_SUBTAB_SELECTORS = (
    "[role='tab']:has-text('Frames')",
    "button:has-text('Frames')",
)
ELEMENTOS_SUBTAB_SELECTORS = (
    "[role='tab']:has-text('Elements')",
    "button:has-text('Elements')",
)


async def _probe(page: Page, label: str, candidates: tuple[str, ...], timeout_ms: int = 4000):
    """Try each selector; return (locator, selector) for the first visible match,
    else (None, None). Logs every attempt so the operator sees which won."""
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=timeout_ms)
            log.info("selector_matched", probe=label, selector=sel)
            return loc, sel
        except Exception:  # noqa: BLE001
            log.info("selector_miss", probe=label, selector=sel)
    log.warning("selector_probe_failed", probe=label, tried=list(candidates))
    return None, None
```

- [ ] **Step 2: Extend `_drive_spike` to probe and switch to video mode**

Append to `_drive_spike` after `log.info("spike_editor_ready", ...)`:

```python
    video_tab, _ = await _probe(page, "video_mode_tab", VIDEO_MODE_TAB_SELECTORS)
    if video_tab is None:
        await page.screenshot(path=str(out_dir / "no_video_tab.png"), full_page=True)
        raise RuntimeError("Video mode tab not found — see screenshot, update §6 selectors")
    await video_tab.click()
    await page.wait_for_timeout(1500)
    log.info("video_mode_entered")

    frames, _ = await _probe(page, "frames_subtab", FRAMES_SUBTAB_SELECTORS)
    elementos, _ = await _probe(page, "elementos_subtab", ELEMENTOS_SUBTAB_SELECTORS)
    log.info("subtab_probe_done", frames_found=frames is not None,
             elementos_found=elementos is not None)
```

- [ ] **Step 3: Run and record**

Run: `uv run python scripts/smoke_video_editor.py --profile-dir $HOME/gflow-video-spike`
Expected: `selector_matched` for `video_mode_tab`; `subtab_probe_done` shows whether Frames/Elementos were found.
Record the winning selectors (or "none — DOM differs") in a scratch note for Task 6's findings write-up. If a probe fails, inspect `tmp/.../no_video_tab.png` and the live DOM and note the correct selector.

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke_video_editor.py
git commit -m "chore(spike): probe video-mode tab and Frames/Elementos sub-tabs"
```

---

## Task 3: Probe the video aspect-ratio control (answers Q5 — SQUARE)

**Files:**
- Modify: `scripts/smoke_video_editor.py`

- [ ] **Step 1: Add the aspect-ratio probe**

Add to `scripts/smoke_video_editor.py`:

```python
# Reuse the image-editor settings trigger; confirm it exists for video too.
GEN_SETTINGS_BUTTON_SELECTORS = (
    "button:has(i.google-symbols:text('crop_16_9'))",
    "button:has(i.google-symbols:text('crop_9_16'))",
    "button:has(i.google-symbols:text('crop_square'))",
)
ASPECT_TAB_CANDIDATES = {"portrait": "9:16", "landscape": "16:9", "square": "1:1"}


async def _probe_aspect_options(page: Page) -> None:
    """Open the settings panel and log which aspect-ratio tabs the video editor offers."""
    btn, _ = await _probe(page, "gen_settings_button", GEN_SETTINGS_BUTTON_SELECTORS)
    if btn is None:
        log.warning("aspect_probe_skipped", reason="settings button not found in video mode")
        return
    await btn.click()
    await page.wait_for_timeout(600)
    for name, text in ASPECT_TAB_CANDIDATES.items():
        count = await page.locator(f'[role="tab"]:has-text("{text}")').count()
        log.info("aspect_option_probe", aspect=name, tab_text=text, present=count > 0)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(400)
```

- [ ] **Step 2: Call it from `_drive_spike`**

Append to `_drive_spike` after the sub-tab probe:

```python
    await _probe_aspect_options(page)
```

- [ ] **Step 3: Run and record**

Run: `uv run python scripts/smoke_video_editor.py --profile-dir $HOME/gflow-video-spike`
Expected: three `aspect_option_probe` lines. Record `present` for each — especially `square`. **This answers §10.2 Q5.**

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke_video_editor.py
git commit -m "chore(spike): probe video aspect-ratio options"
```

---

## Task 4: Fire one T2V generation and capture the response (core mechanism check)

**Files:**
- Modify: `scripts/smoke_video_editor.py`

- [ ] **Step 1: Add the generate-response listener**

Add to `scripts/smoke_video_editor.py`:

```python
VIDEO_GENERATE_ROUTES = (
    "batchAsyncGenerateVideoText",
    "batchAsyncGenerateVideoStartAndEndImage",
    "batchAsyncGenerateVideoReferenceImages",
)


async def _capture_video_generate(page: Page, timeout_s: int = 150) -> dict:
    """Capture the first batchAsyncGenerateVideo* response. Mirrors the
    _capture_batch_response pattern in smoke_worker_style.py."""
    captured: list[dict] = []

    async def on_response(response: Response) -> None:
        if not any(r in response.url for r in VIDEO_GENERATE_ROUTES):
            return
        try:
            captured.append({"status": response.status, "url": response.url,
                              "body": await response.json()})
            log.info("video_generate_captured", status=response.status, url=response.url)
        except Exception as e:  # noqa: BLE001
            log.warning("video_generate_parse_failed", error=str(e))

    page.on("response", on_response)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and not captured:
        await asyncio.sleep(0.5)
    page.remove_listener("response", on_response)
    if not captured:
        raise TimeoutError(
            f"No batchAsyncGenerateVideo* response within {timeout_s}s — "
            "did the submit fire? did reCAPTCHA fail silently?"
        )
    return captured[0]
```

- [ ] **Step 2: Fire T2V in `_drive_spike` and save the response**

Append to `_drive_spike` (the editor is already in video mode from Task 2; T2V is the default sub-mode — no image inputs):

```python
    # T2V: video mode is active; send the prompt and capture the response.
    await _send_prompt(page, prompt_text, out_dir)
    generate_resp = await _capture_video_generate(page)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "t2v_generate_response.json").write_text(
        json.dumps(generate_resp, indent=2), encoding="utf-8")
    body = generate_resp["body"]
    media_name = body["media"][0]["name"]
    log.info("t2v_generated", status=generate_resp["status"], media_name=media_name,
             remaining_credits=body.get("remainingCredits"),
             route=generate_resp["url"].split("?")[0].rsplit("/", 1)[-1])
```

- [ ] **Step 3: Run (SPENDS CREDITS) and verify**

Run: `uv run python scripts/smoke_video_editor.py --profile-dir $HOME/gflow-video-spike --prompt "a calm forest at dawn, cinematic"`
Expected: `video_generate_captured` with `status=200`; `t2v_generated` logs a `media_name` and the route ends in `batchAsyncGenerateVideoText`. Confirms the UI-drive mechanism works for video. If the route differs, record it.

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke_video_editor.py
git commit -m "chore(spike): fire one T2V generation and capture the response"
```

---

## Task 5: Verify the T2V poll handle (answers Q7)

**Files:**
- Modify: `scripts/smoke_video_editor.py`

- [ ] **Step 1: Add the status poll**

Add to `scripts/smoke_video_editor.py`:

```python
STATUS_URL = ("https://aisandbox-pa.googleapis.com/v1/"
              "video:batchCheckAsyncVideoGenerationStatus")


async def _check_status(page: Page, media_name: str, project_id: str) -> dict:
    """POST batchCheckAsyncVideoGenerationStatus via the browser context
    (no reCAPTCHA token needed — spec §2.3) and return the parsed body."""
    resp = await page.request.post(
        STATUS_URL,
        data=json.dumps({"media": [{"name": media_name, "projectId": project_id}]}),
        headers={"content-type": "text/plain;charset=UTF-8"},
    )
    body = await resp.json()
    log.info("status_checked", http_status=resp.status, media_name=media_name)
    return {"http_status": resp.status, "body": body}
```

- [ ] **Step 2: Call it from `_drive_spike`**

Append to `_drive_spike`:

```python
    status = await _check_status(page, media_name, project_id)
    (out_dir / "t2v_status_response.json").write_text(
        json.dumps(status, indent=2), encoding="utf-8")
    media = status["body"].get("media", [])
    matched = bool(media) and media[0].get("name") == media_name
    gen_status = (media[0].get("mediaMetadata", {}).get("mediaStatus", {})
                  .get("mediaGenerationStatus") if media else None)
    log.info("poll_handle_verified", http_status=status["http_status"],
             media_name_matched=matched, media_generation_status=gen_status)
```

- [ ] **Step 3: Run and record**

Run: `uv run python scripts/smoke_video_editor.py --profile-dir $HOME/gflow-video-spike`
Expected: `poll_handle_verified` with `http_status=200`, `media_name_matched=true`, and a `media_generation_status` (e.g. `MEDIA_GENERATION_STATUS_PENDING`/`SCHEDULED`/`ACTIVE`). A `true` + a real status **confirms `media[0].name` is the T2V poll handle — §10.2 Q7.** If `matched=false`, inspect `t2v_generate_response.json` for the alternative id (`operations[0].operation.name`, `workflows[0].metadata.primaryMediaId`) and record which one the status endpoint accepts.

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke_video_editor.py
git commit -m "chore(spike): verify the T2V status poll handle"
```

---

## Task 6: Probe image-mode attachment and record findings (Q1, Q3, Q6)

**Files:**
- Modify: `scripts/smoke_video_editor.py`
- Modify: `docs/superpowers/specs/2026-05-18-ui-automation-video-generation-design.md`

- [ ] **Step 1: Add the Frames/Elementos attachment probe**

Add to `scripts/smoke_video_editor.py`:

```python
CATALOG_TRIGGER_SELECTORS = (
    "button:has-text('Inicial')", "button:has-text('Initial')",
    "button:has-text('Start')", "button:has(i:text('add'))",
    "button[aria-label*='Add' i]",
)


async def _probe_image_attachment(page: Page, out_dir: Path) -> None:
    """Frames mode: probe the catalog/file-picker trigger (Q1). Elementos mode:
    probe the add-reference control and how many references are allowed (Q6)."""
    frames, _ = await _probe(page, "frames_subtab", FRAMES_SUBTAB_SELECTORS)
    if frames is not None:
        await frames.click()
        await page.wait_for_timeout(1200)
        trigger, sel = await _probe(page, "frames_catalog_trigger", CATALOG_TRIGGER_SELECTORS)
        log.info("frames_attachment_probe", catalog_trigger=sel)
        await page.screenshot(path=str(out_dir / "frames_mode.png"), full_page=True)

    elementos, _ = await _probe(page, "elementos_subtab", ELEMENTOS_SUBTAB_SELECTORS)
    if elementos is not None:
        await elementos.click()
        await page.wait_for_timeout(1200)
        add, sel = await _probe(page, "elementos_add_reference", CATALOG_TRIGGER_SELECTORS)
        log.info("elementos_attachment_probe", add_trigger=sel)
        await page.screenshot(path=str(out_dir / "elementos_mode.png"), full_page=True)
```

- [ ] **Step 2: Call it, run, and record**

Append `await _probe_image_attachment(page, out_dir)` to `_drive_spike`.
Run: `uv run python scripts/smoke_video_editor.py --profile-dir $HOME/gflow-video-spike`
From the logs + the `frames_mode.png` / `elementos_mode.png` screenshots, record: the catalog/file-picker mechanism (does clicking the trigger open a Playwright `file_chooser`, or an in-page catalog dialog?) — **§10.2 Q1**; and how many reference slots Elementos exposes — **§10.2 Q6**. For **Q3** (start-only I2V), optionally attach one image to the Frames "Start" slot, leave "End" empty, submit, and observe whether the generate is accepted (spends credits — operator's call).

- [ ] **Step 3: Write the findings into the spec**

In `docs/superpowers/specs/2026-05-18-ui-automation-video-generation-design.md`, edit §10.2: for each of Q1, Q3, Q5, Q6, Q7, append a `**Resolved (Phase 0):** <answer>` line stating the observed result. Move any fully-answered question's substance into §10.1 if appropriate. Update §6 with the selector(s) the spike confirmed (replace the "unverified guesses" note with the verified selectors).

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke_video_editor.py docs/superpowers/specs/2026-05-18-ui-automation-video-generation-design.md
git commit -m "chore(spike): probe image attachment; record Phase 0 findings in the spec"
```

---

## Done criteria

Phase 0 is complete when:
- `scripts/smoke_video_editor.py` runs end-to-end against live Flow: enters the editor, switches to video mode, fires a T2V generation, and polls its status.
- §10.2 Q1, Q3, Q5, Q6, Q7 each have a `**Resolved (Phase 0):**` answer in the spec.
- §6 selectors are updated to the spike-verified values.
- The core finding is confirmed: video generation **can** be driven through the UI exactly like `generate_images` (or, if not, the deviation is documented and the spec/Phase A plan is revised before Phase A starts).

If the spike disproves the UI-drive assumption, **stop** — re-open the spec design before planning Phase A.

---

## Self-Review

- **Spec coverage:** Phase 0 per spec §10.3 = "drive the editor, fire one T2V `batchAsyncGenerateVideoText`, capture the response; validate §6 selectors; answer Q1/Q3/Q5/Q6/Q7." Mapped: T2V fire+capture → Task 4; §6 selector validation → Tasks 2-3, 6; Q5 → Task 3; Q7 → Task 5; Q1/Q6/Q3 → Task 6. Covered.
- **Placeholder scan:** none — every step has runnable code or an exact command.
- **Type/name consistency:** `_drive_spike`, `_probe`, `_capture_video_generate`, `_check_status`, `_probe_image_attachment`, `media_name`, `project_id` used consistently across tasks; `out_dir` threaded from `run` (reused from `smoke_worker_style.py`).
