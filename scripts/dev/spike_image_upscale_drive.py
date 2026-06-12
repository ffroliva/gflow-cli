#!/usr/bin/env python3
r"""Image-upscale spike — DRIVE the 1K/2K/4K download menu programmatically.

Unlike spike_image_upscale_capture.py (passive, human-driven), this script
drives the whole flow itself: land on the project gallery (cold-loading the
/edit/<media> deep-link 500s, so we navigate via the gallery + in-app routing),
click the target image tile to open the preview, click the download button,
read the menu, and click a scale option (default 2K). A passive network capture
runs throughout so we record the upscale wire.

It screenshots + dumps DOM at every stage to the out dir so selectors can be
discovered/corrected. Image ops are FREE (no credits). 4K is Ultra-gated; on a
Pro account it shows "Upgrade" — pick 1K/2K there.

Usage (headed, supervised):

    ! set PYTHONUTF8=1 && .venv\Scripts\python.exe scripts\dev\spike_image_upscale_drive.py ^
        --profile ffroliva ^
        --project ffb768fb-cf2d-48b7-a135-92978667c37d ^
        --media 9d5e015c-0162-4b63-9ec4-bd6bf7c68200 ^
        --locale pt --scale 2K

Outputs (gitignored): scripts/dev/_spike_out/spike_image_upscale_drive_<ts>.{json,*.png}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

# Reuse the capture filters/redactors from the passive spike.
from spike_image_upscale_capture import (  # noqa: E402
    _interesting,
    _is_binary,
    _keep_body,
    _redact_body,
    _redact_url,
)

# JS: enumerate clickable candidates (buttons + edit links) with icon ligatures
# and screen position, so we can spot the download button (top-right) and the
# gallery tiles (anchors to /edit/<id>).
_DUMP_CANDIDATES = r"""
(() => {
  const norm = (s) => (s || '').trim().replace(/\s+/g, ' ').slice(0, 50);
  const rect = (el) => { const r = el.getBoundingClientRect();
    return {x: Math.round(r.x), y: Math.round(r.y),
            w: Math.round(r.width), h: Math.round(r.height)}; };
  const icons = (el) => [...el.querySelectorAll('i.google-symbols, i.material-symbols-outlined, i')]
    .map(i => norm(i.textContent)).filter(Boolean).slice(0, 4);
  const SEL = 'button, [role="button"], [role="menuitem"], [role="menuitemradio"]';
  const buttons = [...document.querySelectorAll(SEL)]
    .map(el => ({
      tag: el.tagName, role: el.getAttribute('role'),
      text: norm(el.innerText || el.textContent),
      aria: el.getAttribute('aria-label'),
      haspopup: el.getAttribute('aria-haspopup'),
      state: el.getAttribute('data-state'),
      icons: icons(el),
      rect: rect(el),
    }))
    .filter(b => b.rect.w > 0 && b.rect.h > 0);
  const editLinks = [...document.querySelectorAll('a[href*="/edit/"]')]
    .map(a => ({ href: a.getAttribute('href'), text: norm(a.innerText), rect: rect(a) }));
  return { url: location.href, vw: window.innerWidth, vh: window.innerHeight,
           buttonCount: buttons.length, buttons, editLinks };
})()
"""


async def _shot(page: Any, out_dir: Path, name: str) -> None:
    try:
        await page.screenshot(path=str(out_dir / f"{name}.png"))
        step("shot", f"{name}.png", prefix="drive")
    except Exception as e:  # noqa: BLE001
        step("shot-fail", f"{name}: {e}", prefix="drive")


async def _dump(page: Any, out_dir: Path, name: str) -> dict[str, Any]:
    try:
        data: dict[str, Any] = await page.evaluate(_DUMP_CANDIDATES)
    except Exception as e:  # noqa: BLE001
        data = {"_error": str(e)}
    (out_dir / f"{name}.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    step(
        "dump",
        f"{name}: buttons={data.get('buttonCount')} editLinks={len(data.get('editLinks', []))}",
        prefix="drive",
    )
    return data


def _gallery_url(locale: str | None, project_id: str) -> str:
    seg = f"/{locale.strip().split('-', 1)[0].lower()}" if locale else ""
    return f"https://labs.google/fx{seg}/tools/flow/project/{project_id}"


async def _run(
    *,
    profile_dir: Path,
    project_id: str,
    media_id: str | None,
    locale: str | None,
    scale: str,
    window: int,
    out_path: Path,
) -> int:
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    events: list[dict[str, Any]] = []
    notes: list[str] = []

    def flush() -> None:
        out_path.write_text(
            json.dumps(
                {
                    "spike": "image-upscale-drive",
                    "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "projectId": project_id,
                    "mediaId": media_id,
                    "scaleRequested": scale,
                    "notes": notes,
                    "events": events,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def note(msg: str) -> None:
        notes.append(msg)
        step("note", msg, prefix="drive")
        flush()

    async with build_client(profile_dir, headless=False) as client:
        page = await client._checkout_page()  # noqa: SLF001

        def on_request(req: Any) -> None:
            try:
                if not _interesting(req.url, req.method):
                    return
                raw = req.post_data if req.method == "POST" else None
                body: Any = None
                if raw:
                    try:
                        body = json.loads(raw)
                    except Exception:  # noqa: BLE001
                        body = raw[:2000]
                events.append(
                    {
                        "dir": "request",
                        "t": round(time.monotonic(), 2),
                        "method": req.method,
                        "url": _redact_url(req.url),
                        "body": _redact_body(body) if isinstance(body, (dict, list)) else body,
                    }
                )
                low = req.url.lower()
                if any(k in low for k in ("upscale", "upres", "superres", "enhance")):
                    step(
                        "WIRE",
                        f"UPSCALE-candidate: {req.method} {_redact_url(req.url)[:120]}",
                        prefix="drive",
                    )
                flush()
            except Exception as e:  # noqa: BLE001
                step("on_request-err", str(e), prefix="drive")

        async def on_response(resp: Any) -> None:
            try:
                req = resp.request
                if not _interesting(resp.url, req.method):
                    return
                headers = await resp.all_headers()
                ev: dict[str, Any] = {
                    "dir": "response",
                    "t": round(time.monotonic(), 2),
                    "status": resp.status,
                    "method": req.method,
                    "url": _redact_url(resp.url),
                    "contentType": (headers.get("content-type") or "")[:60],
                    "contentLength": headers.get("content-length"),
                }
                if _keep_body(resp.url) and not _is_binary(headers):
                    try:
                        ev["body"] = _redact_body(await resp.json())
                    except Exception:  # noqa: BLE001
                        try:
                            ev["bodyText"] = (await resp.text())[:4000]
                        except Exception:  # noqa: BLE001
                            ev["body"] = "<unreadable>"
                events.append(ev)
                flush()
            except Exception as e:  # noqa: BLE001
                step("on_response-err", str(e), prefix="drive")

        page.on("request", on_request)
        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        # --- Step 1: land on the gallery (cold /edit/ deep-link 500s) ---
        gurl = _gallery_url(locale, project_id)
        note(f"step1 goto gallery {gurl}")
        await page.goto(gurl, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(4500)
        try:
            await page.wait_for_selector("a[href*='/edit/']", timeout=15_000)
        except Exception:  # noqa: BLE001
            note("step1 WARN: no a[href*=/edit/] tiles appeared within 15s")
        await _shot(page, out_dir, "01_gallery")
        gal = await _dump(page, out_dir, "01_gallery")

        # --- Step 2: open the target image (or first tile) via in-app routing ---
        target_sel = None
        links = gal.get("editLinks", []) if isinstance(gal, dict) else []
        if media_id:
            for lk in links:
                if media_id in (lk.get("href") or ""):
                    target_sel = f"a[href*='{media_id}']"
                    break
        if not target_sel and links:
            target_sel = "a[href*='/edit/']"
        if not target_sel:
            note("step2 ABORT: no gallery tiles to click. See 01_gallery.* — adjust selectors.")
            await page.wait_for_timeout(2000)
            client._checkin_page(page)  # noqa: SLF001
            return 0

        note(f"step2 click tile {target_sel}")
        try:
            await page.locator(target_sel).first.click(timeout=10_000)
        except Exception as e:  # noqa: BLE001
            note(f"step2 click failed: {e}")
        # Wait for SPA route to the preview (url -> /edit/...)
        try:
            await page.wait_for_function("() => location.href.includes('/edit/')", timeout=15_000)
            note(f"step2 OK url={page.url[-60:]}")
        except Exception:  # noqa: BLE001
            note(f"step2 WARN: url did not reach /edit/ (now {page.url[-60:]})")
        await page.wait_for_timeout(3500)
        await _shot(page, out_dir, "02_preview")
        await _dump(page, out_dir, "02_preview")

        # --- Step 3: find + click the download button ---
        dl_candidates = [
            "button:has(i.google-symbols:text-is('download'))",
            "button:has(i.google-symbols:text-is('download_2'))",
            "button:has(i.google-symbols:text-is('file_download'))",
            "button:has(i.google-symbols:text-is('save_alt'))",
            "button:has(i.google-symbols:text-is('file_save'))",
            "button[aria-label*='ownload']",
            "button[aria-label*='aixar']",  # Baixar (pt)
            "button[aria-label*='ransferir']",  # Transferir (pt)
            "button[aria-haspopup='menu']",
        ]
        clicked_dl = False
        for sel in dl_candidates:
            try:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    note(f"step3 download button via {sel}")
                    await loc.click(timeout=5000)
                    clicked_dl = True
                    break
            except Exception as e:  # noqa: BLE001
                step("dl-try", f"{sel}: {e}", prefix="drive")
        if not clicked_dl:
            note("step3 ABORT: no download button matched. See 02_preview.* for the real selector.")
            await page.wait_for_timeout(2000)
            client._checkin_page(page)  # noqa: SLF001
            return 0

        await page.wait_for_timeout(1500)
        await _shot(page, out_dir, "03_menu")
        await _dump(page, out_dir, "03_menu")

        # --- Step 4: click the scale option (1K/2K/4K) ---
        scale_norm = scale.strip().upper()
        scale_selectors = [
            f"[role='menuitem']:has-text('{scale_norm}')",
            f"[role='menuitemradio']:has-text('{scale_norm}')",
            f"button:has-text('{scale_norm}')",
            f"li:has-text('{scale_norm}')",
            f"text=/\\b{scale_norm}\\b/",
        ]
        clicked_scale = False
        for sel in scale_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    note(f"step4 click scale '{scale_norm}' via {sel}")
                    await loc.click(timeout=5000)
                    clicked_scale = True
                    break
            except Exception as e:  # noqa: BLE001
                step("scale-try", f"{sel}: {e}", prefix="drive")
        if not clicked_scale:
            note(f"step4 WARN: could not click '{scale_norm}'. See 03_menu.* for menu items.")
        else:
            note(f"step4 OK clicked {scale_norm} — watching wire for the upscale call")

        # --- Step 5: let the upscale wire settle ---
        settle = min(window, 60)
        note(f"step5 settle {settle}s capturing wire")
        end = time.monotonic() + settle
        while time.monotonic() < end:
            await asyncio.sleep(3)
            flush()
        await _shot(page, out_dir, "04_after_scale")
        await _dump(page, out_dir, "04_after_scale")

        client._checkin_page(page)  # noqa: SLF001

    ups = sum(
        1
        for e in events
        if any(k in e.get("url", "").lower() for k in ("upscale", "upres", "superres", "enhance"))
    )
    print(f"\n[drive] events={len(events)} upscale-candidate={ups}", flush=True)
    print(f"[drive] out -> {out_path}", flush=True)
    print(f"[drive] screenshots/dumps -> {out_dir}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Drive Flow image 1K/2K/4K download menu — wire capture, 0 credits."
    )
    p.add_argument("--profile", default=os.environ.get("GFLOW_CLI_PROFILE", "ffroliva"))
    p.add_argument("--project", required=True)
    p.add_argument(
        "--media", default=None, help="Target image media UUID (else first gallery tile)."
    )
    p.add_argument(
        "--locale", default="pt", help="Locale segment (default 'pt' — matches the account)."
    )
    p.add_argument("--scale", default="2K", help="Scale to click: 1K | 2K | 4K (default 2K).")
    p.add_argument(
        "--window", type=int, default=60, help="Post-click wire-settle seconds (default 60)."
    )
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    profile_dir = resolve_profile_dir(args.profile)
    out_path = (
        Path(args.out) if args.out else default_out_path("spike_image_upscale_drive", ".json")
    )
    step("--", f"profile={args.profile} project={args.project} scale={args.scale}", prefix="drive")
    try:
        return asyncio.run(
            _run(
                profile_dir=profile_dir,
                project_id=args.project,
                media_id=args.media,
                locale=args.locale,
                scale=args.scale,
                window=args.window,
                out_path=out_path,
            )
        )
    except KeyboardInterrupt:
        print("[drive] aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
