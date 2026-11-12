"""Does flow.google.com's project listing carry the caption the ``@`` picker searches? ($0)

#546 heals a stale catalog caption from the project listing. On flow.google.com the labs
listing (``flow.projectInitialData``) is retired ("Flow RPCs have been deprecated and
disabled"), so the resolver raises and a renamed image fails with exit 32. The new app
loads its own listing on ``Zzl0ze`` (docs/FLOW_WIRE_RPCS.md). This asks whether a field
of a ``Zzl0ze`` image record is the caption the picker finds the image by.

Method: open the project editor, keep the ``Zzl0ze`` frames the page loads, pick one image
record, and for each short text field in it type ``@<field>`` into the composer and check
whether the picker offers THAT image's tile (matched by its ``/asb/<token>``, never by a
label). Every probe reloads the editor first. Nothing is submitted, so it costs nothing.

Pre-registered readings (written before the first run):

* exactly one field makes the picker offer the tile -> that index is the searchable
  caption; port the listing read to ``Zzl0ze`` and read it there.
* several fields do -> the shortest stable one; record all of them.
* no field does -> ``Zzl0ze`` does not carry the caption; climb to the HAR rung for the
  picker's own search (``UpteDb``) or another read. Settles nothing about the port.
* no image record in the frames -> the listing shape changed, or the project holds no image.

    uv run python scripts/dev/spike_migrated_listing_captions.py <profile> <project-id> [<media-id>]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gflow_cli.api.image import ImageRef  # noqa: E402
from gflow_cli.api.transports.batchexecute import _is_record, parse_frames  # noqa: E402
from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    _GRID_TOKEN_JS,
    _OPTION_TITLES_JS,
    _OPTION_TOKENS_JS,
    LISTING_RPC,
    PICKER_OPTION,
    MigratedComposer,
)
from gflow_cli.errors import ReferenceNotFoundError  # noqa: E402

from _spike_common import build_client, default_out_path, resolve_profile_dir  # noqa: E402, isort: skip


def _records(node: Any) -> list[list[Any]]:
    if not isinstance(node, list):
        return []
    if _is_record(node):
        return [node]
    return [rec for child in node for rec in _records(child)]


def _texts(node: Any, path: str = "") -> list[tuple[str, str]]:
    """(index path, value) for every short human-ish string: no URLs, ids or tokens."""
    if isinstance(node, str):
        s = node.strip()
        looks_id = len(s) >= 32 and " " not in s
        if 2 <= len(s) <= 120 and "://" not in s and not looks_id:
            return [(path, s)]
        return []
    if isinstance(node, list):
        return [hit for i, child in enumerate(node) for hit in _texts(child, f"{path}[{i}]")]
    return []


_TILE_ATTRS_JS = """(id) => {
  const out = [];
  let el = document.querySelector(`img[data-media-id="${id}"]`);
  for (let hop = 0; el && hop < 5; hop++, el = el.parentElement) {
    const attrs = {};
    for (const a of el.attributes) {
      const skip = /^(src|srcset|href|style|class|_ngcontent.*|_nghost.*)$/;
      if (!skip.test(a.name)) attrs[a.name] = a.value;
    }
    out.push({hop, tag: el.tagName.toLowerCase(), attrs});
  }
  return out;
}"""


def _media_id(rec: list[Any]) -> str:
    return str(rec[0][0]) if rec and isinstance(rec[0], list) and rec[0] else str(rec[0])


async def _main(profile: str, project_id: str, media_id: str | None, control: str | None) -> int:
    frames: list[Any] = []

    async def on_response(response: Any) -> None:
        if LISTING_RPC not in str(getattr(response, "url", "")):
            return
        try:
            text = await response.text()
        except Exception:  # noqa: BLE001 - an aborted body is not our frame
            return
        frames.extend(payload for rid, payload in parse_frames(text) if rid == LISTING_RPC)

    out: dict[str, Any] = {"project_id": project_id, "probes": []}
    composer = MigratedComposer()
    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 - dev instrument
        assert page is not None
        page.on("response", on_response)
        await composer.ensure_editor(page, project_id)
        await page.wait_for_timeout(4000)
        if media_id:
            # Rung 2b: does the grid tile itself carry the caption? Attributes only, on the
            # tile and up to 4 ancestors; compared against the control below.
            out["tile_attrs"] = await page.evaluate(_TILE_ATTRS_JS, media_id)
            for hop in out["tile_attrs"]:
                match = [k for k, v in hop["attrs"].items() if control and v.strip() == control]
                print(f"[spike] tile hop {hop['hop']} <{hop['tag']}> attrs={sorted(hop['attrs'])}"
                      f" equals-control={match}", file=sys.stderr, flush=True)  # fmt: skip
        if media_id:
            # Rung 2c: with nothing typed, does the `@` picker list this image, titled with its
            # caption? Matched by the grid tile's /asb/ token, never by a label.
            token = await page.evaluate(_GRID_TOKEN_JS, media_id)
            await composer._caret_to_end(page)  # noqa: SLF001
            await page.keyboard.type("@", delay=120)
            await page.wait_for_timeout(3000)
            tokens = await page.evaluate(_OPTION_TOKENS_JS, PICKER_OPTION)
            titles = await page.evaluate(_OPTION_TITLES_JS, PICKER_OPTION)
            listed = token in tokens
            title = titles[tokens.index(token)] if listed else None
            matches = bool(control) and title == control
            out["empty_query"] = {
                "options": len(tokens),
                "listed": listed,
                "title_matches": matches,
            }
            print(f"[spike] empty @ query: {len(tokens)} options; target listed={listed}; "
                  f"title equals control={matches}",
                  file=sys.stderr, flush=True)  # fmt: skip
            await page.keyboard.press("Escape")
        images = [r for f in frames for r in _records(f) if len(r) == 7]  # 7 = image (2026-09-08)
        out["image_records"] = len(images)
        if not images:
            print("[spike] VERDICT  no image record in Zzl0ze", file=sys.stderr)
        else:
            rec = next((r for r in images if _media_id(r) == media_id), images[0])
            target = _media_id(rec)
            out["media_id"] = target
            out["fields"] = _texts(rec)
            # The control: a caption gflow recorded must find the tile through this same probe,
            # or a negative reading says only that the probe is broken.
            probes = [*out["fields"], *([("control", control)] if control else [])]
            for path, value in probes:
                await page.reload(wait_until="domcontentloaded", timeout=45_000)
                await composer.ensure_editor(page, project_id)
                ref = ImageRef(name=target, display_name=value, in_project=True)
                try:
                    tokens = await composer.await_existing_references(page, (ref,))
                    await composer._mention_by_token(  # noqa: SLF001
                        page, value, tokens[target], target, expect_chips=1
                    )
                    found = True
                except ReferenceNotFoundError:
                    found = False
                out["probes"].append({"path": path, "len": len(value), "picker_offers_tile": found})
                print(f"[spike] {path:<14} len={len(value):<3} picker finds tile: {found}",
                      file=sys.stderr, flush=True)  # fmt: skip
            hits = [p["path"] for p in out["probes"] if p["picker_offers_tile"]]
            verdict = f"caption at {hits}" if hits else "no field finds the tile"
            print(f"[spike] VERDICT  {verdict}", file=sys.stderr)
    dest = default_out_path("spike_migrated_listing_captions")
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"[spike] wrote {dest}", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile")
    parser.add_argument("project_id")
    parser.add_argument("media_id", nargs="?", default=None)
    parser.add_argument("--control-caption", default=None, help="a caption known to find the tile")
    args = parser.parse_args()
    return asyncio.run(_main(args.profile, args.project_id, args.media_id, args.control_caption))


if __name__ == "__main__":
    raise SystemExit(main())
