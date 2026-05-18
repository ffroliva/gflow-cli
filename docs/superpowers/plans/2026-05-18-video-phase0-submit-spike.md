# Video Phase 0: Submit-Mechanism Spike — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Rev 3 (+ hardening)** — revised across 3 council-review rounds; round 3 reached consensus (4× APPROVE). Rev 2 fixed the round-1 blockers (Task 1 signature crash; listener-before-submit); rev 3 made the Q7 conclusion deterministic (UUID-collapse aware) and added an I2V reuse guard. The one residual round-3 minor is folded in: the reuse-guard capture files are written atomically (`*.tmp` + rename) and read defensively, so a crash mid-write cannot break re-run recovery.

**Goal:** Verify against live Flow that video generation can be driven through the editor UI the way `generate_images` already is, and answer the open questions (spec §10.2 Q1, Q3, Q5, Q6, Q7) that gate Phase A and Phase B.

**Architecture:** A standalone diagnostic script `scripts/smoke_video_editor.py`, modeled on the existing `scripts/smoke_worker_style.py`. It drives a real authenticated Flow session, probes the video-editor selectors, fires one T2V generation, verifies the status poll handle, and probes image attachment. This is a **spike** — the script is diagnostic tooling like the other `scripts/smoke_*.py` / `scripts/debug_*.py`, so (consistent with those files) it carries **no unit tests**. There are **no `src/` changes** in Phase 0. Verification is operator observation of structured logs against live Flow; observations are appended to a durable findings file as each task runs.

**Tech Stack:** Python 3.11+, Playwright (async), structlog, `uv`. The login/editor harness is reused verbatim from `scripts/smoke_worker_style.py`.

---

## Prerequisites & cost — READ FIRST

- Requires a **live, authenticated Google AI Ultra/Pro Flow account**. The script opens a headed Chromium against `--profile-dir`; the operator signs in once, manually, in that window (the script polls until detected — no stdin).
- The spike fires **two real generations** that **spend Veo credits**: one T2V (Task 4) and one I2V (Task 6). Do not run repeatedly without reason.
- **Re-running cheaply:** Task 4 writes `t2v_generate_response.json` and Task 6 writes `i2v_startonly_response.json` into `--out`. Re-run with `--out <the same dir>` to reuse **both** captured generations and skip the paid steps — useful after a crash. A fresh `--out` always re-spends.
- **Profile — real-Chrome auth is MANDATORY.** The spike drives Google's Flow UI; automated / bundled-Chromium browsers are rejected by Google sign-in (the "G12 block"). `--profile-dir` MUST point at a profile authenticated via **real Chrome** — run `gflow auth login --profile <name> --browser chrome` first (it writes the `.gflow_browser_strategy` marker + a complete session). The spike **fails fast** if the profile lacks that marker; you cannot sign in inside the spike's browser. In every command below, replace `$HOME/gflow-video-spike` with your authenticated `gflow` profile dir (e.g. `…/ffroliva/gflow-cli/profile_<name>`).
- This spike **cannot run in CI** (needs a real session). It is operator-run.
- All runtime output (screenshots, captured JSON, the findings file) goes under `tmp/` per the repo output-path rule.

## File Structure

| File | Responsibility |
|---|---|
| `scripts/smoke_video_editor.py` | **New.** Diagnostic spike script — drives the Flow video editor, probes selectors, fires + verifies one T2V generation, probes image attachment + one I2V generation. Built incrementally across Tasks 1-6. |
| `tmp/video-spike/<utc>/phase0_findings.md` | **Generated at runtime.** Durable findings log — each task appends to it so observations survive a mid-run crash. |
| `docs/superpowers/specs/2026-05-18-ui-automation-video-generation-design.md` | **Modified** in Task 6 — §10.2 questions Q1/Q3/Q5/Q6/Q7 marked resolved with the spike's answers; §6 selectors updated to verified values. |

The harness functions are reused verbatim from `scripts/smoke_worker_style.py`; only the video-specific drive logic is new.

---

## Task 1: Scaffold the diagnostic script

**Files:**
- Create: `scripts/smoke_video_editor.py`

- [ ] **Step 1: Create the file — reused harness**

Create `scripts/smoke_video_editor.py`. Copy these symbols **verbatim** from `scripts/smoke_worker_style.py` (unchanged): `log`, `FLOW_URL`, `PROMPT_INPUT_SELECTORS`, `SUBMIT_BUTTON_SELECTORS`, `_check_logged_in`, `_ensure_logged_in_to_flow`, `_enter_editor`, `_send_prompt`. Do **not** copy `_drive`, `run`, `main`, `_download`, `_extract_image_urls`, `_capture_batch_response`, `_configure_generation_settings`, `GEN_SETTINGS_BUTTON_SELECTORS`, `ASPECT_RATIO_MAP`, `COUNT_TAB_MAP`, `NEW_PROJECT_SELECTORS` (— `_enter_editor` already carries `NEW_PROJECT_SELECTORS`'s use; copy `NEW_PROJECT_SELECTORS` too since `_enter_editor` references it).

Use exactly this imports block (note: `httpx` is **not** imported — nothing copied uses it; `json` **is**):

```python
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import structlog
from playwright.async_api import BrowserContext, Locator, Page, Response, async_playwright
```

Add this module docstring at the very top of the file:

```python
"""Spike — drive the Flow VIDEO editor and answer the Phase 0 open questions.

Diagnostic tooling (like scripts/smoke_worker_style.py) for the video-generation
spike. Modeled on smoke_worker_style.py: launch_persistent_context, manual
sign-in poll, gallery -> editor. Then probes the video-mode selectors, fires one
T2V generation, verifies the status poll handle, and probes image attachment.

SPENDS CREDITS — one T2V generation (Task 4) and one I2V generation (Task 6).
Re-run with --out <prior dir> to reuse a captured generation and skip the paid
T2V step. Requires a live Flow account.

Usage::

    uv run python scripts/smoke_video_editor.py \\
        --profile-dir ~/gflow-video-spike \\
        --prompt "a calm forest at dawn, cinematic"
"""
```

> Note: the script is assembled across Steps 1-3; several imports are unused until later steps land their code. Run lint only after Step 3 — the Task 1 commit (Step 5) is clean.

- [ ] **Step 2: Add the durable findings recorder and the drive stub**

Append to `scripts/smoke_video_editor.py`:

```python
def _record(out_dir: Path, line: str) -> None:
    """Append a line to the durable findings file so observations survive a crash."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "phase0_findings.md").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


async def _drive_spike(context: BrowserContext, prompt_text: str, out_dir: Path) -> None:
    page = context.pages[0] if context.pages else await context.new_page()
    await _ensure_logged_in_to_flow(page, out_dir)
    await _enter_editor(page, out_dir)
    project_id = page.url.split("/project/")[1].split("?")[0]
    _record(out_dir, f"# Phase 0 spike findings\n\nproject_id: {project_id}\n")
    log.info("spike_editor_ready", project_id=project_id, url=page.url)
```

- [ ] **Step 3: Add the trimmed `run` and `main`**

Append to `scripts/smoke_video_editor.py`. These are **new** (not copied) — trimmed for the spike: no `expected_count`/`aspect_ratio`, no `--count`/`--aspect-ratio`:

```python
async def run(profile_dir: Path, prompt_text: str, out_dir: Path) -> None:
    """Drive the spike using Playwright's persistent context (Worker pattern)."""
    log.info("launching_persistent_context", profile_dir=str(profile_dir))
    async with async_playwright() as pw:
        from gflow_cli.browser_manager import channel_for_profile
        channel = channel_for_profile(profile_dir)
        context = await pw.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            channel=channel,
            ignore_default_args=["--enable-automation", "--no-sandbox"],
            args=["--disable-blink-features=AutomationControlled"],
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """)
        try:
            await _drive_spike(context, prompt_text, out_dir)
        finally:
            await context.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile-dir", type=Path, default=Path.home() / "gflow-video-spike",
        help="Playwright Chromium user-data-dir (default: $HOME/gflow-video-spike)",
    )
    parser.add_argument(
        "--prompt", default="a calm forest at dawn, cinematic",
        help="T2V prompt to generate",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Output dir (default: tmp/video-spike/<utc>). Re-pass a prior dir "
        "to reuse its captured T2V generation and skip the paid generate step.",
    )
    args = parser.parse_args()
    out_dir = args.out or (
        Path("tmp") / "video-spike" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    )
    args.profile_dir.mkdir(parents=True, exist_ok=True)
    asyncio.run(run(args.profile_dir, args.prompt, out_dir))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run it against live Flow**

Run: `uv run python scripts/smoke_video_editor.py --profile-dir $HOME/gflow-video-spike`
Sign in inside the Chromium window when prompted.
Expected: log line `spike_editor_ready` with a `project_id` and a `/project/<uuid>` URL; `tmp/video-spike/<utc>/phase0_findings.md` created.

- [ ] **Step 5: Commit**

```bash
git add scripts/smoke_video_editor.py
git commit -m "chore(spike): scaffold video-editor diagnostic script"
```

---

## Task 2: Probe the video-mode tab and sub-tabs

**Files:**
- Modify: `scripts/smoke_video_editor.py`

- [ ] **Step 1: Add the selector-probe helper and video selectors**

Append to `scripts/smoke_video_editor.py`:

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


async def _probe(page: Page, label: str, candidates: tuple[str, ...],
                 timeout_ms: int = 4000) -> tuple[Locator | None, str | None]:
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

- [ ] **Step 2: Extend `_drive_spike` to switch to video mode and probe sub-tabs**

Append to `_drive_spike`, after `log.info("spike_editor_ready", ...)`:

```python
    video_tab, video_sel = await _probe(page, "video_mode_tab", VIDEO_MODE_TAB_SELECTORS)
    if video_tab is None:
        await page.screenshot(path=str(out_dir / "no_video_tab.png"), full_page=True)
        _record(out_dir, "- video_mode_tab: NOT FOUND — see no_video_tab.png; update §6")
        raise RuntimeError("Video mode tab not found — see screenshot, update §6 selectors")
    await video_tab.click()
    await page.wait_for_timeout(1500)
    log.info("video_mode_entered")

    _, frames_sel = await _probe(page, "frames_subtab", FRAMES_SUBTAB_SELECTORS)
    _, elementos_sel = await _probe(page, "elementos_subtab", ELEMENTOS_SUBTAB_SELECTORS)
    _record(out_dir, f"- §6 video_mode_tab selector: {video_sel}")
    _record(out_dir, f"- §6 frames_subtab selector: {frames_sel}")
    _record(out_dir, f"- §6 elementos_subtab selector: {elementos_sel}")
```

- [ ] **Step 3: Run and verify**

Run: `uv run python scripts/smoke_video_editor.py --profile-dir $HOME/gflow-video-spike`
Expected: `selector_matched` for `video_mode_tab`; `video_mode_entered`; the three `§6 ... selector:` lines appended to `phase0_findings.md`. If a probe fails (`selector_probe_failed`), inspect `no_video_tab.png` and the live DOM, note the correct selector, and update `VIDEO_MODE_TAB_SELECTORS` / `FRAMES_SUBTAB_SELECTORS` / `ELEMENTOS_SUBTAB_SELECTORS` before continuing.

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke_video_editor.py
git commit -m "chore(spike): probe video-mode tab and Frames/Elementos sub-tabs"
```

---

## Task 3: Probe the video aspect-ratio control (answers Q5 — SQUARE)

**Files:**
- Modify: `scripts/smoke_video_editor.py`

- [ ] **Step 1: Add a multi-shape aspect probe**

The video editor may render aspect ratio as tabs, menu items, or plain buttons — probing only `[role="tab"]` risks a false negative. Append to `scripts/smoke_video_editor.py`:

```python
# The settings trigger shows the current ratio icon; enumerate the icon names.
ASPECT_SETTINGS_TRIGGER_SELECTORS = (
    "button:has(i.google-symbols:text('crop_16_9'))",
    "button:has(i.google-symbols:text('crop_9_16'))",
    "button:has(i.google-symbols:text('crop_square'))",
    "button:has(i.google-symbols:text('aspect_ratio'))",
)
ASPECT_OPTIONS = {"portrait": "9:16", "landscape": "16:9", "square": "1:1"}


async def _probe_aspect_options(page: Page, out_dir: Path) -> None:
    """Open the settings panel; report which aspect ratios the video editor offers
    and the control shape (tab / menuitem / button)."""
    btn, _ = await _probe(page, "aspect_settings_trigger", ASPECT_SETTINGS_TRIGGER_SELECTORS)
    if btn is None:
        log.warning("aspect_probe_skipped", reason="settings trigger not found in video mode")
        _record(out_dir, "- Q5 aspect: settings trigger NOT FOUND — probe inconclusive")
        return
    await btn.click()
    await page.wait_for_timeout(700)
    await page.screenshot(path=str(out_dir / "aspect_panel.png"), full_page=True)
    for name, text in ASPECT_OPTIONS.items():
        shapes = {
            "tab": f'[role="tab"]:has-text("{text}")',
            "menuitem": f'[role="menuitem"]:has-text("{text}")',
            "button": f'button:has-text("{text}")',
        }
        found_as = []
        for shape, sel in shapes.items():
            if await page.locator(sel).count() > 0:
                found_as.append(shape)
        log.info("aspect_option_probe", aspect=name, tab_text=text, found_as=found_as)
        _record(out_dir, f"- Q5 aspect {name} ({text}): present as {found_as or 'NOT FOUND'}")
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(400)
```

- [ ] **Step 2: Call it from `_drive_spike`**

Append to `_drive_spike`:

```python
    await _probe_aspect_options(page, out_dir)
```

- [ ] **Step 3: Run and verify**

Run: `uv run python scripts/smoke_video_editor.py --profile-dir $HOME/gflow-video-spike`
Expected: three `aspect_option_probe` lines + `Q5 aspect ...` findings lines; `aspect_panel.png` written. **This answers §10.2 Q5** — `square` present (as any shape) → SQUARE is offered for video; absent everywhere → it is not. Cross-check against `aspect_panel.png` before trusting a negative.

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke_video_editor.py
git commit -m "chore(spike): probe video aspect-ratio options"
```

---

## Task 4: Fire one T2V generation and capture the response (core mechanism check)

**Files:**
- Modify: `scripts/smoke_video_editor.py`

- [ ] **Step 1: Add the response listener (attached BEFORE submit)**

The listener must be registered before the prompt is submitted, or a fast response is missed (mirrors `ui_automation.py:833-838`). Append to `scripts/smoke_video_editor.py`:

```python
VIDEO_GENERATE_ROUTES = (
    "batchAsyncGenerateVideoText",
    "batchAsyncGenerateVideoStartAndEndImage",
    "batchAsyncGenerateVideoReferenceImages",
)


def _attach_video_listener(page: Page):
    """Register a page response listener for the batchAsyncGenerateVideo* routes
    BEFORE the prompt is submitted. Returns (captured_list, handler)."""
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
    return captured, on_response


async def _await_capture(page: Page, captured: list[dict], handler, timeout_s: int = 150) -> dict:
    """Wait for the first captured video-generate response, then detach the listener."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and not captured:
        await asyncio.sleep(0.5)
    page.remove_listener("response", handler)
    if not captured:
        raise TimeoutError(
            f"No batchAsyncGenerateVideo* response within {timeout_s}s — "
            "did the submit fire? did reCAPTCHA fail silently?"
        )
    return captured[0]


def _save_capture(path: Path, obj: dict) -> None:
    """Write a captured response atomically — a crash mid-write cannot leave a
    corrupt reuse file (write to *.tmp, then atomic rename)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_reuse(path: Path) -> dict | None:
    """Load a prior captured response for re-run reuse. Returns None if the file
    is absent or corrupt (e.g. a crash mid-write) — the caller then re-fires."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("reuse_file_corrupt", path=str(path), error=str(e))
        return None
```

- [ ] **Step 2: Fire T2V in `_drive_spike` (or reuse a prior capture); guard a rejected response**

Append to `_drive_spike`. The editor is already in video mode (Task 2); T2V is the default sub-mode (no image inputs). A non-200 / no-`media` response is **recorded data, not a crash**:

```python
    resp_path = out_dir / "t2v_generate_response.json"
    generate_resp = _load_reuse(resp_path)
    if generate_resp is not None:
        log.info("t2v_generate_reused", path=str(resp_path))
    else:
        captured, handler = _attach_video_listener(page)
        await _send_prompt(page, prompt_text, out_dir)
        generate_resp = await _await_capture(page, captured, handler)
        _save_capture(resp_path, generate_resp)

    body = generate_resp.get("body", {})
    http_status = generate_resp.get("status")
    media = body.get("media") or []
    media_name = media[0].get("name") if media else None
    route = generate_resp.get("url", "").split("?")[0].rsplit("/", 1)[-1]

    if http_status != 200 or not media_name:
        failure_reasons: list[str] = []
        for m in media:
            ms = (m.get("mediaMetadata") or {}).get("mediaStatus") or {}
            failure_reasons += ms.get("failureReasons") or []
        log.warning("t2v_generate_rejected", http_status=http_status,
                     error=body.get("error"), failure_reasons=failure_reasons)
        _record(out_dir, f"- T2V generate REJECTED: http={http_status} "
                         f"reasons={failure_reasons} error={body.get('error')}")
    else:
        log.info("t2v_generated", http_status=http_status, route=route,
                 media_name=media_name, remaining_credits=body.get("remainingCredits"))
        _record(out_dir, f"- T2V generate OK: route={route} media_name={media_name} "
                         f"credits_left={body.get('remainingCredits')}")
```

- [ ] **Step 3: Run (SPENDS CREDITS) and verify**

Run: `uv run python scripts/smoke_video_editor.py --profile-dir $HOME/gflow-video-spike --prompt "a calm forest at dawn, cinematic"`
Expected: `video_generate_captured` (`status=200`); `t2v_generated` with a `media_name` and `route=batchAsyncGenerateVideoText`. This confirms the UI-drive mechanism works for video. If `t2v_generate_rejected` fires instead, that is still a valid Phase 0 finding — record the `failure_reasons` and continue (Task 5 will skip cleanly). **Note the `tmp/video-spike/<utc>/` directory printed in the log** — re-pass it as `--out` to Tasks 5-6 to reuse this generation without re-spending credits.

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke_video_editor.py
git commit -m "chore(spike): fire one T2V generation with listener attached before submit"
```

---

## Task 5: Verify the T2V poll handle — test all three candidates (answers Q7)

**Files:**
- Modify: `scripts/smoke_video_editor.py`

A bare 200 from the status endpoint only proves an id is *valid*, not that it is *the* poll handle. Spec §2.4 names three candidates; the spike POSTs each and reports which returns a populated status.

- [ ] **Step 1: Add the status-poll helper**

Append to `scripts/smoke_video_editor.py`:

```python
STATUS_URL = ("https://aisandbox-pa.googleapis.com/v1/"
              "video:batchCheckAsyncVideoGenerationStatus")


async def _check_status(page: Page, candidate_id: str, project_id: str) -> dict:
    """POST batchCheckAsyncVideoGenerationStatus via the browser context
    (no reCAPTCHA token needed — spec §2.3). Returns {http_status, body}."""
    resp = await page.request.post(
        STATUS_URL,
        data=json.dumps({"media": [{"name": candidate_id, "projectId": project_id}]}),
        headers={"content-type": "text/plain;charset=UTF-8"},
    )
    try:
        parsed = await resp.json()
    except Exception:  # noqa: BLE001
        parsed = {}
    return {"http_status": resp.status, "body": parsed}
```

- [ ] **Step 2: Probe all three candidate handles in `_drive_spike`**

Append to `_drive_spike`. This is a single poll per candidate — handle *identification* only, not a polling loop (terminal-status observation is Phase A):

```python
    if media_name is None:
        log.warning("poll_handle_check_skipped", reason="generate returned no media")
        _record(out_dir, "- Q7 poll handle: SKIPPED (T2V generate was rejected)")
    else:
        operations = body.get("operations") or []
        workflows = body.get("workflows") or []
        # Every candidate id spec §2.4 names, by source label.
        candidates: dict[str, str | None] = {"media[0].name": media_name}
        if operations:
            candidates["operations[0].operation.name"] = (
                operations[0].get("operation") or {}).get("name")
        if workflows:
            candidates["workflows[0].metadata.primaryMediaId"] = (
                workflows[0].get("metadata") or {}).get("primaryMediaId")
        present = {lbl: v for lbl, v in candidates.items() if v}
        log.info("poll_candidate_uuids", candidates=present)
        _record(out_dir, "- Q7 candidate UUIDs by source:")
        for lbl, v in present.items():
            _record(out_dir, f"    - {lbl} = {v}")
        # Group source labels by UUID — the candidates often collapse to one UUID.
        by_uuid: dict[str, list[str]] = {}
        for lbl, v in present.items():
            by_uuid.setdefault(v, []).append(lbl)
        # Probe each DISTINCT UUID exactly once.
        results: dict[str, dict] = {}
        for uuid, labels in by_uuid.items():
            res = await _check_status(page, uuid, project_id)
            res_media = res["body"].get("media") or []
            gen_status = None
            if res_media:
                gen_status = ((res_media[0].get("mediaMetadata") or {})
                              .get("mediaStatus") or {}).get("mediaGenerationStatus")
            empty_200 = res["http_status"] == 200 and not gen_status
            results[uuid] = {"source_labels": labels, "http_status": res["http_status"],
                             "media_generation_status": gen_status, "empty_body_200": empty_200}
            log.info("poll_uuid_probed", uuid=uuid, source_labels=labels,
                     http_status=res["http_status"], media_generation_status=gen_status,
                     empty_body_200=empty_200)
            _record(out_dir, f"- Q7 uuid {uuid} (sources: {', '.join(labels)}): "
                             f"http={res['http_status']} status={gen_status}"
                             f"{'  [empty-body 200]' if empty_200 else ''}")
        (out_dir / "t2v_status_probe.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8")
        # Deterministic conclusion — handle confirmed only if exactly one distinct
        # UUID polls successfully (collapsed candidates count as that one UUID).
        polled = [u for u, r in results.items() if r["media_generation_status"]]
        if len(polled) == 1:
            srcs = ", ".join(results[polled[0]]["source_labels"])
            _record(out_dir, f"- Q7 RESOLVED: poll handle = {srcs} "
                             f"(the only candidate UUID that returns a status)")
        else:
            _record(out_dir, f"- Q7 INCONCLUSIVE: {len(polled)} distinct UUID(s) returned a "
                             f"status — inspect t2v_status_probe.json and decide manually")
        log.info("poll_handle_conclusion", distinct_uuids=len(by_uuid), distinct_polling=len(polled))
```

- [ ] **Step 3: Run and verify**

Run: `uv run python scripts/smoke_video_editor.py --profile-dir $HOME/gflow-video-spike --out tmp/video-spike/<the-Task-4-dir>`
(Re-passing the Task 4 `--out` dir reuses the captured generation — no new credit spend.)
Expected: a `poll_candidate_uuids` line listing each candidate UUID by source, one `poll_uuid_probed` line per *distinct* UUID, and a `poll_handle_conclusion`. **§10.2 Q7 is resolved when exactly one distinct UUID returns a real `media_generation_status`** — its source-label set is the poll handle (if all candidates collapse to one UUID, that one is the handle and the distinction is moot). If two *distinct* UUIDs both poll OK, the run records `Q7 INCONCLUSIVE` — inspect `t2v_status_probe.json` and decide manually.

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke_video_editor.py
git commit -m "chore(spike): verify the T2V poll handle across all three candidates"
```

---

## Task 6: Probe image attachment + record findings (answers Q1, Q3, Q6)

**Files:**
- Modify: `scripts/smoke_video_editor.py`
- Modify: `docs/superpowers/specs/2026-05-18-ui-automation-video-generation-design.md`

Uses the committed fixture `test_assets/image_00.png`.

- [ ] **Step 1: Add the image-attachment probe**

`page.expect_file_chooser()` directly answers Q1 (a `filechooser` event = a real file picker; no event = an in-page catalog dialog). Append to `scripts/smoke_video_editor.py`:

```python
START_FRAME_SELECTORS = (
    "button:has-text('Start')", "button:has-text('Initial')",
    "button:has-text('Inicial')", "button:has(i:text('add'))",
)
ADD_ELEMENT_SELECTORS = (
    "button[aria-label*='Add' i]", "button:has(i:text('add'))",
)
TEST_IMAGE = Path("test_assets/image_00.png")


async def _probe_image_attachment(page: Page, out_dir: Path) -> str | None:
    """Frames mode: open the start-frame attach control with expect_file_chooser
    (answers Q1) and upload TEST_IMAGE. Elementos mode: count reference slots (Q6).
    Returns the uploaded start-frame asset path used, or None if attach failed."""
    frames, _ = await _probe(page, "frames_subtab", FRAMES_SUBTAB_SELECTORS)
    if frames is None:
        _record(out_dir, "- Q1/Q3: Frames sub-tab not found — image probes skipped")
        return None
    await frames.click()
    await page.wait_for_timeout(1200)
    trigger, _ = await _probe(page, "start_frame_trigger", START_FRAME_SELECTORS)
    if trigger is None:
        await page.screenshot(path=str(out_dir / "frames_mode.png"), full_page=True)
        _record(out_dir, "- Q1: start-frame trigger NOT FOUND — see frames_mode.png")
        return None
    chooser_fired = True
    try:
        async with page.expect_file_chooser(timeout=5000) as fc_info:
            await trigger.click()
        fc = await fc_info.value
        await fc.set_files(str(TEST_IMAGE))
        log.info("frames_file_chooser", fired=True, uploaded=str(TEST_IMAGE))
    except Exception:  # noqa: BLE001
        chooser_fired = False
        await page.screenshot(path=str(out_dir / "frames_catalog.png"), full_page=True)
        log.info("frames_file_chooser", fired=False)
    _record(out_dir, f"- Q1 attachment mechanism: "
                     f"{'native file_chooser' if chooser_fired else 'in-page catalog dialog (see frames_catalog.png)'}")
    await page.wait_for_timeout(1500)

    elementos, _ = await _probe(page, "elementos_subtab", ELEMENTOS_SUBTAB_SELECTORS)
    if elementos is not None:
        await elementos.click()
        await page.wait_for_timeout(1200)
        slots = await page.locator(", ".join(ADD_ELEMENT_SELECTORS)).count()
        await page.screenshot(path=str(out_dir / "elementos_mode.png"), full_page=True)
        log.info("elementos_reference_slots", add_controls=slots)
        _record(out_dir, f"- Q6 reference slots: {slots} add-control(s) "
                         f"visible (cross-check elementos_mode.png)")
    return str(TEST_IMAGE) if chooser_fired else None
```

- [ ] **Step 2: Run a mandatory start-only I2V generation (answers Q3)**

Q3 (is start-only I2V valid?) gates `__post_init__` validation, so it must be answered definitively — not left optional. Append to `_drive_spike`:

```python
    uploaded = await _probe_image_attachment(page, out_dir)
    i2v_path = out_dir / "i2v_startonly_response.json"
    i2v_resp: dict | None = _load_reuse(i2v_path)
    if i2v_resp is not None:
        # Re-run with the same --out: reuse the paid I2V, don't re-spend.
        log.info("i2v_startonly_reused", path=str(i2v_path))
    elif media_name is None:
        # A rejected T2V predicts a rejected I2V — don't burn the credit unprompted.
        log.warning("i2v_startonly_skipped", reason="T2V was rejected")
        _record(out_dir, "- Q3 start-only I2V: SKIPPED (T2V was rejected — re-run "
                         "deliberately against a fresh --out to force the I2V test)")
    elif uploaded is None:
        log.warning("i2v_startonly_skipped", reason="start-frame attach failed")
        _record(out_dir, "- Q3 start-only I2V: SKIPPED (could not attach a start frame)")
    else:
        # Q3: start frame attached, NO end frame — does the generate succeed?
        # SPENDS CREDITS.
        frames2, _ = await _probe(page, "frames_subtab", FRAMES_SUBTAB_SELECTORS)
        if frames2 is not None:
            await frames2.click()
            await page.wait_for_timeout(1000)
        captured2, handler2 = _attach_video_listener(page)
        await _send_prompt(page, prompt_text, out_dir)
        try:
            i2v_resp = await _await_capture(page, captured2, handler2, timeout_s=150)
            _save_capture(i2v_path, i2v_resp)
        except TimeoutError:
            _record(out_dir, "- Q3 start-only I2V: NO RESPONSE captured (timeout) — "
                             "submit may be disabled without an end frame")
            log.warning("i2v_startonly_no_response")

    if i2v_resp is not None:
        i2v_media = i2v_resp.get("body", {}).get("media") or []
        accepted = i2v_resp.get("status") == 200 and bool(i2v_media)
        log.info("i2v_startonly_result", accepted=accepted, http_status=i2v_resp.get("status"))
        _record(out_dir, f"- Q3 start-only I2V: {'ACCEPTED' if accepted else 'REJECTED'} "
                         f"(http={i2v_resp.get('status')})")
```

- [ ] **Step 3: Run (SPENDS CREDITS — one I2V) and verify**

Run: `uv run python scripts/smoke_video_editor.py --profile-dir $HOME/gflow-video-spike --out tmp/video-spike/<the-Task-4-dir>`
Expected: `frames_file_chooser` (`fired` true/false), `elementos_reference_slots`, and `i2v_startonly_result` (or `i2v_startonly_no_response`). All findings appended to `phase0_findings.md`.

- [ ] **Step 4: Write the findings into the spec**

Read `tmp/video-spike/<dir>/phase0_findings.md`. In `docs/superpowers/specs/2026-05-18-ui-automation-video-generation-design.md` §10.2, append a `**Resolved (Phase 0):** <answer>` line under each of Q1, Q3, Q5, Q6, Q7 stating the observed result. Update §6 with the selector(s) the spike confirmed (replace the "unverified guesses" note with the verified selectors). **Describe observations in prose — do NOT paste the raw `*.png` screenshots into the committed spec; they show the authenticated account (email, project thumbnails).**

- [ ] **Step 5: Commit**

```bash
git add scripts/smoke_video_editor.py docs/superpowers/specs/2026-05-18-ui-automation-video-generation-design.md
git commit -m "chore(spike): probe image attachment + start-only I2V; record Phase 0 findings"
```

---

## Done criteria

Phase 0 is complete when:
- `scripts/smoke_video_editor.py` runs end-to-end against live Flow: enters the editor, switches to video mode, fires a T2V generation, verifies the poll handle, and probes image attachment + a start-only I2V generation.
- §10.2 Q1, Q3, Q5, Q7 each have a `**Resolved (Phase 0):**` answer in the spec. Q6 (`MAX_REFERENCE_IMAGES`) gets an answer too, but it may be an *estimate* tagged "confirm in Phase B" — Task 6 counts add-controls, a proxy for the slot cap, not the cap itself.
- §6 selectors are updated to the spike-verified values.
- The core finding is confirmed: video generation **can** be driven through the UI like `generate_images` (or, if not, the deviation is documented and the spec/Phase A plan is revised before Phase A starts).

**Known Phase-0 non-goals** (deferred to Phase A, not blockers): observing a *terminal* T2V status (`SUCCESSFUL`/`FAILED`) — Task 5 polls once for handle identification only; and confirming the FAILED-T2V wire shape — capture `11` (an I2V FAILED) remains the only `failureReasons` sample.

If the spike disproves the UI-drive assumption, **stop** — re-open the spec design before planning Phase A.

---

## Self-Review

- **Spec coverage:** Phase 0 per spec §10.3 = "drive the editor, fire one T2V `batchAsyncGenerateVideoText`, capture the response; validate §6 selectors; answer Q1/Q3/Q5/Q6/Q7." Mapped: T2V fire+capture → Task 4; §6 selector validation → Tasks 2, 3, 6; Q5 → Task 3; Q7 → Task 5; Q1/Q6 → Task 6 Step 1; Q3 → Task 6 Step 2. Covered.
- **Placeholder scan:** none — every code step is complete; the harness reuse names exact symbols and `run`/`main` are given in full (no "copy then trim").
- **Type/name consistency:** `_drive_spike`, `_record`, `_probe`, `_attach_video_listener`, `_await_capture`, `_save_capture`, `_load_reuse`, `_check_status`, `_probe_aspect_options`, `_probe_image_attachment` defined before use; `media_name`, `body`, `project_id`, `out_dir` threaded consistently; `_send_prompt` called as `(page, prompt_text, out_dir)`; `run`/`main` signatures match (`profile_dir, prompt_text, out_dir` — no `expected_count`/`aspect_ratio`).
