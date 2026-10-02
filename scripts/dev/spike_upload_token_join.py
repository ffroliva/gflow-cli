r"""Does an UPLOADED asset get the same grid-tile <-> picker-token join as a generated one? ($0)

PLAN Task 10 (#913): local-file references should upload each distinct file once per run,
then reference it in place like `batch:N` (no duplicate uploads). That reuses the token
binder measured for generated images (option `/asb/<token>` == grid `img[data-media-id]`
token). This checks the join holds for an upload, in a project that holds one.

Pre-registered reading:
  the upload's picker option token equals a grid tile token, and that tile carries a
  data-media-id  -> uploads bind by identity exactly like generations; T10 can reuse the
                    in-place path.
  no grid tile carries the token -> uploads are not in the grid; T10 needs another binder.

    python scripts/dev/spike_upload_token_join.py --profile ci-probe --project <uuid> \
        --query "<upload display name prefix>"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    _GRID_TOKEN_JS,
    _OPTION_TOKENS_JS,
    COMPOSER,
    PICKER_OPTION,
    MigratedComposer,
)

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402, isort: skip

_ALL_TILES_JS = r"""() => [...document.querySelectorAll('img[data-media-id]')].map(e => ({
  id: e.getAttribute('data-media-id'),
  token: ((e.getAttribute('src') || '').match(/\/asb\/([A-Za-z0-9_-]+)/) || [])[1] || '',
}))"""


async def main(profile: str, project: str, query: str) -> int:
    findings: dict[str, Any] = {"project": project, "query": query}
    async with build_client(resolve_profile_dir(profile)) as client:
        ctx = client._context  # noqa: SLF001
        assert ctx is not None
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        composer = MigratedComposer()
        await composer.ensure_editor(page, project)
        await page.wait_for_timeout(3000)
        tiles = await page.evaluate(_ALL_TILES_JS)
        await page.locator(COMPOSER).first.click(timeout=5000)
        await page.keyboard.type("@", delay=120)
        await page.wait_for_timeout(2200)
        await page.keyboard.type(query, delay=100)
        await page.wait_for_timeout(3000)
        texts = [t.strip() for t in await page.locator(PICKER_OPTION).all_text_contents()]
        tokens = [str(t) for t in await page.evaluate(_OPTION_TOKENS_JS, PICKER_OPTION)]
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)
        await composer.clear_composer(page)
        by_token = {t["token"]: t["id"] for t in tiles if t["token"]}
        findings["options"] = [
            {"text": text, "token": tok[:16], "grid_media_id": by_token.get(tok)}
            for text, tok in zip(texts, tokens, strict=False)
        ]
        findings["grid_tiles"] = len(tiles)
        # Round trip: the matched tile's id resolves back to the same token.
        for opt, tok in zip(findings["options"], tokens, strict=False):
            if opt["grid_media_id"]:
                back = await page.evaluate(_GRID_TOKEN_JS, opt["grid_media_id"])
                opt["round_trip"] = back == tok
        step("join", json.dumps(findings))
    out = default_out_path("upload_token_join")
    out.write_text(json.dumps(findings, indent=2), encoding="utf-8")
    step("wrote", str(out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="ci-probe")
    ap.add_argument("--project", required=True)
    ap.add_argument("--query", required=True)
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.profile, a.project, a.query)))
