r"""#922: what in Flow's download menu is locale-free, and does a 1080p export bill? ($0 / opt-in)

The upscale port picks the 2K / 4K / 1080p items by their visible label (``:has-text``),
and the 2026-09-30 spike recorded those labels translated ("2K (Aprimorada)"). AGENTS.md
forbids text-label selectors. This dumps the STRUCTURE of every ``[role=menuitem]`` in the
image and video download menus — tag, attributes, icon ligatures, position, and the digit
tokens of the label (``2K``, ``1080``) — so the anchor can be chosen on evidence. Run it on
two profiles with different account locales to see what stays put.

``--export-1080p`` additionally clicks the video's 1080p item and records every batchexecute
frame on ``p0UkFb`` / ``jwpduf`` as an int-keeping skeleton, every ``URL.createObjectURL``
blob (type + size only), and whether the export reply carries a credit balance the way the
generation submit reply does (``[null, <balance>, …]``). Cost of that arm: UNKNOWN — that is
the question; everything else is navigation and DOM reads ($0).

PRE-REGISTERED READING:
  * items carry a stable attribute or icon per option -> anchor on it
  * items differ only by label text, but in the SAME ORDER on both locales, and the digit
    token of the label matches the option -> anchor on position, assert the digit token
  * order differs across locales -> neither; report, do not guess
  * export: blob count == 1 and its size matches no other blob on the page -> first-blob is
    safe in this observation; >1 video blob -> must tie to the ``_upsampled`` reply
  * export: a balance slot that moves by N -> 1080p costs N; unchanged -> free (1 obs.)

    python scripts/dev/spike_upscale_menu_anchors.py --profile P --image PROJ:MEDIA --video PROJ:MEDIA [--export-1080p]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402, isort: skip

from gflow_cli.api.transports.batchexecute import parse_frames  # noqa: E402

_UUID = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")

_MENU_JS = r"""() => [...document.querySelectorAll(
  '.cdk-overlay-container button, .cdk-overlay-container [role], .cdk-overlay-container a'
)].map((el, i) => ({
  role: el.getAttribute('role'),
  index: i,
  tag: el.tagName.toLowerCase(),
  attrs: [...el.attributes].map(a => a.name + (/^(class|style|id)$/.test(a.name) ? ''
         : '=' + a.value.slice(0, 40))),
  disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
  icons: [...el.querySelectorAll('mat-icon, i, .google-symbols')].map(x => x.textContent.trim()),
  digits: (el.textContent.match(/\d+\s*[KkPp]?/g) || []).map(s => s.replace(/\s/g, '')),
  text_len: el.textContent.trim().length,
  children: [...el.children].map(c => c.tagName.toLowerCase()),
}))"""

_BLOB_HOOK = r"""() => {
  if (window.__spikeBlobs) return;
  window.__spikeBlobs = [];
  const orig = URL.createObjectURL;
  URL.createObjectURL = function (obj) {
    try { window.__spikeBlobs.push({type: obj && obj.type, size: obj && obj.size,
                                    t: performance.now()}); } catch (e) {}
    return orig.apply(this, arguments);
  };
}"""


def skeleton(node: Any) -> Any:
    if isinstance(node, list):
        return [skeleton(c) for c in node]  # type: ignore[misc]
    if isinstance(node, str):
        if _UUID.match(node.split("_")[0]):
            return "<uuid>" + ("_" + node.split("_", 1)[1] if "_" in node else "")
        return "<url>" if node.startswith("https://") else "<str>"
    return node


async def _open_menu(page: Any, url: str) -> list[dict[str, Any]]:
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(7000)
    dl = page.locator(
        'button:has(mat-icon:text-is("download")), button:has(.google-symbols:text-is("download"))'
    ).first
    if not await dl.count():
        return [{"error": "no download button on the edit route"}]
    await dl.click()
    await page.wait_for_timeout(1500)
    return await page.evaluate(_MENU_JS)


async def _main(profile: str, image: str | None, video: str | None, export: bool, out: Path) -> int:
    report: dict[str, Any] = {"profile": profile}
    frames: list[dict[str, Any]] = []

    async def on_response(resp: Any) -> None:
        if "batchexecute" not in str(getattr(resp, "url", "")):
            return
        try:
            text = await resp.text()
        except Exception:  # noqa: BLE001
            return
        for rid, payload in parse_frames(text):
            if rid in ("p0UkFb", "jwpduf", "SPrCad"):
                frames.append({"rpc": rid, "skeleton": json.dumps(skeleton(payload))[:600]})

    edit = "https://flow.google.com/project/{}/edit/{}"
    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 - dev instrument
        assert page is not None
        page.on("response", on_response)
        events: list[str] = []
        report["events"] = events
        page.on("close", lambda *_: events.append("page_close"))
        page.on("download", lambda d: events.append(f"download:{d.suggested_filename[-12:]}"))
        page.context.on("page", lambda pg: events.append(f"new_page:{pg.url[:40]}"))
        if image:
            report["image_menu"] = await _open_menu(page, edit.format(*image.split(":")))
            report["lang"] = await page.evaluate("document.documentElement.lang")
            await page.keyboard.press("Escape")
        if video:
            report["video_menu"] = await _open_menu(page, edit.format(*video.split(":")))
            report["lang"] = await page.evaluate("document.documentElement.lang")
            if export and isinstance(report["video_menu"], list):
                idx = next(
                    (
                        m["index"]
                        for m in report["video_menu"]
                        if "1080" in "".join(m.get("digits", []))
                    ),
                    None,
                )
                report["export_clicked_index"] = idx
                if idx is not None:
                    await page.evaluate(_BLOB_HOOK)
                    await (
                        page.locator(
                            ".cdk-overlay-container button, .cdk-overlay-container [role], "
                            ".cdk-overlay-container a"
                        )
                        .nth(idx)
                        .click()
                    )
                    for _ in range(36):  # up to ~3 min
                        if page.is_closed():
                            break
                        await asyncio.sleep(5)
                        blobs = await page.evaluate("window.__spikeBlobs || []")
                        if any((b.get("type") or "").startswith("video") for b in blobs):
                            break
                    report["blobs"] = (
                        "page closed" if page.is_closed()
                        else await page.evaluate("window.__spikeBlobs || []")
                    )
    report["frames"] = frames
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    step("done", str(out))
    print(json.dumps(report, indent=1)[:7000])
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--image", help="PROJECT:MEDIA of an image")
    ap.add_argument("--video", help="PROJECT:MEDIA of a video")
    ap.add_argument("--export-1080p", action="store_true")
    a = ap.parse_args()
    out = default_out_path(f"spike_upscale_menu_anchors_{a.profile}")
    sys.exit(asyncio.run(_main(a.profile, a.image, a.video, a.export_1080p, out)))
