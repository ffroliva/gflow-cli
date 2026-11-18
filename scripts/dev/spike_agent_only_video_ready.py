r"""When is an agent-only t2v clip READY, and where does its id surface? (CREDITS: one 4 s video)

Live 2026-09-15 a driver run timed out on a clip that existed: grid tiles on a loaded page
carry opaque `flow.google.com/asb/...` media (no uuid), and `<video>` mounts only on hover.
The chat reply's `flow-a2ui-video-option` poster does carry `flow-content.google/image/<uuid>`,
the clip's own id. This records, every 5 s from submit, what could mark readiness:

* the chat option (uuid, ligatures, classes)
* the newest grid tile (thumbnail host kind, ligatures, any text — a progress %?)
* whether hovering that tile mounts a `<video>`, and its src host kind
* which batchexecute replies mention the clip uuid, and whether one carries its
  `flow-content.google/video/<uuid>` URL (bodies are not kept — only rpcid + booleans)

Assumes "Confirm before generating" is Never (no gate). Nothing is deleted.

    python scripts/dev/spike_agent_only_video_ready.py --profile <name> --project <id>

Pre-registered readings:

| Observation | Reading |
|---|---|
| a batchexecute reply carries `video/<uuid>` at readiness | wire completion + signed download URL, as on the classic path |
| hover `<video>` appears only once ready, absent while queued | hover presence is the readiness signal; download via its /asb/ src |
| the newest tile shows a progress element/text while queued, gone when ready | that element's absence is the signal |
| none of these change between queued and ready | readiness is not observable here without opening the clip |
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

_STATE_JS = r"""
() => {
  const kind = (s) => !s ? '' : s.includes('flow-content.google/') ? 'cdn:' + s.split('?')[0].split('/').slice(-2).join('/')
    : s.includes('/asb/') ? 'asb' : 'other';
  const opts = [...document.querySelectorAll('flow-a2ui-video-option')].map(o => ({
    poster: kind((o.querySelector('img') || {}).src || ''),
    ligs: [...o.querySelectorAll('mat-icon')].map(i => i.textContent.trim()),
    cls: [...o.classList], text: (o.innerText || '').trim().slice(0, 40),
  }));
  const t = document.querySelector('flow-video-tile, flow-image-tile');
  const tile = !t ? null : {
    tag: t.tagName.toLowerCase(),
    title: ((t.querySelector('.footer-title') || {}).textContent || '').trim().slice(0, 40),
    media: [...t.querySelectorAll('img, video')].map(e => e.tagName.toLowerCase() + ':' + kind(e.currentSrc || e.src || '')),
    ligs: [...t.querySelectorAll('mat-icon')].map(i => i.textContent.trim()),
    nodes: [...t.querySelectorAll('*')].filter(e => e.tagName.includes('-') || /progress|spinner|pending|queue|load/i.test(e.className))
      .map(e => e.tagName.toLowerCase() + '.' + [...e.classList].slice(0, 3).join('.')),
    text: [...t.querySelectorAll('*')].flatMap(e => [...e.childNodes].filter(n => n.nodeType === 3)
      .map(n => n.textContent.trim())).filter(Boolean).slice(0, 8),
  };
  return { opts, tile, in_flight: document.querySelectorAll('flow-stop-icon-button').length };
}
"""


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--wait-s", type=float, default=300.0)
    ap.add_argument(
        "--prompt", default="a small red paper boat turning slowly on still water, overcast light"
    )
    args = ap.parse_args()
    url = f"https://flow.google.com/project/{args.project}"
    f: dict[str, Any] = {"question": "#799 agent-only video readiness", "rpc": [], "timeline": []}
    t0 = time.monotonic()
    watch: dict[str, str] = {}  # uuid under watch, once known

    async def on_response(response: Any) -> None:
        if "batchexecute" not in response.url:
            return
        rpcids = response.url.split("rpcids=", 1)[1].split("&", 1)[0] if "rpcids=" in response.url else "?"
        try:
            body = await response.text()
        except Exception:  # noqa: BLE001
            return
        entry: dict[str, Any] = {"t": round(time.monotonic() - t0), "rpcids": rpcids}
        u = watch.get("uuid")
        if u:
            entry["mentions_uuid"] = u in body
            entry["has_video_url"] = f"flow-content.google/video/{u}" in body
        entry["video_urls"] = len(re.findall(rf"flow-content\.google/video/{UUID}", body))
        f["rpc"].append(entry)

    async with build_client(resolve_profile_dir(args.profile)) as client:
        page = await client._context.new_page()  # noqa: SLF001 - spike reads the live context
        page.on("response", on_response)
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(10_000)
        before = await page.evaluate(_STATE_JS)
        f["before"] = before
        baseline = {o["poster"] for o in before["opts"]}
        directive = f"Make me a 4 second video of {args.prompt} in a 9:16 aspect ratio."
        editor = page.locator(".ProseMirror").last
        await editor.click(timeout=8_000)
        await page.keyboard.insert_text(directive)
        await page.locator("flow-generate-icon-button button").last.click(timeout=8_000)
        t0 = time.monotonic()
        step("submitted", directive)
        partial = default_out_path("spike_agent_only_video_ready_partial")
        while time.monotonic() - t0 < args.wait_s:
            await page.wait_for_timeout(5_000)
            s = await page.evaluate(_STATE_JS)
            new = [o for o in s["opts"] if o["poster"] not in baseline]
            if new and "uuid" not in watch:
                m = re.search(UUID, new[-1]["poster"])
                if m:
                    watch["uuid"] = m.group(0)
            hover_video = ""
            tile = page.locator("flow-video-tile").first
            if await tile.count():
                await tile.hover()
                await page.wait_for_timeout(1_500)
                v = tile.locator("video").first
                if await v.count():
                    src = await v.get_attribute("src") or ""
                    hover_video = "asb" if "/asb/" in src else ("cdn" if "flow-content" in src else "other")
                await page.mouse.move(5, 5)
            elapsed = round(time.monotonic() - t0)
            s["t"], s["new_opts"], s["hover_video"], s["uuid"] = elapsed, new, hover_video, watch.get("uuid")
            f["timeline"].append(s)
            step("poll", f"t={elapsed}s in_flight={s['in_flight']} new_opts={len(new)} hover_video={hover_video!r} "
                 f"tile={s['tile'] and s['tile']['title']!r} media={s['tile'] and s['tile']['media']}")
            partial.write_text(json.dumps(f, indent=2, ensure_ascii=False), encoding="utf-8")
            if hover_video and watch.get("uuid") and elapsed > 30:
                break
    out = default_out_path("spike_agent_only_video_ready")
    out.write_text(json.dumps(f, indent=2, ensure_ascii=False), encoding="utf-8")
    step("done", f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
