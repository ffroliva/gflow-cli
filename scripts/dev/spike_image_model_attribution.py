r"""Is a migrated image's model recorded ANYWHERE reachable? ($0)

The 2026-09-11 Lite spike reported the binding as unverifiable after searching two
surfaces. "Unverifiable by the routes tested" is not "unknowable", and three things say
the search was too narrow:

1. The labs trpc `initialdata` capture (in `_spike_out/`, 2026-08-31)
   shows Flow DOES store a model per media -- `mediaMetadata.requestData
   .videoGenerationRequestData.videoModelControlInput.videoModelName` -- for video. An
   image equivalent plausibly exists and was simply never requested.
2. The previous probe FILTERED: it only recorded replies already containing a media id or
   a model token, and only during the first 12 s of a project load. A reply carrying
   attribution under a different key, or fetched on interaction, was dropped unseen.
3. If Flow's own UI shows which model produced an image, the data is on the page.

So this probe removes the filter and adds the interaction:

- **every** `batchexecute` reply on load, by rpcid, with per-token and per-media-id
  co-occurrence -- nothing dropped
- then the DOM: does any visible text name a model tier while a generated image is open

## Pre-registered readings

| Outcome | Reading |
|---|---|
| an rpcid carries a media id AND a model token | reachable; decode it, stop echoing |
| the DOM names a tier next to an image | reachable via the page, if not via a reply |
| all rpcids and the DOM are silent | genuinely absent here, not merely unread |
| the media tile cannot be opened | interaction arm unmeasured; load arm stands |

## Cost

Zero. Navigation, DOM reads, and at most one click on an existing thumbnail. No
generation, nothing created or deleted.

    python scripts/dev/spike_image_model_attribution.py \
        --profile ci-probe --project <id> --media <uuid> [--media <uuid>]
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

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

_MODEL_TOKENS = (
    "HARBOR_SEAL",
    "NARWHAL",
    "GEM_PIX_2",
    "IMAGEN_3_5",
    "Nano Banana 2 Lite",
    "Nano Banana Pro",
    "Nano Banana 2",
)

#: Keys the labs shape uses for the same idea, searched for by NAME so a rename is
#: visible as a miss rather than as an absence.
_ATTRIBUTION_KEYS = (
    "imageModelName",
    "modelNameType",
    "imageModelControlInput",
    "imageGenerationRequestData",
    "videoModelName",
    "requestData",
    "mediaMetadata",
)

#: Read visible text only, and only short runs, so a prompt or a signed URL cannot ride
#: along. A tier label is at most a few words.
_DOM_TIER_JS = r"""
(tokens) => {
  const out = [];
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walk.nextNode())) {
    const t = (n.textContent || '').trim();
    if (!t || t.length > 60) continue;
    if (tokens.some((tok) => t.includes(tok))) {
      const el = n.parentElement;
      out.push({
        text: t,
        tag: el ? el.tagName.toLowerCase() : null,
        cls: el ? [...el.classList].slice(0, 3) : [],
      });
    }
  }
  return out.slice(0, 25);
}
"""


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--media", action="append", default=[])
    args = ap.parse_args()

    replies: list[dict[str, Any]] = []

    async with build_client(resolve_profile_dir(args.profile)) as client:
        page = client.transport._page  # noqa: SLF001

        def on_response(resp: Any) -> None:
            url = str(getattr(resp, "url", ""))
            if "batchexecute" not in url:
                return

            async def _read() -> None:
                try:
                    text = await resp.text()
                except Exception:  # noqa: BLE001
                    return
                rpcids = re.findall(r"rpcids=([A-Za-z0-9,]+)", url)
                replies.append(
                    {
                        "rpcid": rpcids[0] if rpcids else "?",
                        "len": len(text),
                        "model_tokens": sorted({t for t in _MODEL_TOKENS if t in text}),
                        "attribution_keys": sorted({k for k in _ATTRIBUTION_KEYS if k in text}),
                        "media_ids": sorted({m for m in args.media if m in text}),
                    }
                )

            asyncio.ensure_future(_read())  # noqa: RUF006

        page.on("response", on_response)
        step("load", f"opening project {args.project}")
        await page.goto(
            f"https://flow.google.com/project/{args.project}",
            wait_until="domcontentloaded",
            timeout=45_000,
        )
        await asyncio.sleep(14.0)
        load_replies = len(replies)

        dom_on_load = await page.evaluate(_DOM_TIER_JS, list(_MODEL_TOKENS))
        step("load", f"{load_replies} batchexecute replies; DOM tier hits: {len(dom_on_load)}")

        # Interaction arm, run PER MEDIA -- this is the control that matters. A tier
        # label appearing once proves nothing: `model-select-trigger-content` is the
        # composer's own picker, which would read the same whatever is open. Only a
        # label that CHANGES with the opened media is attribution.
        # Thumbnail `src` does not carry the media id, so target BY INDEX and ask the one
        # question that separates the two readings: does the tier label CHANGE between
        # two different thumbnails? Tracking the opened media means attribution; holding
        # still means it is the composer's own picker and says nothing about the image.
        tiles = page.locator("img[src*='flow-content'], img[src*='googleusercontent']")
        tile_count = await tiles.count()
        per_media: list[dict[str, Any]] = []
        for index in range(min(tile_count, 3)):
            entry: dict[str, Any] = {"tile_index": index}
            try:
                await tiles.nth(index).click(timeout=5000)
                await asyncio.sleep(5.0)
                entry["opened"] = True
                entry["dom_tier_hits"] = await page.evaluate(_DOM_TIER_JS, list(_MODEL_TOKENS))
            except Exception as exc:  # noqa: BLE001 - a failed arm is a datum
                entry["opened"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            step("open", f"tile {index} -> {entry.get('dom_tier_hits', entry['opened'])}")
            per_media.append(entry)

        opened = [e.get("opened") for e in per_media]
        dom_after_open = per_media
        page.remove_listener("response", on_response)

    result = {
        "question": "is a migrated image's model recorded anywhere reachable? (#789)",
        "cost": "credit-free: navigation, DOM reads, one thumbnail click",
        "media_watched": args.media,
        "replies": replies,
        "replies_on_load": load_replies,
        "thumbnail_opened": opened,
        "dom_tier_hits_on_load": dom_on_load,
        "dom_tier_hits_after_open": dom_after_open,
        "joins_media_and_model": [r for r in replies if r["media_ids"] and r["model_tokens"]],
    }
    out = default_out_path("spike_image_model_attribution", ".json")
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    step("done", f"wrote {out}")
    print(
        json.dumps(
            {
                "rpcids": [
                    f"{r['rpcid']} len={r['len']} tokens={r['model_tokens']} "
                    f"keys={r['attribution_keys']} media={len(r['media_ids'])}"
                    for r in replies
                ],
                "joins": result["joins_media_and_model"],
                "dom_after_open": dom_after_open,
            },
            indent=2,
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
