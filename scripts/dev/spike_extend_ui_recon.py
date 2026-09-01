"""Locate the *Extend* affordance in Flow's editor — recon before the paid click.

Follow-up to `docs/superpowers/spikes/2026-08-31-veo-extend-route-recon.md`,
which settled the model table and route name from server data but left three
questions that only a real submit can answer:

  1. What does the `batchAsyncGenerateVideoExtendVideo` request body look like?
  2. Does Flow's own UI send the un-suffixed `veo_3_1_extend_fast_landscape`
     on this SERVICE_TIER_INTERMEDIATE account (settling the tier cross-check)?
  3. What does the response look like — same shape as batchAsyncGenerateVideoText?

That submit costs ~20 credits. This script spends ZERO: it only finds and
reports the affordance so the paid run is one confident click, not a fumble.

It never clicks Extend. It hovers, opens menus, and dumps what it sees.

Usage:
    python scripts/dev/spike_extend_ui_recon.py --profile ffroliva --project <uuid>
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

# Dump every interactive element with its text/aria so the extend control can be
# identified by inspection rather than guessed. Locale-aware: this account may
# render pt-BR, so we do NOT filter on the English word here — we dump and read.
_DUMP_JS = r"""
() => {
    const vis = (e) => {
        const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    };
    const desc = (e) => ({
        tag: e.tagName.toLowerCase(),
        role: e.getAttribute('role') || '',
        aria: (e.getAttribute('aria-label') || '').slice(0, 80),
        title: (e.getAttribute('title') || '').slice(0, 80),
        text: (e.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80),
        cls: (e.className || '').toString().slice(0, 60),
    });
    const sel = 'button, [role="button"], [role="menuitem"], [role="tab"], a[href]';
    return {
        lang: document.documentElement.lang || '',
        url: location.href,
        videos: [...document.querySelectorAll('video')].filter(vis).map((v) => ({
            src: (v.currentSrc || v.src || '').slice(0, 120),
            w: v.videoWidth, h: v.videoHeight, dur: v.duration,
        })),
        controls: [...document.querySelectorAll(sel)].filter(vis).map(desc),
    };
}
"""


async def _dump(page: Any, label: str) -> dict[str, Any]:
    d: dict[str, Any] = await page.evaluate(_DUMP_JS)
    d["label"] = label
    step(label, f"{len(d['controls'])} controls, {len(d['videos'])} <video>, lang={d['lang']!r}")
    # Surface anything that smells like extend in ANY locale by matching the
    # stem 'extend'/'estend'/'esten' (en/pt/es share the Latin stem).
    hot = [
        c
        for c in d["controls"]
        if any(
            s in (c["text"] + c["aria"] + c["title"]).lower()
            for s in ("extend", "estend", "esten", "erweiter", "prolong")
        )
    ]
    for c in hot:
        step(label, f"  EXTEND-ish: {c}")
    return d


async def _run(profile: str, project: str, open_clip: str, clicks: list[str]) -> int:
    dumps: list[dict[str, Any]] = []

    async with build_client(resolve_profile_dir(profile)) as client:
        ctx = client._context  # noqa: SLF001
        assert ctx is not None
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        step("nav", f"goto editor for project {project}")
        await page.goto(
            routes.project_editor_url("en", project),
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        for _ in range(30):
            if await page.locator('div[role="textbox"], textarea').count() > 0:
                break
            await page.wait_for_timeout(1000)
        await page.wait_for_timeout(4000)
        dumps.append(await _dump(page, "editor"))

        # The editor renders the library as div[role=button] tiles, not <video>
        # (run 1: 0 visible <video>). Open the clip first — the bundle's
        # VIDEO_EDITOR_EXTEND surface implies extend lives in the clip view.
        if open_clip:
            tile = page.locator(f'[role="button"]:has-text("{open_clip}")').first
            if await tile.count():
                step("clip", f"opening tile matching {open_clip!r}")
                await tile.click(timeout=8000)
                await page.wait_for_timeout(3500)
                dumps.append(await _dump(page, "clip_opened"))
                # Extend may sit behind the clip view's own overflow menu.
                for sel in ("[aria-label*='more' i]", "[aria-label*='option' i]"):
                    loc = page.locator(sel)
                    for j in range(min(await loc.count(), 3)):
                        try:
                            await loc.nth(j).click(timeout=3000)
                            await page.wait_for_timeout(1000)
                            dumps.append(await _dump(page, f"clipmenu_{j}_{sel[:18]}"))
                            await page.keyboard.press("Escape")
                            await page.wait_for_timeout(400)
                        except Exception:  # noqa: BLE001, S110
                            pass
            else:
                step("clip", f"no tile matched {open_clip!r}")

        # Follow an explicit click path by accessible name. The clip view has
        # no visible Extend; the bundle's SCENE_BUILDER_EXTEND_SUBMITTED implies
        # it is reached through the timeline's add/clip flow.
        for name in clicks:
            loc = page.locator(
                f'button:has-text("{name}"), [role="button"]:has-text("{name}")'
            ).first
            if not await loc.count():
                step("click", f"no control named {name!r}")
                continue
            step("click", f"clicking {name!r}")
            try:
                await loc.click(timeout=8000)
                await page.wait_for_timeout(2500)
            except Exception as e:  # noqa: BLE001
                step("click", f"  failed: {e}")
                continue
            dumps.append(await _dump(page, f"after_{name[:18]}"))

        # Extend usually hangs off a per-clip control, so walk each rendered
        # video: hover it, then open any overflow/more menu that appears.
        vids = page.locator("video")
        n = await vids.count()
        step("clips", f"{n} <video> elements rendered")
        for i in range(min(n, 4)):
            v = vids.nth(i)
            try:
                await v.scroll_into_view_if_needed(timeout=4000)
                await v.hover(timeout=4000)
                await page.wait_for_timeout(1200)
            except Exception as e:  # noqa: BLE001
                step("clips", f"  clip {i}: hover failed {e}")
                continue
            dumps.append(await _dump(page, f"clip{i}_hover"))

            # Try any overflow trigger that appeared near the clip.
            for sel in (
                "[aria-label*='more' i]",
                "[aria-label*='mais' i]",
                "button:has(i.google-symbols:text-is('more_vert'))",
                "button:has(i.google-symbols:text-is('more_horiz'))",
            ):
                loc = page.locator(sel)
                if await loc.count() == 0:
                    continue
                try:
                    await loc.first.click(timeout=3000)
                    await page.wait_for_timeout(1000)
                    dumps.append(await _dump(page, f"clip{i}_menu_{sel[:22]}"))
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(400)
                except Exception:  # noqa: BLE001, S110
                    pass

        out = default_out_path("spike_extend_ui_recon")
        out.write_text(json.dumps(dumps, indent=2), encoding="utf-8")
        step("out", str(out))
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="ffroliva")
    ap.add_argument("--project", required=True)
    ap.add_argument("--open-clip", default="")
    ap.add_argument("--click", action="append", default=[])
    a = ap.parse_args()
    return asyncio.run(_run(a.profile, a.project, a.open_clip, a.click))


if __name__ == "__main__":
    raise SystemExit(main())
