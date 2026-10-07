r"""How does Flow deliver a 270p "animated GIF" export? ($0 — the export measured free)

`gflow video upscale --scale 270p` timed out after 120 s (2026-10-07) waiting for an
`image/gif` blob from `URL.createObjectURL`, while 1080p and 720p exports arrive that way.
This records EVERY delivery channel during one 270p export, with anchor clicks recorded
(not followed) exactly as the transport does:

  * every createObjectURL argument: type + size (Blob/MediaSource), no content
  * every anchor click: href scheme/host/path tail, `download` attribute
  * Playwright `download` events, new pages, and responses whose content-type is an image
    or video, or whose URL mentions gif — status, content-type, size
  * batchexecute rpcids seen

PRE-REGISTERED READING:
  * a blob with type '' / octet-stream and a GIF8 body -> widen the MIME filter
  * an anchor with an https/data href (no blob) -> fetch that href instead
  * a Playwright download event -> use page.expect_download
  * nothing within the wait -> the menu click did not start an export; say so, settle nothing

    python scripts/dev/spike_gif_export_delivery.py --profile P --video PROJECT:MEDIA
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402, isort: skip

from gflow_cli.api.transports.batchexecute import parse_frames  # noqa: E402
from gflow_cli.api.transports.migrated_recover import MIGRATED_CLIP_URL  # noqa: E402
from gflow_cli.api.transports.migrated_upscale import DOWNLOAD_BUTTON_SELECTOR  # noqa: E402

_HOOK = r"""() => {
  window.__spike = {objs: [], anchors: [], t0: performance.now()};
  const origURL = URL.createObjectURL;
  URL.createObjectURL = function (obj) {
    try {
      window.__spike.objs.push({t: Math.round(performance.now() - window.__spike.t0), ctor: obj && obj.constructor && obj.constructor.name,
                                type: obj && obj.type, size: obj && obj.size});
    } catch (e) {}
    return origURL.apply(this, arguments);
  };
  HTMLAnchorElement.prototype.click = function () {
    try {
      const u = this.href || '';
      window.__spike.anchors.push({scheme: u.split(':')[0], len: u.length,
                                   host: u.startsWith('http') ? new URL(u).host : '',
                                   download: this.getAttribute('download')});
    } catch (e) {}
  };
}"""


async def _main(profile: str, video: str, wait_s: float, out: Path) -> int:
    project_id, media_id = video.split(":")
    report: dict[str, Any] = {"responses": [], "rpcs": [], "events": []}

    async def on_response(resp: Any) -> None:
        url = str(getattr(resp, "url", ""))
        try:
            ctype = (resp.headers or {}).get("content-type", "")
        except Exception:  # noqa: BLE001
            ctype = ""
        if "batchexecute" in url:
            try:
                for rid, _ in parse_frames(await resp.text()):
                    report["rpcs"].append(rid)
            except Exception:  # noqa: BLE001
                pass
        elif ctype.startswith(("image/gif", "video/")) or "gif" in url.lower():
            u = urlsplit(url)
            report["responses"].append(
                {
                    "host": u.hostname,
                    "path_tail": u.path[-24:],
                    "status": resp.status,
                    "ctype": ctype,
                    "len": (resp.headers or {}).get("content-length"),
                }
            )

    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 - dev instrument
        assert page is not None
        page.on("response", on_response)
        page.on("download", lambda d: report["events"].append(f"download:{d.suggested_filename}"))
        page.on("close", lambda *_: report["events"].append("page_close"))
        page.context.on("page", lambda pg: report["events"].append(f"new_page:{pg.url[:60]}"))

        await page.goto(
            MIGRATED_CLIP_URL.format(project_id=project_id, media_id=media_id),
            wait_until="domcontentloaded",
        )
        await page.wait_for_timeout(7000)
        await page.locator(DOWNLOAD_BUTTON_SELECTOR).first.click()
        await page.wait_for_timeout(1500)
        items = page.locator('[role="menuitem"]')
        texts = [await items.nth(i).inner_text() for i in range(await items.count())]
        idx = next((i for i, t in enumerate(texts) if "270" in t), None)
        report["menu_has_270"] = idx is not None
        if idx is not None:
            await page.evaluate(_HOOK)
            await items.nth(idx).click()
            step("click", "270p clicked; recording")
            waited = 0.0
            while waited < wait_s and not page.is_closed():
                await asyncio.sleep(2)
                waited += 2
            if not page.is_closed():
                report["hooks"] = await page.evaluate("window.__spike")
            report["waited_s"] = waited
    report["rpcs"] = sorted(set(report["rpcs"]))
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    step("done", str(out))
    print(json.dumps(report, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--video", required=True, help="PROJECT:MEDIA")
    ap.add_argument("--wait", type=float, default=90.0)
    a = ap.parse_args()
    sys.exit(asyncio.run(_main(a.profile, a.video, a.wait, default_out_path("spike_gif_export"))))
