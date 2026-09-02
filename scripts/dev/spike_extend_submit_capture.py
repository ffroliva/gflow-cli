"""PAID capture — drive Flow's own UI to extend a clip and record the wire traffic.

Settles the three questions left open by
`docs/superpowers/spikes/2026-08-31-veo-extend-route-recon.md`:

  1. The `batchAsyncGenerateVideoExtendVideo` REQUEST body shape.
  2. Which model key Flow itself sends on a SERVICE_TIER_INTERMEDIATE account.
     This is the decisive one: the six orderable extend keys carry NO
     displayName in projectInitialData, yet the menu renders
     "Extend (Veo 3.1 - Lite)" — a label matching no key. So the label does
     NOT identify the wire key and our landscape/INTERMEDIATE inference
     (`veo_3_1_extend_fast_landscape`) is a hypothesis, not a finding.
  3. The RESPONSE shape — same as batchAsyncGenerateVideoText, or different.

Letting Flow's own UI compose the request is the point: it is ground truth for
key selection and body shape in a way our transport's guess could never be.

**COST: one video extend (~20 credits at this tier), only with --submit.**
Without --submit it walks to the composer and stops — free.

The request logger is armed BEFORE the first navigation, so if any click bills
unexpectedly the traffic is still captured rather than lost.

Usage:
    python scripts/dev/spike_extend_submit_capture.py --profile ffroliva \
        --project <uuid> --open-clip "Ocean wave"            # free recon
    python scripts/dev/spike_extend_submit_capture.py ... --submit --prompt "..."
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts" / "dev"))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api import routes  # noqa: E402

WATCH = ("batchAsyncGenerateVideoExtendVideo", "batchCheckAsyncVideoGenerationStatus")

_DUMP_JS = r"""
() => {
    const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const sel = 'button, [role="button"], [role="menuitem"], [role="textbox"], textarea';
    return {
        url: location.href,
        controls: [...document.querySelectorAll(sel)].filter(vis).map((e) => ({
            tag: e.tagName.toLowerCase(),
            role: e.getAttribute('role') || '',
            aria: (e.getAttribute('aria-label') || '').slice(0, 80),
            text: (e.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80),
        })),
        credits: (document.body.innerText.match(/[\d,]+\s*(credits?|cr[ée]ditos?)/i) || [''])[0],
    };
}
"""


async def _dump(page: Any, label: str) -> dict[str, Any]:
    d: dict[str, Any] = await page.evaluate(_DUMP_JS)
    d["label"] = label
    step(label, f"{len(d['controls'])} controls · credits={d['credits']!r}")
    return d


async def _run(args: argparse.Namespace) -> int:
    reqs: list[dict[str, Any]] = []
    resps: list[dict[str, Any]] = []
    dumps: list[dict[str, Any]] = []

    async with build_client(resolve_profile_dir(args.profile)) as client:
        ctx = client._context  # noqa: SLF001
        assert ctx is not None
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        def on_request(req: Any) -> None:
            if not any(w in req.url for w in WATCH):
                return
            try:
                body = req.post_data
            except Exception:  # noqa: BLE001
                body = None
            reqs.append({"url": req.url, "method": req.method, "post_data": body})
            step("WIRE", f"REQUEST {req.url.split('/')[-1]} ({len(body or '')} bytes)")

        async def on_response(resp: Any) -> None:
            if not any(w in resp.url for w in WATCH):
                return
            try:
                text = await resp.text()
            except Exception:  # noqa: BLE001
                text = ""
            resps.append({"url": resp.url, "status": resp.status, "body": text})
            step("WIRE", f"RESPONSE {resp.status} {resp.url.split('/')[-1]} ({len(text)} bytes)")

        page.on("request", on_request)
        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        step("nav", f"editor for {args.project}")
        await page.goto(
            routes.project_editor_url("en", args.project),
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        for _ in range(30):
            if await page.locator('div[role="textbox"], textarea').count() > 0:
                break
            await page.wait_for_timeout(1000)
        await page.wait_for_timeout(3000)

        tile = page.locator(f'[role="button"]:has-text("{args.open_clip}")').first
        if not await tile.count():
            step("clip", f"no tile matching {args.open_clip!r} — aborting")
            return 1
        await tile.click(timeout=8000)
        await page.wait_for_timeout(3500)
        dumps.append(await _dump(page, "clip_open"))

        add = page.locator('button:has-text("Add Clip")').first
        if not await add.count():
            step("path", "no 'Add Clip' — aborting")
            return 1
        await add.click(timeout=8000)
        await page.wait_for_timeout(2000)

        ext = page.locator('[role="menuitem"]:has-text("Extend")').first
        if not await ext.count():
            step("path", "no Extend menu item — aborting")
            return 1
        label = (await ext.text_content() or "").strip()
        step("path", f"clicking menu item: {label!r}")
        await ext.click(timeout=8000)
        await page.wait_for_timeout(3000)
        dumps.append(await _dump(page, "extend_composer"))

        if not args.submit:
            step("STOP", "--submit not given: stopping before any billable action")
        else:
            box = page.locator('div[role="textbox"], textarea').first
            if await box.count():
                step("submit", f"typing extension prompt: {args.prompt!r}")
                await box.click(timeout=6000)
                await box.type(args.prompt, delay=25)
                await page.wait_for_timeout(1200)
            gen = page.locator(
                'button[aria-label*="Create" i], button:has-text("Create"), '
                'button[aria-label*="Generate" i]'
            ).last
            if not await gen.count():
                step("submit", "no Create/Generate button found — nothing submitted")
            else:
                step("submit", "*** CLICKING GENERATE — THIS BILLS CREDITS ***")
                await gen.click(timeout=10_000)
                # Wait for the extend request to actually leave the page.
                for _ in range(40):
                    if reqs:
                        break
                    await page.wait_for_timeout(1000)
                await page.wait_for_timeout(8000)
                dumps.append(await _dump(page, "post_submit"))

    out = default_out_path("spike_extend_submit_capture")
    out.write_text(
        json.dumps(
            {"submitted": args.submit, "requests": reqs, "responses": resps, "dumps": dumps},
            indent=2,
        ),
        encoding="utf-8",
    )
    step("out", str(out))
    step("done", f"{len(reqs)} extend requests, {len(resps)} responses captured")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="ffroliva")
    ap.add_argument("--project", required=True)
    ap.add_argument("--open-clip", required=True)
    ap.add_argument("--submit", action="store_true", help="BILLS CREDITS")
    ap.add_argument("--prompt", default="the wave recedes back into the calm ocean")
    return asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
