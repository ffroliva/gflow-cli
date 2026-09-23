r"""Which aspect radios does flow.google.com's composer render, per mode? ($0)

Question: the image driver refuses 3:4 because a 2026-09-08 enumeration on one account
found four aspect radios (`crop_16_9`, `crop_landscape`, `crop_square`, `crop_9_16`). A
2026-09-17 screenshot shows five on the image tab (16:9, 4:3, 1:1, 3:4, 9:16). This spike
re-enumerates every radiogroup in the settings pane for the Image and the Video mode and
records each radio's ligature and checked state — nothing is submitted.

Pre-registered reading:

| Outcome                                   | Reading                                              |
|-------------------------------------------|------------------------------------------------------|
| a fifth image radio with a new ligature   | 3:4 is offered; that ligature is the anchor to add   |
| four image radios                         | this account still gets four; cohort-dependent       |
| video group differs from 16:9/9:16        | record it; video aspects need their own port         |
| pane never opens                          | unmeasured for this profile                          |

    python scripts/dev/spike_migrated_aspect_radios.py --profile flavio.oliva
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

from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    RADIOGROUP,
    MigratedComposer,
)

_GROUPS_JS = """
(pane) => [...pane.querySelectorAll("[role='radiogroup']")].map((g) => [...g.querySelectorAll("[role='radio']")].map((r) => ({
  lig: [...r.querySelectorAll('mat-icon, i.google-symbols')].map(i => i.textContent.trim()).join('|'),
  text: (r.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 24),
  checked: r.getAttribute('aria-checked'),
})))
"""


async def main() -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", default=None, help="project id; default: first on the grid")
    args = ap.parse_args()

    out: dict[str, Any] = {"profile": args.profile}
    async with build_client(resolve_profile_dir(args.profile)) as client:
        page = client._page  # noqa: SLF001 - spike reads the live page
        assert page is not None
        project = args.project
        if project is None:
            await page.goto("https://flow.google.com/", wait_until="domcontentloaded")
            link = page.locator("a[href*='/project/']").first
            await link.wait_for(state="visible", timeout=45_000)
            project = (await link.get_attribute("href") or "").rsplit("/", 1)[-1]
        composer = MigratedComposer()
        await composer.ensure_editor(page, project, timeout_s=45.0)
        # One pane for both modes: switching the mode radio re-renders the groups in
        # place, while closing and reopening raced the overlay's teardown.
        pane = await composer._open_pane(page)  # noqa: SLF001 - production path
        for mode in ("image", "videocam"):
            await composer._select(page, pane, axis="mode", lig=mode)  # noqa: SLF001
            await page.wait_for_timeout(1500)
            handle = await page.locator(".cdk-overlay-pane").filter(
                has=page.locator(RADIOGROUP)
            ).last.element_handle()
            out[mode] = await page.evaluate(_GROUPS_JS, handle)
            step(mode, f"{len(out[mode])} radiogroups ({RADIOGROUP})")
            for i, group in enumerate(out[mode]):
                step(mode, f"  group {i}: " + ", ".join(f"{r['lig']}[{r['text']}]" for r in group))
        await composer._close_pane(page, strict=False)  # noqa: SLF001

    path = default_out_path(f"spike_migrated_aspect_radios_{args.profile}")
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    step("done", f"wrote {path}")


if __name__ == "__main__":
    asyncio.run(main())
