r"""Can the flow.google.com composer reference an image ALREADY in the project, with no upload? ($0)

Context: wiring manifest references (#913) must not re-upload an image the project already
holds (the owner's requirement: re-uploading clutters the project with duplicates). Today
the migrated composer attaches a reference only by uploading a local file and then
@-mentioning the upload's run-unique name (``attach_references``); a reference given by
media UUID is refused as unported (``_unported_image_form``). This measures whether the
same @-mention can bind an existing GENERATED image directly.

Target: a project holding two generations of the SAME prompt, so name ambiguity is in play.
No submit: nothing is generated, nothing is spent.

Records, per step: the picker options offered (text + every data-* attribute + any media
UUID in the markup), the chip that lands (text, data-entity-id, data-reference-type), and
every request the page makes while mentioning (to see whether anything uploads).

Pre-registered reading (written before the first run):

  Q1 picker offers the generated images at all
      yes -> an existing image is mentionable; no -> only uploads are; wiring must upload.
  Q2 the two same-prompt generations are distinguishable in the picker
      (distinct text, or a per-option media UUID) -> selection can target one exactly.
  Q3 chip data-entity-id equals one of the two known media UUIDs
      -> identity is verifiable by UUID after the fact (no name trust needed).
  Q4 no upload request (no file-chooser, no upload rpc) during the mention
      -> referencing existing media adds nothing to the project.
  All four yes -> "reference existing media, never re-upload" is buildable on this host.
  Q1 no, or Q3 unreadable -> unmeasured/blocked; the design must fall back to upload+dedupe.

    python scripts/dev/spike_mention_existing_media.py --profile ci-probe \
        --project <uuid> --query "<prompt prefix>" --media <uuid> <uuid>
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

from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    COMPOSER,
    PICKER_OPTION,
    MigratedComposer,
)

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402, isort: skip

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

_OPTIONS_JS = r"""(sel) => [...document.querySelectorAll(sel)].map(o => ({
  text: (o.textContent || '').trim().slice(0, 80),
  attrs: Object.fromEntries([...o.attributes].map(a => [a.name, a.value.slice(0, 120)])),
  html: o.outerHTML.slice(0, 1500),
}))"""


def _uuids(text: str) -> list[str]:
    return sorted(set(_UUID.findall(text)))


async def _open_picker(page: Any, query: str) -> list[dict[str, Any]]:
    await page.locator(COMPOSER).first.click(timeout=5000)
    await page.keyboard.type("@", delay=120)
    await page.wait_for_timeout(2200)
    if query:
        await page.keyboard.type(query, delay=100)
        await page.wait_for_timeout(2500)
    opts = await page.evaluate(_OPTIONS_JS, PICKER_OPTION)
    for o in opts:
        o["uuids"] = _uuids(o.pop("html"))
    return opts


async def main(profile: str, project: str, query: str, media: list[str]) -> int:
    findings: dict[str, Any] = {"project": project, "query": query, "known_media": media}
    out = default_out_path("mention_existing_media")

    def save() -> None:  # after every step: run 1 crashed mid-way and kept nothing
        out.write_text(json.dumps(findings, indent=2), encoding="utf-8")

    composer = MigratedComposer()
    async with build_client(resolve_profile_dir(profile)) as client:
        ctx = client._context  # noqa: SLF001 - spike reads the live context
        assert ctx is not None
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        requests: list[str] = []
        page.on("request", lambda r: requests.append(f"{r.method} {r.url[:160]}"))

        await composer.ensure_editor(page, project)
        step("editor", page.url)

        # Q1: everything the bare picker offers.
        findings["bare_picker"] = await _open_picker(page, "")
        step("Q1", json.dumps([(o["text"], o["uuids"]) for o in findings["bare_picker"]]))
        save()
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)
        findings["query_picker"] = await _open_picker(page, query)
        step("query", f"{query!r} offered {len(findings['query_picker'])} option(s)")
        save()
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)
        await composer.clear_composer(page)

        # Q2-Q4: run 1 showed the prompt text matches no option, so pick by position
        # from the bare picker (options 1 and 2) and let the chip say what was bound.
        findings["picks"] = []
        for pick in (0, 1):
            await composer.clear_composer(page)
            requests.clear()
            opts = await _open_picker(page, "")
            for _ in range(pick):
                await page.keyboard.press("ArrowDown")
                await page.wait_for_timeout(400)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(2500)
            chips = await composer.read_chips(page)
            rec = {
                "pick": pick,
                "options": opts,
                "chips": chips,
                "chip_matches_known": [c.get("entity_id") in media for c in chips],
                "requests_during_mention": [
                    r for r in requests if "flow.google.com" in r or "googleapis" in r
                ][:40],
            }
            findings["picks"].append(rec)
            save()
            step(f"pick{pick}", json.dumps({"options": len(opts), "chips": chips,
                                            "matches": rec["chip_matches_known"]}))
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(800)
        await composer.clear_composer(page)  # leave the composer empty; nothing submitted

    save()
    step("wrote", str(out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="ci-probe")
    ap.add_argument("--project", required=True)
    ap.add_argument("--query", required=True)
    ap.add_argument("--media", nargs="+", required=True)
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.profile, a.project, a.query, a.media)))
