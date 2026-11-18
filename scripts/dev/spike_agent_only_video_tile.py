r"""What marks a FINISHED video tile on #799's agent-only composer? ($0)

Live 2026-09-15: a t2v run timed out although the clip existed — the incident's ui.json
counted zero `<video>` elements, while the 2026-09-15 drive spike had seen
`<video src=".../video/<uuid>">` inside `flow-video-tile` ~30 s after the poster. So a
`<video>` is not a reliable completion signal. This reads every `flow-video-tile` (and the
chat's `flow-a2ui-video-option`) structurally, then hovers the first tile and reads again.

    python scripts/dev/spike_agent_only_video_tile.py --profile <name> --project <id>

Pre-registered readings:

| Observation | Reading |
|---|---|
| `<video>` appears only after hover | the grid mounts the player lazily; completion must key on something else |
| a finished tile carries a structural marker (ligature, class, attribute) absent while queued | that marker is the completion signal |
| a tile exposes the clip URL without `<video>` (attribute, `<source>`, data-*) | parse the uuid there |
| nothing distinguishes it | fall back to the clip URL request, or a status RPC |
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

_TILES_JS = r"""
() => [...document.querySelectorAll('flow-video-tile, flow-a2ui-video-option')].map(t => {
  const attrs = (e) => Object.fromEntries([...e.attributes]
    .filter(a => !/^_ng|^ng-/.test(a.name))
    .map(a => [a.name, a.value.replace(/Signature=[^&"]+/g, 'Signature=REDACTED').slice(0, 160)]));
  return {
    tag: t.tagName.toLowerCase(),
    attrs: attrs(t),
    ligatures: [...t.querySelectorAll('mat-icon, i')].map(i => (i.textContent || '').trim()),
    nodes: [...t.querySelectorAll('*')].map(e => ({
      tag: e.tagName.toLowerCase(), cls: [...e.classList].slice(0, 5), attrs: attrs(e),
      text: [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(' ').slice(0, 40),
    })).filter(n => n.tag.includes('-') || ['img','video','source','button','span'].includes(n.tag) || n.text),
  };
})
"""


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    f: dict[str, Any] = {"question": "#799 finished video tile marker"}
    url = f"https://flow.google.com/project/{args.project}"
    async with build_client(resolve_profile_dir(args.profile)) as client:
        page = await client._context.new_page()  # noqa: SLF001 - spike reads the live context
        requests: list[str] = []
        page.on(
            "request",
            lambda r: requests.append(r.url.split("?")[0]) if "flow-content.google" in r.url else None,
        )
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(10_000)
        f["before_hover"] = await page.evaluate(_TILES_JS)
        step("before_hover", f"tiles={len(f['before_hover'])}")
        tile = page.locator("flow-video-tile").first
        if await tile.count():
            await tile.hover()
            await page.wait_for_timeout(3_000)
            f["after_hover"] = await page.evaluate(_TILES_JS)
            step("after_hover", f"videos={sum(n['tag']=='video' for t in f['after_hover'] for n in t['nodes'])}")
            # Is the hover player's opaque /asb/ URL a downloadable MP4? Bytes are not kept.
            video = tile.locator("video").first
            if await video.count():
                src = await video.get_attribute("src") or ""
                resp = await page.request.get(src, timeout=120_000)
                body = await resp.body()
                f["asb_download"] = {
                    "status": resp.status,
                    "final_host": resp.url.split("/")[2],
                    "content_type": resp.headers.get("content-type"),
                    "bytes": len(body),
                    "ftyp": body[4:8] == b"ftyp",
                }
                step("asb_download", json.dumps(f["asb_download"]))
            # Does opening the tile name the media id anywhere (URL, dialog media src)?
            before_url = page.url
            await tile.locator("img, video").first.click(timeout=5_000)
            await page.wait_for_timeout(4_000)
            f["after_open"] = {
                "url_changed": page.url != before_url,
                "url_path": page.url.split("?")[0].replace(args.project, "<project>"),
                "cdn_media": await page.evaluate(
                    "() => [...document.querySelectorAll('video, img, source')]"
                    ".map(e => e.currentSrc || e.src || '')"
                    ".filter(s => s.includes('flow-content.google/video/'))"
                    ".map(s => s.split('?')[0])"
                ),
            }
            step("after_open", json.dumps(f["after_open"]))
        f["cdn_requests"] = sorted(set(requests))
    out = default_out_path("spike_agent_only_video_tile")
    out.write_text(json.dumps(f, indent=2, ensure_ascii=False), encoding="utf-8")
    step("done", f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
