r"""Can a picker option be told apart as CHARACTER vs MEDIA before committing it? ($0)

The migrated composer's `@` picker lists character entities and media assets in ONE
list and does not rank them. Measured 2026-09-07, twice, on the same account with the
same query `@Kael`:

  * three CLI runs committed the ENTITY (`data-reference-type="entity"`, real entity_id)
  * a later spike run committed the MEDIA file `kael_ref.jpg` (`entity_id: null`)

Same name, same gesture, different anchor. **The ordering moves between runs**, so the
current gesture — type the query, press Enter, take whatever was ranked first — cannot be
made deterministic by tuning waits. It has to select the option it wants.

That is only possible if an option is distinguishable BEFORE it is committed. This probe
answers exactly that: open the picker on a query that matches both a character and a file,
and dump every option's tag, attributes, roles, ligatures and structure.

If a discriminator exists, `attach_character_entities` can click the character option
instead of pressing Enter, and "a character outranks a file of the same name" becomes a
rule the driver enforces rather than a hope about Flow's sort order.

If none exists, the honest fallback is the read-back that already guards this: commit,
check the chip's `data-reference-type`, and refuse — which is today's behaviour, and which
caught a real mis-resolution on its first live ambiguity.

Credit-free: opens the picker and reads it. **Nothing is committed** — the probe presses
Escape rather than Enter — and nothing is submitted.

    python scripts/dev/spike_migrated_picker_option_kind.py \
        --profile ffroliva --project <uuid> --query Kael
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    COMPOSER,
    MigratedComposer,
)

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402, isort: skip

#: Every option in the open picker, described structurally. Deliberately dumps ALL
#: attributes rather than guessing which one carries the kind — the point is to find a
#: discriminator, and naming it in advance would only confirm a guess.
_OPTIONS_JS = """
() => {
  const opts = [...document.querySelectorAll("button.asset-item[role='option'], .asset-item")];
  return opts.map((o, i) => ({
    index: i,
    tag: o.tagName.toLowerCase(),
    text: (o.textContent || '').trim().slice(0, 80),
    attrs: Object.fromEntries([...o.attributes].map(a => [a.name, a.value.slice(0, 160)])),
    classes: (typeof o.className === 'string' ? o.className : '').trim().split(/\\s+/),
    ligatures: [...o.querySelectorAll('mat-icon, i')].map(e => (e.textContent || '').trim()),
    // A character tile may be marked by a nested element rather than by the button
    // itself -- record the shape of the subtree, not just the button.
    child_tags: [...o.querySelectorAll('*')].map(e => e.tagName.toLowerCase()).slice(0, 14),
    img_srcs: [...o.querySelectorAll('img')].map(e => (e.getAttribute('src') || '').slice(0, 90)),
    aria: {
      label: o.getAttribute('aria-label'),
      selected: o.getAttribute('aria-selected'),
      describedby: o.getAttribute('aria-describedby'),
    },
  }));
}
"""


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--query", required=True, help="A query matching BOTH a character and a file.")
    args = ap.parse_args()

    findings: dict[str, Any] = {k: v for k, v in vars(args).items()}
    composer = MigratedComposer()

    async with build_client(resolve_profile_dir(args.profile)) as client:
        page = client._page  # noqa: SLF001 - dev instrument
        assert page is not None
        await composer.ensure_editor(page, args.project)
        await composer.clear_composer(page)

        await page.locator(COMPOSER).first.click(timeout=5000)
        # Real keystrokes: insert_text opens a picker with no query behind it.
        await page.keyboard.type("@", delay=120)
        await page.wait_for_timeout(2200)
        findings["options_before_query"] = await page.evaluate(_OPTIONS_JS)
        await page.keyboard.type(args.query, delay=100)
        await page.wait_for_timeout(2600)
        findings["options_after_query"] = await page.evaluate(_OPTIONS_JS)

        # Escape, not Enter — this probe must not commit anything.
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)
        findings["chips_after_escape"] = await composer.read_chips(page)

    out = default_out_path("spike_migrated_picker_option_kind")
    out.write_text(json.dumps(findings, indent=2), encoding="utf-8")

    opts = findings["options_after_query"]
    step("query", f"{args.query!r} -> {len(opts)} option(s)")
    for o in opts:
        step(
            f"  opt{o['index']}",
            f"{o['text']!r} classes={o['classes'][:4]} ligatures={o['ligatures']} "
            f"attrs={sorted(o['attrs'])}",
        )
    # A discriminator is any key or class that is NOT shared by every option.
    if len(opts) > 1:
        shared_attrs = set.intersection(*(set(o["attrs"]) for o in opts))
        shared_cls = set.intersection(*(set(o["classes"]) for o in opts))
        step("DISCRIMINATOR attrs", str(sorted(set().union(*(set(o["attrs"]) for o in opts)) - shared_attrs) or "NONE"))
        step("DISCRIMINATOR classes", str(sorted(set().union(*(set(o["classes"]) for o in opts)) - shared_cls) or "NONE"))
    step("chips after Escape", str(findings["chips_after_escape"]))
    step("done", f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
