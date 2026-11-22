"""Spike: can a browser-minted reCAPTCHA token be used in a direct httpx batchexecute
call for ``ogiZ0b`` (image generation)?

Question
--------
If gflow mints a reCAPTCHA Enterprise token inside a real browser page and then sends
the ``ogiZ0b`` frame via httpx (outside the browser), does Flow accept it, or does Flow
bind the token to the page's HTTP client and reject the out-of-band request?

Why this matters
----------------
If it works, ``gflow image t2i`` could keep a lightweight browser open only to mint
tokens and discover the site key, while all generation traffic goes as direct HTTP — no
UI automation, no selectors, no click timeouts, no WAF-scored page actions.

Pre-registered reading (written BEFORE the run)
------------------------------------------------
+----------+----------------------------------------------------------------+
| Outcome  | Reading                                                        |
+==========+================================================================+
| Arm A: httpx POST returns 200 with image URLs in the ``ogiZ0b``    |
|          | frame → the token is NOT bound to the page's HTTP client;      |
|          | direct image generation is viable.                             |
+----------+----------------------------------------------------------------+
| Arm A error frame with reCAPTCHA reasons → escalate to Arm B.      |
+----------+----------------------------------------------------------------+
| Arm B: ``page.evaluate(fetch(...))`` with the same captured frame   |
|          | and a fresh token → accepted means the refusal is httpx-bound  |
|          | (UA/fingerprint/TLS), not token-bound; a no-selector route     |
|          | through the page still exists.                                 |
+----------+----------------------------------------------------------------+
| Arm B also refused → escalate to Arm C.                             |
+----------+----------------------------------------------------------------+
| Arm C: let the page's OWN UI submit through (normal click path)     |
|          | → if that also refuses, the profile is being refused (#906)    |
|          | and this run is void, not a finding.                           |
+----------+----------------------------------------------------------------+
| HTTP 403 or ``PUBLIC_ERROR_UNUSUAL_ACTIVITY`` → WAF rejects the     |
|          | direct request regardless of a valid token.                    |
+----------+----------------------------------------------------------------+
| 200 but empty / null payload → frame structure wrong; fix before    |
|          | concluding.                                                    |
+----------+----------------------------------------------------------------+

Method
------
Phase 1 — CAPTURE ($0):
  Open the browser on the profile, navigate to a project, type a prompt, click Generate,
  and route-abort the ``ogiZ0b`` POST before Flow acts. This captures the REAL positional
  array the page sends (not ``_build_batch_generate_images_body`` which is REST-shaped).
  Also hook ``grecaptcha.enterprise.execute`` to record the action string.

Phase 2 — ARM A (direct httpx POST):
  Mint a fresh token (discovered action), splice it into the captured frame, and POST
  via httpx using the same URL/headers/cookies as the captured request.

Phase 3 — ARM B (in-page fetch, only if A fails):
  Same frame + fresh token, sent via ``page.evaluate(fetch(...))`` — same TLS session,
  same cookie jar, different call site.

Phase 4 — ARM C (UI submit, only if B fails):
  Let the page click Generate again and NOT abort — if that also fails, the profile is
  bad.

Cost: Arm A and B spend one image quota each IF accepted. Arm C is a normal generation.
The abort in Phase 1 is $0.

Usage:
    $env:PYTHONUTF8 = "1"
    uv run python scripts/dev/spike_direct_image_with_browser_token.py --profile denon82

Requires: a saved browser profile with an image-capable project.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import secrets
import time
from http.cookiejar import CookieJar, DefaultCookiePolicy
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode

import httpx

# ---------------------------------------------------------------------------
# Bootstrap — make src/ importable from scripts/dev/
# ---------------------------------------------------------------------------
from _spike_common import build_client, default_out_path, resolve_profile_dir, step

from gflow_cli.api.transports.batchexecute import image_records, parse_frames, rpc_errors

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_BATCHEXECUTE_URL_PATTERN = re.compile(r".*/batchexecute.*")
_ORIGIN = "https://flow.google.com"
_PROMPT = f"a serene mountain lake at dawn, spike {secrets.token_hex(4)}"
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
OUT_PATH = default_out_path("spike_direct_image_token", ".jsonl")


def _log(tag: str, data: dict[str, Any]) -> None:
    line = {"tag": tag, "ts": time.strftime("%H:%M:%S"), **data}
    print(json.dumps(line, default=str), flush=True)
    with OUT_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, default=str) + "\n")


def _redact(s: str, *, keep: int = 8) -> str:
    """Redact a string to its first *keep* chars."""
    return s[:keep] + "..." if len(s) > keep else s


# ---------------------------------------------------------------------------
# Phase 1: capture the real ogiZ0b request via route-abort
# ---------------------------------------------------------------------------

_captured: dict[str, Any] = {}
_recaptcha_action: str | None = None


async def _capture_route(route: Any) -> None:
    """Route handler: capture the first ogiZ0b POST then abort ($0)."""
    request = route.request
    if request.method != "POST":
        await route.continue_()
        return

    body = request.post_data or ""
    if "ogiZ0b" not in body:
        await route.continue_()
        return

    # Capture everything
    _captured["url"] = request.url
    _captured["headers"] = dict(request.headers)
    _captured["body"] = body  # URL-encoded form body
    _captured["method"] = request.method

    step("CAPTURE", f"intercepted ogiZ0b POST to {_redact(request.url, keep=60)}")
    await route.abort()


async def _run_capture(client: Any, project_id: str) -> bool:
    """Drive the UI to submit an image, capturing the ogiZ0b request.

    Returns True if the capture succeeded.
    """
    page = client.page

    # Hook grecaptcha to record the action before the page mints
    await page.evaluate("""() => {
        if (typeof window.__spike_recaptcha_actions === 'undefined') {
            window.__spike_recaptcha_actions = [];
        }
        if (typeof grecaptcha !== 'undefined' && grecaptcha.enterprise) {
            const orig = grecaptcha.enterprise.execute;
            grecaptcha.enterprise.execute = function(siteKey, opts) {
                window.__spike_recaptcha_actions.push({
                    siteKey,
                    action: opts && opts.action,
                    ts: Date.now()
                });
                return orig.apply(this, arguments);
            };
        }
    }""")

    # Route-intercept batchexecute
    await page.route(_BATCHEXECUTE_URL_PATTERN, _capture_route)

    try:
        # Type a prompt in the composer (same as migrated_composer.type_prompt)
        composer = page.locator('[contenteditable="true"]').first
        await composer.click(timeout=5000)
        # insert_text dispatches input events without key presses so a newline
        # in the prompt lands as text instead of an Enter that might submit.
        await page.keyboard.insert_text(_PROMPT)
        step("CAPTURE", f"typed prompt: {_redact(_PROMPT, keep=30)}")

        # The submit button: a button containing a mat-icon with ligature "arrow_forward"
        # (same selector as _mc_submit_observe._pre_submit_gate)
        def _exact(name: str) -> re.Pattern[str]:
            return re.compile(rf"^{re.escape(name)}$")

        generate_btn = page.locator("button").filter(
            has=page.locator("mat-icon").filter(has_text=_exact("arrow_forward"))
        ).first

        # Wait for the button to appear and be enabled
        await generate_btn.wait_for(state="visible", timeout=10_000)
        step("CAPTURE", "submit button (arrow_forward) visible")

        # Wait for it to be enabled (may take a moment after typing)
        for _ in range(20):
            if await generate_btn.is_enabled():
                break
            await asyncio.sleep(0.3)

        await generate_btn.click(timeout=5000)
        step("CAPTURE", "clicked generate button (arrow_forward)")

        # Wait for the route handler to fire
        for _ in range(60):
            if _captured:
                break
            await asyncio.sleep(0.5)

        if not _captured:
            step("CAPTURE", "TIMEOUT: no ogiZ0b POST intercepted in 30s")
            return False

        # Read the hooked recaptcha action
        global _recaptcha_action  # noqa: PLW0603
        actions = await page.evaluate("() => window.__spike_recaptcha_actions || []")
        if actions:
            _recaptcha_action = actions[-1].get("action")
            step("CAPTURE", f"reCAPTCHA action discovered: {_recaptcha_action!r}")
        else:
            step("CAPTURE", "WARNING: no reCAPTCHA action intercepted (hook may have missed it)")

        return True
    finally:
        await page.unroute(_BATCHEXECUTE_URL_PATTERN, _capture_route)


# ---------------------------------------------------------------------------
# Phase 2: Arm A — direct httpx POST
# ---------------------------------------------------------------------------


def _replace_token(body: str, new_token: str) -> str:
    """Replace the reCAPTCHA token in the captured URL-encoded body.

    The token is the longest string in the decoded f.req payload that looks like
    a reCAPTCHA token (long base64-ish string).
    """
    parsed = parse_qs(body, keep_blank_values=True)
    freq = parsed.get("f.req", [""])[0]

    # Decode the f.req JSON to find the token
    try:
        outer = json.loads(freq)
    except (json.JSONDecodeError, TypeError):
        step("REPLACE", "could not decode f.req")
        return body

    # The inner payload is a JSON string at outer[0][0][1]
    inner_json = outer[0][0][1]
    try:
        inner = json.loads(inner_json)
    except (json.JSONDecodeError, TypeError):
        step("REPLACE", "could not decode inner payload")
        return body

    # Find the token: a long string (>100 chars) that appears in the payload
    # reCAPTCHA tokens are typically 300-800 chars of base64
    old_token = None

    def _find_token(obj: Any) -> str | None:
        if isinstance(obj, str) and len(obj) > 100 and not obj.startswith("http"):
            # Looks like a token
            return obj
        if isinstance(obj, list):
            for item in obj:
                found = _find_token(item)
                if found:
                    return found
        if isinstance(obj, dict):
            for v in obj.values():
                found = _find_token(v)
                if found:
                    return found
        return None

    old_token = _find_token(inner)
    if not old_token:
        step("REPLACE", "no token-shaped string found in payload")
        return body

    step("REPLACE", f"found token: {_redact(old_token, keep=20)} ({len(old_token)} chars)")

    # Replace every occurrence in the inner JSON string
    new_inner_json = inner_json.replace(old_token, new_token)
    outer[0][0][1] = new_inner_json
    new_freq = json.dumps(outer, separators=(",", ":"))

    # Rebuild the URL-encoded body
    parsed["f.req"] = [new_freq]
    return urlencode(parsed, doseq=True)


async def _arm_a(client: Any, profile_dir: Path) -> dict[str, Any]:
    """Arm A: direct httpx POST with browser-minted token."""
    page = client.page

    # Mint a fresh token
    from gflow_cli.api.recaptcha import TokenMinter

    minter = TokenMinter(page)
    action = _recaptcha_action or "imageGeneration"
    step("ARM_A", f"minting token with action={action!r}")
    token = await minter.mint(action)
    step("ARM_A", f"minted token: {_redact(token, keep=20)} ({len(token)} chars)")

    # Build the request from the capture
    new_body = _replace_token(_captured["body"], token)
    captured_headers = _captured["headers"]

    # Use the same headers the browser sent, keeping the browser's UA and sec-ch-ua
    headers = {
        k: v
        for k, v in captured_headers.items()
        if k.lower()
        not in (
            "host",
            "content-length",
            "connection",
            "transfer-encoding",
            # cookie is set separately on httpx
        )
    }
    # Ensure required headers are present
    headers["content-type"] = "application/x-www-form-urlencoded;charset=UTF-8"
    headers["x-same-domain"] = "1"
    headers["origin"] = _ORIGIN

    # Extract cookies from captured headers
    cookie_header = captured_headers.get("cookie", "")

    # Override the cookie header
    headers["cookie"] = cookie_header

    url = _captured["url"]
    step("ARM_A", f"POSTing to {_redact(url, keep=60)}")

    t0 = time.perf_counter()
    async with httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(240, connect=10),
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
    ) as hx:
        response = await hx.post(url, content=new_body, headers=headers)
    elapsed = time.perf_counter() - t0

    result: dict[str, Any] = {
        "status": response.status_code,
        "elapsed_s": round(elapsed, 2),
        "body_len": len(response.text),
    }

    text = response.text
    frames = [(rid, p) for rid, p in parse_frames(text) if rid == "ogiZ0b"]
    errors = [
        {"rpcid": e.rpcid, "code": e.code, "reasons": list(e.reasons)}
        for e in rpc_errors(text)
        if e.rpcid == "ogiZ0b"
    ]

    result["frame_count"] = len(frames)
    result["errors"] = errors

    if frames:
        try:
            records = image_records("ogiZ0b", frames[0][1])
            result["image_count"] = len(records)
            result["first_image_url_prefix"] = _redact(records[0].image_url, keep=50)
            result["dimensions"] = list(records[0].dimensions)
            result["media_id"] = _redact(records[0].media_id)
            result["prompt_match"] = _PROMPT in records[0].prompt

            # Try downloading the image
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=httpx.Timeout(30),
            ) as hx:
                img_resp = await hx.get(records[0].image_url)
                result["image_download_status"] = img_resp.status_code
                result["image_bytes"] = len(img_resp.content)
                result["image_magic"] = img_resp.content[:4].hex() if img_resp.content else None
                result["is_jpeg"] = img_resp.content[:2] == b"\xff\xd8" if img_resp.content else False
        except Exception as exc:
            result["image_parse_error"] = str(exc)

    _log("arm_a_result", result)
    return result


# ---------------------------------------------------------------------------
# Phase 3: Arm B — in-page fetch (only if A fails)
# ---------------------------------------------------------------------------


async def _arm_b(client: Any) -> dict[str, Any]:
    """Arm B: send via page.evaluate(fetch(...)) — same TLS, same cookies."""
    page = client.page

    from gflow_cli.api.recaptcha import TokenMinter

    minter = TokenMinter(page)
    action = _recaptcha_action or "imageGeneration"
    token = await minter.mint(action)
    step("ARM_B", f"minted token: {_redact(token, keep=20)}")

    new_body = _replace_token(_captured["body"], token)
    url = _captured["url"]

    step("ARM_B", f"sending via page.evaluate(fetch) to {_redact(url, keep=60)}")

    t0 = time.perf_counter()
    # Send the request from within the page's JS context
    fetch_result = await page.evaluate(
        """async ([url, body]) => {
            try {
                const resp = await fetch(url, {
                    method: 'POST',
                    headers: {
                        'content-type': 'application/x-www-form-urlencoded;charset=UTF-8',
                        'x-same-domain': '1'
                    },
                    body: body,
                    credentials: 'include'
                });
                const text = await resp.text();
                return { status: resp.status, body: text, ok: true };
            } catch (e) {
                return { ok: false, error: e.message };
            }
        }""",
        [url, new_body],
        timeout=240_000,
    )
    elapsed = time.perf_counter() - t0

    result: dict[str, Any] = {
        "elapsed_s": round(elapsed, 2),
        "fetch_ok": fetch_result.get("ok", False),
    }

    if not fetch_result.get("ok"):
        result["error"] = fetch_result.get("error")
        _log("arm_b_result", result)
        return result

    result["status"] = fetch_result["status"]
    text = fetch_result.get("body", "")
    result["body_len"] = len(text)

    frames = [(rid, p) for rid, p in parse_frames(text) if rid == "ogiZ0b"]
    errors = [
        {"rpcid": e.rpcid, "code": e.code, "reasons": list(e.reasons)}
        for e in rpc_errors(text)
        if e.rpcid == "ogiZ0b"
    ]

    result["frame_count"] = len(frames)
    result["errors"] = errors

    if frames:
        try:
            records = image_records("ogiZ0b", frames[0][1])
            result["image_count"] = len(records)
            result["first_image_url_prefix"] = _redact(records[0].image_url, keep=50)
            result["prompt_match"] = _PROMPT in records[0].prompt
        except Exception as exc:
            result["image_parse_error"] = str(exc)

    _log("arm_b_result", result)
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", required=True, help="Saved browser profile name")
    ap.add_argument("--project", default=None, help="Project ID (uses first listed if omitted)")
    args = ap.parse_args()

    profile_dir = resolve_profile_dir(args.profile)
    step("INIT", f"profile: {args.profile}, output: {OUT_PATH}")
    _log("init", {"profile": args.profile, "out": str(OUT_PATH)})

    async with build_client(profile_dir, headless=False) as client:
        page = client.page

        # Navigate to Flow and wait for the editor
        step("NAV", "waiting for Flow editor to load...")
        await page.wait_for_load_state("networkidle", timeout=30_000)
        step("NAV", f"on: {_redact(page.url, keep=50)}")

        # Discover a project if not given
        project_id = args.project
        if not project_id:
            # Look for a project link in the page
            project_links = await page.locator('a[href*="/project/"]').all()
            for link in project_links:
                href = await link.get_attribute("href") or ""
                match = re.search(r"/project/([0-9a-f-]{36})", href)
                if match:
                    project_id = match.group(1)
                    break

        if project_id:
            step("NAV", f"using project: {_redact(project_id)}")
            if f"/project/{project_id}" not in page.url:
                await page.goto(f"{_ORIGIN}/project/{project_id}", wait_until="networkidle", timeout=30_000)
        else:
            step("NAV", "WARNING: no project ID found; using current page")

        # Dismiss agent mode if active (the agent-mode chip blocks the classic composer)
        try:
            agent_chip = page.locator('button.agent-mode-chip[aria-pressed="true"]')
            if await agent_chip.count() > 0:
                await agent_chip.click()
                step("NAV", "dismissed agent mode chip")
                await asyncio.sleep(1)
            # Close the agent panel if visible
            agent_panel = page.locator("flow-agent-panel")
            if await agent_panel.count() > 0 and await agent_panel.is_visible():
                close_btn = agent_panel.locator("button").filter(
                    has=page.locator("mat-icon").filter(has_text=re.compile(r"^close$"))
                ).first
                if await close_btn.is_visible(timeout=2000):
                    await close_btn.click()
                    step("NAV", "closed agent panel")
                    await asyncio.sleep(0.5)
        except Exception:
            step("NAV", "agent mode dismissal skipped")

        # Wait for composer to be ready
        await page.wait_for_selector('[contenteditable="true"]', timeout=30_000)
        step("NAV", "composer ready")

        # Ensure we're in image mode — the migrated host uses a settings popover
        # with [role=radiogroup] / [role=radio] buttons. The settings trigger is
        # a button with a mat-icon "tune" ligature.
        try:
            settings_btn = page.locator("button").filter(
                has=page.locator("mat-icon").filter(has_text=re.compile(r"^tune$"))
            ).first
            if await settings_btn.is_visible(timeout=3000):
                await settings_btn.click()
                await asyncio.sleep(0.5)
                # Image mode radio — mat-icon with "image" ligature inside a [role=radio]
                image_radio = page.locator('[role="radio"]').filter(
                    has=page.locator("mat-icon").filter(has_text=re.compile(r"^image$"))
                ).first
                if await image_radio.is_visible(timeout=2000):
                    checked = await image_radio.get_attribute("aria-checked")
                    if checked != "true":
                        await image_radio.click()
                        step("NAV", "switched to image mode")
                    else:
                        step("NAV", "already in image mode")
                # Close settings pane
                await page.keyboard.press("Escape")
        except Exception:
            step("NAV", "settings pane interaction skipped (may already be in image mode)")

        await asyncio.sleep(1)

        # ====================================================================
        # Phase 1: CAPTURE
        # ====================================================================
        step("PHASE_1", "capturing ogiZ0b request via route-abort ($0)...")
        captured = await _run_capture(client, project_id or "unknown")

        if not captured:
            _log("abort", {"reason": "capture failed — no ogiZ0b intercepted"})
            step("ABORT", "no ogiZ0b request captured. The page may not have submitted.")
            return

        _log("capture", {
            "url_prefix": _redact(_captured["url"], keep=60),
            "body_len": len(_captured["body"]),
            "headers_count": len(_captured["headers"]),
            "recaptcha_action": _recaptcha_action,
        })

        # ====================================================================
        # Phase 2: ARM A — direct httpx POST
        # ====================================================================
        step("PHASE_2", "Arm A: direct httpx POST with fresh browser-minted token...")
        arm_a = await _arm_a(client, profile_dir)

        if arm_a.get("frame_count", 0) > 0 and not arm_a.get("errors"):
            step("DONE", "ARM A SUCCEEDED — direct httpx POST with browser token works!")
            _log("verdict", {
                "arm": "A",
                "verdict": "VIABLE",
                "detail": "direct httpx POST accepted a browser-minted reCAPTCHA token",
            })
            return

        step("PHASE_2", f"Arm A result: status={arm_a.get('status')}, errors={arm_a.get('errors')}")

        # ====================================================================
        # Phase 3: ARM B — in-page fetch
        # ====================================================================
        step("PHASE_3", "Arm B: in-page fetch with fresh token...")
        # Need to clear the composer state from the failed attempt first
        # Navigate back to the project to reset state
        if project_id:
            await page.goto(f"{_ORIGIN}/project/{project_id}", wait_until="networkidle", timeout=30_000)
            await page.wait_for_selector('[contenteditable="true"]', timeout=30_000)
            await asyncio.sleep(2)

        arm_b = await _arm_b(client)

        if arm_b.get("frame_count", 0) > 0 and not arm_b.get("errors"):
            step("DONE", "ARM B SUCCEEDED — in-page fetch with browser token works!")
            _log("verdict", {
                "arm": "B",
                "verdict": "VIABLE_IN_PAGE",
                "detail": (
                    "in-page fetch accepted; httpx was refused (likely client fingerprint). "
                    "A no-selector route through page.evaluate(fetch) is viable."
                ),
            })
            return

        step("PHASE_3", f"Arm B result: status={arm_b.get('status')}, errors={arm_b.get('errors')}")

        # ====================================================================
        # ARM C would be: let the page submit normally
        # Only needed if both A and B fail — check if profile is bad
        # ====================================================================
        step("PHASE_4", "Both A and B failed. The profile may be refused (#906).")
        _log("verdict", {
            "arm": "C_needed",
            "verdict": "BOTH_REFUSED",
            "detail": (
                "Both httpx and in-page fetch were refused. "
                "A manual UI submit (Arm C) is needed to rule out profile refusal."
            ),
            "arm_a_errors": arm_a.get("errors"),
            "arm_b_errors": arm_b.get("errors"),
        })


if __name__ == "__main__":
    asyncio.run(main())
