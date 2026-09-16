# ruff: noqa: E501
"""Measure the migrated host's End frame chip (issue #639 slice 2).

The migrated composer refuses any request carrying an end frame because, per
``_unported_form``, "the End chip is unmeasured" (2026-09-05 spike). This script
measures it: it opens a migrated project, selects the Frames sub-mode, and dumps
the chip/picker DOM plus screenshots — read-only, zero credits, no uploads, no
submit. Output tells whether an End chip exists to drive and what anchors it.

Usage (from the repo root, with the gflow venv python for Playwright):
    <gflow-venv>/Scripts/python.exe scripts/dev/spike_migrated_end_frame.py \
        --profile presentation-reels-google \
        --project dcc9bde8-57c2-4694-a123-eeffc1b7e93a \
        --out-dir scripts/dev/_spike_out/end_frame
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from gflow_cli.api.client import FlowApiClient  # noqa: E402
from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    BOUND_CHIP,
    EMPTY_CHIP,
    FRAMES_LIGATURE,
    MigratedComposer,
)
from gflow_cli.paths import default_home, profile_subdir  # noqa: E402


async def dump_chips(page, tag: str) -> dict:
    empty = page.locator(EMPTY_CHIP)
    bound = page.locator(BOUND_CHIP)
    n_empty, n_bound = await empty.count(), await bound.count()
    empties = []
    if n_empty:
        empties = await empty.evaluate_all(
            "els => els.map(el => ({html: el.outerHTML.slice(0, 800), "
            "text: (el.innerText || '').trim().slice(0, 120)}))"
        )
    print(f"[{tag}] empty chips: {n_empty}, bound chips: {n_bound}")
    for e in empties:
        print(f"[{tag}]   empty text={e['text']!r}")
    return {"empty_count": n_empty, "bound_count": n_bound, "empties": empties}


async def capture(profile_name: str, project_id: str, out_dir: Path) -> int:
    profile_dir = profile_subdir(default_home(), profile_name)
    if not profile_dir.exists():
        sys.exit(f"Profile dir does not exist: {profile_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Profile: {profile_name} ({profile_dir})")

    async with FlowApiClient(profile_dir=profile_dir, headless=False) as client:
        page = await client._checkout_page()
        url = f"https://flow.google.com/project/{project_id}"
        print(f"Goto {url} ...")
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(9000)
        print("final url:", page.url)
        dom = await page.evaluate(
            "() => ({i_total: document.querySelectorAll('i').length, "
            "mat_icons: document.querySelectorAll('mat-icon').length})"
        )
        print("DOM:", dom)
        await page.screenshot(path=str(out_dir / "1_project.png"), full_page=True)

        before = await dump_chips(page, "before-frames")
        # Production path: settings pane -> video mode -> Frames sub-mode.
        composer = MigratedComposer()
        pane = await composer._open_pane(page)
        await composer._select(page, pane, axis="mode", lig="videocam")
        await composer._select(page, pane, axis="submode", lig=FRAMES_LIGATURE)
        await composer._close_pane(page, strict=True)
        clicked = "production _select path (mode=videocam, submode=frames)"
        print("Frames sub-mode click:", clicked)
        await page.wait_for_timeout(2500)
        after = await dump_chips(page, "after-frames")
        await page.screenshot(path=str(out_dir / "2_frames_mode.png"), full_page=True)

        # Probe the LAST empty chip (End candidate): does a click open the
        # library picker (search box = PICKER_SEARCH)? When Start is already
        # bound only one empty chip remains — selecting the last one cannot
        # mis-target Start, since the attach flow requires Start before End.
        end_probe: dict = {"attempted": False}
        if after["empty_count"]:
            end_probe["attempted"] = True
            chips = page.locator(EMPTY_CHIP)
            await chips.nth(after["empty_count"] - 1).click(timeout=4000)
            await page.wait_for_timeout(2000)
            search = page.locator("flow-add-menu-popover-content input[type='text']").first
            try:
                await search.wait_for(state="visible", timeout=5000)
                end_probe["picker_opened"] = True
            except Exception:
                end_probe["picker_opened"] = False
            await page.screenshot(path=str(out_dir / "3_end_chip_click.png"), full_page=True)
            await page.keyboard.press("Escape")
        print("End-chip probe:", end_probe)

        evidence = {
            "profile_name": profile_name,
            "project_id": project_id,
            "final_url": page.url,
            "dom": dom,
            "frames_click": clicked,
            "chips_before": before,
            "chips_after": after,
            "end_probe": end_probe,
        }
        (out_dir / "end_frame_evidence.json").write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Evidence: {out_dir / 'end_frame_evidence.json'}")
        verdict = (
            "PRESENT"
            if end_probe.get("picker_opened")
            else ("CHIPS_VISIBLE" if after["empty_count"] >= 2 else "ABSENT")
        )
        print(f"END CHIP VERDICT: {verdict}")
        return 0 if verdict != "ABSENT" else 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="presentation-reels-google")
    parser.add_argument("--project", default="dcc9bde8-57c2-4694-a123-eeffc1b7e93a")
    parser.add_argument("--out-dir", default="scripts/dev/_spike_out/end_frame")
    args = parser.parse_args()
    sys.exit(asyncio.run(capture(args.profile, args.project, Path(args.out_dir))))


if __name__ == "__main__":
    main()
