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

from playwright.async_api import Page  # noqa: E402

from gflow_cli.api.client import FlowApiClient  # noqa: E402
from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    BOUND_CHIP,
    EMPTY_CHIP,
    FRAMES_LIGATURE,
    INTERPOLATION_SUBMIT_RPC,
    MODEL_KEY,
    SUBMIT_RPCS,
    MigratedComposer,
    _body_rpcid,
    _ligature,
    _rpcid,
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
        try:
            return await _capture_on_page(client, page, profile_name, project_id, out_dir)
        finally:
            client._checkin_page(page)


async def _capture_on_page(
    client: FlowApiClient, page: Page, profile_name: str, project_id: str, out_dir: Path
) -> int:
    composer = MigratedComposer()
    # Production navigation, not a hand-built URL: `ensure_editor` is what the driver
    # itself calls, so the spike lands the same way a real run does (and waits for the
    # same READY_ANCHOR instead of a fixed sleep).
    await composer.ensure_editor(page, project_id)
    print("final url:", page.url)
    dom = await page.evaluate(
        "() => ({i_total: document.querySelectorAll('i').length, "
        "mat_icons: document.querySelectorAll('mat-icon').length})"
    )
    print("DOM:", dom)
    await page.screenshot(path=str(out_dir / "1_project.png"), full_page=True)

    before = await dump_chips(page, "before-frames")
    # Production path: settings pane -> video mode -> Frames sub-mode.
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


# ---------------------------------------------------------------------------
# Slice 3 (2026-09-17): the credit-free submit probe.
#
# The chip probe above proves an End chip EXISTS. It cannot prove what a bound
# End chip puts on the wire, and that contract -- rpc `nprQif`, model key
# `veo_3_1_interpolation_lite`, both frame ids -- is what the driver now asserts
# against before it lets Flow bill anything. Verifying it by generating costs a
# Veo credit per run and is not repeatable in review.
#
# So: attach both frames for real (uploads are free -- `maseQ`, image quota only),
# then ROUTE-ABORT the submit. Playwright hands us the request body before the
# request leaves the browser; aborting it means Google never sees the submit and
# nothing is billed. Memory: credit-free-route-abort-verification.
# ---------------------------------------------------------------------------


async def submit_probe(
    profile_name: str, project_id: str, start_png: Path, end_png: Path, out_dir: Path
) -> int:
    profile_dir = profile_subdir(default_home(), profile_name)
    if not profile_dir.exists():
        sys.exit(f"Profile dir does not exist: {profile_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    captured: list[dict] = []
    async with FlowApiClient(profile_dir=profile_dir, headless=False) as client:
        page = await client._checkout_page()
        try:
            composer = MigratedComposer()
            await composer.ensure_editor(page, project_id)

            # Frames sub-mode, then both chips -- the production attach path.
            pane = await composer._open_pane(page)
            await composer._select(page, pane, axis="mode", lig="videocam")
            await composer._select(page, pane, axis="submode", lig=FRAMES_LIGATURE)
            await composer._close_pane(page, strict=True)

            start_id = await composer.attach_start_frame(page, project_id, start_png)
            end_id = await composer.attach_end_frame(page, project_id, end_png)
            print(f"start media id: {start_id}")
            print(f"end   media id: {end_id}")

            await composer.send_prompt(page, "a slow dolly between the two frames")

            async def block_submit(route, request) -> None:
                body = request.post_data or ""
                rpcid = _body_rpcid(body) or _rpcid(request.url)
                if rpcid in SUBMIT_RPCS:
                    key = MODEL_KEY.search(body)
                    captured.append(
                        {
                            "rpcid": rpcid,
                            "rpcid_source": "f.req body"
                            if _body_rpcid(body)
                            else "url rpcids param",
                            "model_key": key.group(0) if key else None,
                            "carries_start_id": start_id in body,
                            "carries_end_id": end_id in body,
                            "body_bytes": len(body),
                        }
                    )
                    await route.abort()  # nothing reaches Google; nothing is billed
                    return
                await route.continue_()

            await page.route("**/batchexecute*", block_submit)
            submit = page.locator("button").filter(has=_ligature(page, "arrow_forward")).first
            await submit.click(timeout=15000)
            # The abort resolves fast; give the app a beat to actually issue it.
            for _ in range(40):
                if captured:
                    break
                await page.wait_for_timeout(250)
            await page.screenshot(path=str(out_dir / "4_submit_aborted.png"), full_page=True)
        finally:
            # Evidence before teardown: a raise past this point must not discard
            # what we already measured.
            (out_dir / "submit_probe_evidence.json").write_text(
                json.dumps(
                    {
                        "profile_name": profile_name,
                        "project_id": project_id,
                        "captured_submits": captured,
                        "expected": {
                            "rpcid": INTERPOLATION_SUBMIT_RPC,
                            "model_key_shape": "*_interpolation_* or *_first_last",
                        },
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            client._checkin_page(page)

    print(json.dumps(captured, indent=2))
    if not captured:
        print("VERDICT: NO SUBMIT OBSERVED (the click did not produce a batchexecute)")
        return 1
    hit = captured[0]
    ok = (
        hit["rpcid"] == INTERPOLATION_SUBMIT_RPC
        and hit["carries_start_id"]
        and hit["carries_end_id"]
        and hit["model_key"]
        and ("interpolation" in hit["model_key"] or "first_last" in hit["model_key"])
    )
    print(f"VERDICT: {'CONFIRMED' if ok else 'MISMATCH'} -- {hit}")
    return 0 if ok else 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    # Required, not defaulted: a default profile/project points a maintainer's
    # authenticated Chrome at someone else's account.
    parser.add_argument("--profile", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--out-dir", default="scripts/dev/_spike_out/end_frame")
    parser.add_argument(
        "--mode",
        choices=("chips", "submit"),
        default="chips",
        help="chips = DOM-only End-chip probe; submit = credit-free route-abort "
        "capture of the interpolation submit body (needs --start-frame/--end-frame)",
    )
    parser.add_argument("--start-frame", type=Path)
    parser.add_argument("--end-frame", type=Path)
    args = parser.parse_args()
    out = Path(args.out_dir)
    if args.mode == "submit":
        if not args.start_frame or not args.end_frame:
            parser.error("--mode submit requires --start-frame and --end-frame")
        sys.exit(
            asyncio.run(
                submit_probe(args.profile, args.project, args.start_frame, args.end_frame, out)
            )
        )
    sys.exit(asyncio.run(capture(args.profile, args.project, out)))


if __name__ == "__main__":
    main()
