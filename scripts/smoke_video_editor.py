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

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import structlog
from playwright.async_api import BrowserContext, Locator, Page, Response, async_playwright

log = structlog.get_logger(__name__)


FLOW_URL = "https://labs.google/fx/tools/flow?hl=en"

PROMPT_INPUT_SELECTORS = [
    'div[role="textbox"][data-slate-editor="true"]',
    'div[contenteditable="true"]',
    "textarea",
    '[aria-label*="prompt"]',
]

SUBMIT_BUTTON_SELECTORS = [
    # Universal: icon-based selector (locale-invariant, same pattern as NEW_PROJECT_SELECTORS)
    "button:has(i.google-symbols:text('arrow_forward'))",
    "button:has(i:text('arrow_forward'))",
    # Fallback: button whose visible text includes the icon ligature text
    "button:has-text('arrow_forward')",
]


# (2026-05-12): the button wraps a Material Symbols icon (text "add_2",
# rendered as "+") followed by localized label text "New project" /
# "Novo projeto" / etc. The most robust match is the icon class, which is
# stable across locales: `i.google-symbols` is the Material Symbols span.
NEW_PROJECT_SELECTORS = [
    # Universal: any button containing the google-symbols "add_2" icon.
    "button:has(i.google-symbols:text('add_2'))",
    "button:has(i:text('add_2'))",
    # Localized text fallbacks
    "button:has-text('New project')",
    "button:has-text('Novo projeto')",
    "button:has-text('Nuevo proyecto')",
    "button:has-text('Nouveau projet')",
    "[role='button']:has-text('New project')",
    "a:has-text('New project')",
    r"button:text-matches('\+\s+\S+', 'i')",
    "[aria-label*='New project' i]",
    "[aria-label*='Project' i]",
]


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

# The settings trigger shows the current ratio icon; enumerate the icon names.
ASPECT_SETTINGS_TRIGGER_SELECTORS = (
    "button:has(i.google-symbols:text('crop_16_9'))",
    "button:has(i.google-symbols:text('crop_9_16'))",
    "button:has(i.google-symbols:text('crop_square'))",
    "button:has(i.google-symbols:text('aspect_ratio'))",
)
ASPECT_OPTIONS = {"portrait": "9:16", "landscape": "16:9", "square": "1:1"}

VIDEO_GENERATE_ROUTES = (
    "batchAsyncGenerateVideoText",
    "batchAsyncGenerateVideoStartAndEndImage",
    "batchAsyncGenerateVideoReferenceImages",
)

STATUS_URL = "https://aisandbox-pa.googleapis.com/v1/video:batchCheckAsyncVideoGenerationStatus"

START_FRAME_SELECTORS = (
    "button:has-text('Start')",
    "button:has-text('Initial')",
    "button:has-text('Inicial')",
    "button:has(i:text('add'))",
)
ADD_ELEMENT_SELECTORS = (
    "button[aria-label*='Add' i]",
    "button:has(i:text('add'))",
)
TEST_IMAGE = Path("test_assets/image_00.png")


async def _check_logged_in(page: Page) -> bool:
    """Authenticated if we're on a Flow URL and not on a sign-in page.

    The strict-positive variant (require visible +New project CTA) failed
    because the button uses Material Symbols ligature icons whose
    DOM text is "add_2"; not all locale renderings match our text
    selectors. URL gating + sign-in-page negation is sufficient here:
    if accounts.google.com isn't in the URL and we're on
    labs.google/<locale>/tools/flow, the profile's cookies have already
    authenticated us — the gallery is shown.
    """
    if "accounts.google.com" in page.url:
        return False
    on_flow = "labs.google" in page.url and "/flow" in page.url
    if not on_flow:
        return False
    if "/project/" in page.url:
        return True
    # Reject only if there's a top-level sign-in CTA (which exists on the
    # public landing page but not on the authenticated gallery).
    try:
        signin_button = await page.locator(
            "button:has-text('Sign in'), a:has-text('Sign in')"
        ).count()
    except Exception:  # noqa: BLE001
        signin_button = 0
    return signin_button == 0


async def _ensure_logged_in_to_flow(
    page: Page,
    out_dir: Path,
    poll_interval_s: float = 5.0,
    timeout_s: float = 600.0,
) -> None:
    """Wait for the operator to sign in inside Chromium (poll, no stdin needed)."""
    try:
        await page.goto(FLOW_URL, wait_until="networkidle", timeout=45_000)
    except Exception as e:  # noqa: BLE001
        log.warning("flow_initial_goto_failed", error=str(e))

    if await _check_logged_in(page):
        log.info("logged_in", url=page.url)
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    shot = out_dir / "auth_pending.png"
    try:
        await page.screenshot(path=str(shot), full_page=True)
    except Exception:  # noqa: BLE001
        pass
    print(
        "\n>> Not signed in to Flow yet.\n"
        f">> Current URL : {page.url}\n"
        f">> Screenshot  : {shot}\n"
        ">> Sign in inside the open Chromium window (any Flow account).\n"
        ">> Script will auto-detect when you're authenticated and continue.\n"
        f">> Polling every {poll_interval_s:.0f}s, max wait {timeout_s:.0f}s.",
        flush=True,
    )

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_interval_s)
        if await _check_logged_in(page):
            log.info("logged_in_detected", url=page.url)
            print(">> Detected sign-in — continuing.\n", flush=True)
            return
        if "accounts.google.com" not in page.url and "labs.google" not in page.url:
            try:
                await page.goto(FLOW_URL, wait_until="networkidle", timeout=15_000)
            except Exception:  # noqa: BLE001
                pass
        log.info("polling_for_signin", url=page.url[:80])
    raise TimeoutError(f"Sign-in not detected within {timeout_s:.0f}s. Last URL: {page.url}")


async def _enter_editor(page: Page, out_dir: Path) -> None:
    """If on the gallery, click 'New project' and wait for /project/UUID nav."""
    if "/project/" in page.url:
        log.info("editor_already_open", url=page.url)
        return
    await page.wait_for_timeout(3000)
    for selector in NEW_PROJECT_SELECTORS:
        try:
            loc = page.locator(selector).first
            await loc.wait_for(state="visible", timeout=5000)
            log.info("clicking_new_project", selector=selector)
            await loc.click()
            try:
                await page.wait_for_url(lambda url: "/project/" in url, timeout=15_000)
                log.info("entered_editor", url=page.url)
                return
            except Exception:
                log.warning("new_project_click_did_not_navigate", selector=selector)
        except Exception:
            continue
    out_dir.mkdir(parents=True, exist_ok=True)
    shot = out_dir / "debug_new_project.png"
    try:
        await page.screenshot(path=str(shot), full_page=True)
    except Exception:  # noqa: BLE001
        pass
    raise RuntimeError(
        f"Could not find 'New project' on Flow gallery. URL: {page.url}. Screenshot: {shot}"
    )


async def _send_prompt(page: Page, prompt_text: str, out_dir: Path) -> None:
    """Type the prompt into the Slate editor and click submit (or press Enter)."""
    input_box = None
    for selector in PROMPT_INPUT_SELECTORS:
        try:
            loc = page.locator(selector).first
            await loc.wait_for(state="visible", timeout=10_000)
            input_box = loc
            log.info("prompt_input_found", selector=selector)
            break
        except Exception:
            continue
    if input_box is None:
        out_dir.mkdir(parents=True, exist_ok=True)
        shot = out_dir / "debug_prompt_not_found.png"
        try:
            await page.screenshot(path=str(shot), full_page=True)
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(
            f"Prompt input not found in Flow UI. URL: {page.url}. Screenshot: {shot}"
        )

    await input_box.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Delete")
    # insertText fires a single 'beforeinput' event that Slate.js handles
    # natively — much faster than per-keystroke type() (~1.5s/char in headed Chrome).
    await page.keyboard.insert_text(prompt_text)
    await page.wait_for_timeout(600)

    for sel in SUBMIT_BUTTON_SELECTORS:
        try:
            btn = page.locator(sel).first
            await btn.wait_for(state="visible", timeout=3_000)
            await btn.click()
            log.info("prompt_submitted", via=sel)
            return
        except Exception:
            continue
    log.info("prompt_submitted", via="enter_key_fallback")
    await page.keyboard.press("Enter")


def _record(out_dir: Path, line: str) -> None:
    """Append a line to the durable findings file so observations survive a crash."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "phase0_findings.md").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


async def _probe(
    page: Page, label: str, candidates: tuple[str, ...], timeout_ms: int = 4000
) -> tuple[Locator | None, str | None]:
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


def _attach_video_listener(page: Page):
    """Register a page response listener for the batchAsyncGenerateVideo* routes
    BEFORE the prompt is submitted. Returns (captured_list, handler)."""
    captured: list[dict] = []

    async def on_response(response: Response) -> None:
        if not any(r in response.url for r in VIDEO_GENERATE_ROUTES):
            return
        try:
            captured.append(
                {"status": response.status, "url": response.url, "body": await response.json()}
            )
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
    mechanism = (
        "native file_chooser"
        if chooser_fired
        else "in-page catalog dialog (see frames_catalog.png)"
    )
    _record(out_dir, f"- Q1 attachment mechanism: {mechanism}")
    await page.wait_for_timeout(1500)

    elementos, _ = await _probe(page, "elementos_subtab", ELEMENTOS_SUBTAB_SELECTORS)
    if elementos is not None:
        await elementos.click()
        await page.wait_for_timeout(1200)
        slots = await page.locator(", ".join(ADD_ELEMENT_SELECTORS)).count()
        await page.screenshot(path=str(out_dir / "elementos_mode.png"), full_page=True)
        log.info("elementos_reference_slots", add_controls=slots)
        _record(
            out_dir,
            f"- Q6 reference slots: {slots} add-control(s) "
            f"visible (cross-check elementos_mode.png)",
        )
    return str(TEST_IMAGE) if chooser_fired else None


async def _drive_spike(context: BrowserContext, prompt_text: str, out_dir: Path) -> None:
    page = context.pages[0] if context.pages else await context.new_page()
    await _ensure_logged_in_to_flow(page, out_dir)
    await _enter_editor(page, out_dir)
    project_id = page.url.split("/project/")[1].split("?")[0]
    _record(out_dir, f"# Phase 0 spike findings\n\nproject_id: {project_id}\n")
    log.info("spike_editor_ready", project_id=project_id, url=page.url)

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

    await _probe_aspect_options(page, out_dir)

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
        log.warning(
            "t2v_generate_rejected",
            http_status=http_status,
            error=body.get("error"),
            failure_reasons=failure_reasons,
        )
        _record(
            out_dir,
            f"- T2V generate REJECTED: http={http_status} "
            f"reasons={failure_reasons} error={body.get('error')}",
        )
    else:
        log.info(
            "t2v_generated",
            http_status=http_status,
            route=route,
            media_name=media_name,
            remaining_credits=body.get("remainingCredits"),
        )
        _record(
            out_dir,
            f"- T2V generate OK: route={route} media_name={media_name} "
            f"credits_left={body.get('remainingCredits')}",
        )

    if media_name is None:
        log.warning("poll_handle_check_skipped", reason="generate returned no media")
        _record(out_dir, "- Q7 poll handle: SKIPPED (T2V generate was rejected)")
    else:
        operations = body.get("operations") or []
        workflows = body.get("workflows") or []
        # Every candidate id spec §2.4 names, by source label.
        candidates: dict[str, str | None] = {"media[0].name": media_name}
        if operations:
            candidates["operations[0].operation.name"] = (operations[0].get("operation") or {}).get(
                "name"
            )
        if workflows:
            candidates["workflows[0].metadata.primaryMediaId"] = (
                workflows[0].get("metadata") or {}
            ).get("primaryMediaId")
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
                gen_status = (
                    (res_media[0].get("mediaMetadata") or {}).get("mediaStatus") or {}
                ).get("mediaGenerationStatus")
            empty_200 = res["http_status"] == 200 and not gen_status
            results[uuid] = {
                "source_labels": labels,
                "http_status": res["http_status"],
                "media_generation_status": gen_status,
                "empty_body_200": empty_200,
            }
            log.info(
                "poll_uuid_probed",
                uuid=uuid,
                source_labels=labels,
                http_status=res["http_status"],
                media_generation_status=gen_status,
                empty_body_200=empty_200,
            )
            _record(
                out_dir,
                f"- Q7 uuid {uuid} (sources: {', '.join(labels)}): "
                f"http={res['http_status']} status={gen_status}"
                f"{'  [empty-body 200]' if empty_200 else ''}",
            )
        (out_dir / "t2v_status_probe.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8"
        )
        # Deterministic conclusion — handle confirmed only if exactly one distinct
        # UUID polls successfully (collapsed candidates count as that one UUID).
        polled = [u for u, r in results.items() if r["media_generation_status"]]
        if len(polled) == 1:
            srcs = ", ".join(results[polled[0]]["source_labels"])
            _record(
                out_dir,
                f"- Q7 RESOLVED: poll handle = {srcs} "
                f"(the only candidate UUID that returns a status)",
            )
        else:
            _record(
                out_dir,
                f"- Q7 INCONCLUSIVE: {len(polled)} distinct UUID(s) returned a "
                f"status — inspect t2v_status_probe.json and decide manually",
            )
        log.info(
            "poll_handle_conclusion", distinct_uuids=len(by_uuid), distinct_polling=len(polled)
        )

    uploaded = await _probe_image_attachment(page, out_dir)
    i2v_path = out_dir / "i2v_startonly_response.json"
    i2v_resp: dict | None = _load_reuse(i2v_path)
    if i2v_resp is not None:
        # Re-run with the same --out: reuse the paid I2V, don't re-spend.
        log.info("i2v_startonly_reused", path=str(i2v_path))
    elif media_name is None:
        # A rejected T2V predicts a rejected I2V — don't burn the credit unprompted.
        log.warning("i2v_startonly_skipped", reason="T2V was rejected")
        _record(
            out_dir,
            "- Q3 start-only I2V: SKIPPED (T2V was rejected — re-run "
            "deliberately against a fresh --out to force the I2V test)",
        )
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
            _record(
                out_dir,
                "- Q3 start-only I2V: NO RESPONSE captured (timeout) — "
                "submit may be disabled without an end frame",
            )
            log.warning("i2v_startonly_no_response")

    if i2v_resp is not None:
        i2v_media = i2v_resp.get("body", {}).get("media") or []
        accepted = i2v_resp.get("status") == 200 and bool(i2v_media)
        log.info("i2v_startonly_result", accepted=accepted, http_status=i2v_resp.get("status"))
        _record(
            out_dir,
            f"- Q3 start-only I2V: {'ACCEPTED' if accepted else 'REJECTED'} "
            f"(http={i2v_resp.get('status')})",
        )


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
        "--profile-dir",
        type=Path,
        default=Path.home() / "gflow-video-spike",
        help="Playwright Chromium user-data-dir (default: $HOME/gflow-video-spike)",
    )
    parser.add_argument(
        "--prompt",
        default="a calm forest at dawn, cinematic",
        help="T2V prompt to generate",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output dir (default: tmp/video-spike/<utc>). Re-pass a prior dir "
        "to reuse its captured T2V generation and skip the paid generate step.",
    )
    args = parser.parse_args()
    out_dir = args.out or (
        Path("tmp") / "video-spike" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    )
    # HARDENING — real-Chrome auth is mandatory. The spike drives Google's Flow
    # UI; automated / bundled-Chromium browsers are rejected by Google sign-in
    # (the "G12 block"). channel_for_profile() returns "chrome" only for a
    # profile authenticated via `gflow auth login --browser chrome`.
    from gflow_cli.browser_manager import channel_for_profile

    if channel_for_profile(args.profile_dir) != "chrome":
        raise SystemExit(
            f"ERROR: profile is not Chrome-strategy authenticated: {args.profile_dir}\n"
            "The spike drives Google's Flow UI and is rejected by Google sign-in\n"
            "unless it runs in real Chrome with a complete session. Authenticate first:\n"
            "  uv run gflow auth login --profile <name> --browser chrome\n"
            "then pass that profile's dir to --profile-dir."
        )
    args.profile_dir.mkdir(parents=True, exist_ok=True)
    asyncio.run(run(args.profile_dir, args.prompt, out_dir))


if __name__ == "__main__":
    main()
