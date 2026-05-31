# ruff: noqa: E501
"""Probe v2 for issue #125: post-bind state + submit-button enumeration.

Probe v1 (``capture_i2v_upload_dialog_dom.py``) refuted the selector hypothesis:
``production add_btn`` resolves correctly to "Incluir no comando" (pt-BR
equivalent of "Add to Prompt") at the bottom of the upload dialog. The
selector cascade is fine.

This v2 probe answers the next-layer questions, still zero credits:

1. **Does ``FRAME_SLOTS_STRUCT.count()`` actually drop after a committed bind?**
   The Stage A "post-attach DOM verification" guard depends on count dropping
   2 -> 1 -> 0 as Start and End commit. If Flow keeps the slot's
   ``div[type='button'][aria-haspopup='dialog']`` element in the DOM even after
   the image binds (re-using the click target to swap), then ``count()`` is not
   a reliable bind signal and the proposed guard would be a no-op.

2. **Is the submit-button selector ambiguous?** The production submit ("via
   ``button:has(i.google-symbols:text('arrow_forward'))``" — logged as
   ``ui_automation.prompt_submitted``) may match more than one button in the
   editor. With both frames bound, enumerate every ``arrow_forward`` candidate
   on the page, with bounding box and parent classes, so we can see whether
   a wrong-button click is the routing bug.

Zero credits: enters the editor, switches to video + I2V frames, attaches
the probe image to BOTH slots through the production ``_attach_frame`` /
``_upload_via_open_dialog`` helpers, measures slot count after each commit,
enumerates submit candidates, and quits BEFORE clicking Generate.

Usage:
    .venv/Scripts/python.exe scripts/dev/capture_i2v_post_bind_state.py \\
        --profile promo-denon82 \\
        --probe-image path/to/9_16.jpg \\
        --out-dir tmp/i2v_post_bind_state
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports.ui_automation_video import (
    FRAME_SLOTS_STRUCT,
    VideoGenerationMixin,
)
from gflow_cli.paths import default_home, profile_subdir

# The production `_send_prompt` clicks the FIRST button matching this selector
# (logged in `ui_automation.prompt_submitted` as `via:`). Enumerating ALL matches
# reveals whether the editor has multiple candidates (a top-level submit + an
# in-panel submit + a Send-to-Agent submit, etc.).
SUBMIT_CANDIDATE_SELECTOR = "button:has(i.google-symbols:text('arrow_forward'))"

# `_send_prompt` types into `page.locator(PROMPT_INPUT_SELECTORS[0]).first`.
# If Flow renders >1 textbox match, the first DOM hit may be the WRONG composer.
PROMPT_INPUT_SELECTOR = 'div[role="textbox"][data-slate-editor="true"]'


async def _enumerate_arrow_forward(page, out: dict) -> None:
    """List every visible arrow_forward button in the editor, with bbox +
    parent class prefix (helps disambiguate which panel each one lives in)."""
    btns = page.locator(SUBMIT_CANDIDATE_SELECTOR)
    n = await btns.count()
    out["arrow_forward_count"] = n
    out["arrow_forward_buttons"] = []
    for i in range(n):
        b = btns.nth(i)
        try:
            visible = await b.is_visible()
        except Exception:
            visible = False
        text = (await b.inner_text()).strip()[:80]
        aria_label = (await b.get_attribute("aria-label") or "")[:80]
        cls = (await b.get_attribute("class") or "")[:80]
        disabled = await b.is_disabled()
        bbox = await b.bounding_box()
        # Walk up 2 parents for context (class prefixes of the panel each lives in).
        parent_classes: list[str] = []
        try:
            current = b
            for _ in range(3):
                parent = current.locator("xpath=..")
                pc = (await parent.get_attribute("class") or "")[:80]
                parent_classes.append(pc)
                current = parent
        except Exception:
            pass
        out["arrow_forward_buttons"].append(
            {
                "idx": i,
                "visible": visible,
                "disabled": disabled,
                "text": text,
                "aria_label": aria_label,
                "class_prefix": cls,
                "bbox_y": int(bbox["y"]) if bbox else None,
                "bbox_x": int(bbox["x"]) if bbox else None,
                "parent_classes": parent_classes,
            }
        )


async def capture(profile_name: str, probe_image: Path, out_dir: Path) -> int:
    profile_dir = profile_subdir(default_home(), profile_name)
    if not profile_dir.exists():
        sys.exit(f"Profile dir does not exist: {profile_dir}")
    if not probe_image.exists():
        sys.exit(f"Probe image not found: {probe_image}")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Profile: {profile_name}  ({profile_dir})")
    print(f"Probe image: {probe_image}")
    print(f"Out dir: {out_dir}")

    evidence: dict = {"profile": profile_name, "probe_image": str(probe_image)}

    async with FlowApiClient(profile_dir=profile_dir, headless=False) as client:
        transport = client.transport
        if transport is None:
            sys.exit("FlowApiClient.transport is None")
        page = await client._checkout_page()
        print("Editor ready.")

        await transport._enter_editor(page, None)  # type: ignore[attr-defined]
        await transport._dismiss_blocking_overlays(page, None)  # type: ignore[attr-defined]
        await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)
        await VideoGenerationMixin._wait_video_editor_ready(page)
        await VideoGenerationMixin._switch_video_sub_mode(page, "frames", out_dir=None)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(600)

        # T0 — empty editor, before any slot interaction.
        evidence["t0_slot_count"] = await page.locator(FRAME_SLOTS_STRUCT).count()
        await page.screenshot(path=str(out_dir / "01-t0-empty.png"))
        print(f"t0 slot count (expect 2): {evidence['t0_slot_count']}")

        # T1 — bind START via the PRODUCTION code path (full attach incl. commit).
        print("Attaching Start frame via production _attach_frame...")
        await VideoGenerationMixin._attach_frame(
            page,
            0,
            "Start",
            probe_image,
            out_dir=out_dir,
        )
        await page.wait_for_timeout(500)
        evidence["t1_slot_count_after_start_bind"] = await page.locator(FRAME_SLOTS_STRUCT).count()
        await page.screenshot(path=str(out_dir / "02-t1-after-start.png"))
        print(
            f"t1 slot count after Start bind (expect 1): {evidence['t1_slot_count_after_start_bind']}"
        )

        # T2 — bind END via the PRODUCTION code path.
        print("Attaching End frame via production _attach_frame...")
        await VideoGenerationMixin._attach_frame(
            page,
            1,
            "End",
            probe_image,
            out_dir=out_dir,
        )
        await page.wait_for_timeout(500)
        evidence["t2_slot_count_after_end_bind"] = await page.locator(FRAME_SLOTS_STRUCT).count()
        await page.screenshot(path=str(out_dir / "03-t2-after-end.png"))
        print(
            f"t2 slot count after End bind  (expect 0): {evidence['t2_slot_count_after_end_bind']}"
        )

        # T3 — both frames bound, prompt textbox in focus. Enumerate every
        # arrow_forward submit candidate. Production picks .first.
        print("Enumerating arrow_forward submit candidates...")
        slot_state: dict = {}
        await _enumerate_arrow_forward(page, slot_state)
        evidence["t3_submit_candidates"] = slot_state
        await page.screenshot(path=str(out_dir / "04-t3-submit-candidates.png"))
        print(f"arrow_forward buttons found in editor: {slot_state['arrow_forward_count']}")
        for b in slot_state["arrow_forward_buttons"]:
            vis = "visible" if b["visible"] else "hidden"
            dis = " disabled" if b["disabled"] else ""
            print(
                f"  idx={b['idx']} {vis}{dis} bbox=({b['bbox_x']},{b['bbox_y']}) text={b['text']!r}"
            )

        # T4 — enumerate all prompt textboxes (production picks .first).
        print("Enumerating Slate textbox candidates...")
        boxes = page.locator(PROMPT_INPUT_SELECTOR)
        nbox = await boxes.count()
        textboxes = []
        for i in range(nbox):
            b = boxes.nth(i)
            try:
                visible = await b.is_visible()
            except Exception:
                visible = False
            cls = (await b.get_attribute("class") or "")[:80]
            bbox = await b.bounding_box()
            parent_classes: list[str] = []
            try:
                cur = b
                for _ in range(3):
                    parent = cur.locator("xpath=..")
                    parent_classes.append((await parent.get_attribute("class") or "")[:80])
                    cur = parent
            except Exception:
                pass
            textboxes.append(
                {
                    "idx": i,
                    "visible": visible,
                    "class_prefix": cls,
                    "bbox_y": int(bbox["y"]) if bbox else None,
                    "bbox_x": int(bbox["x"]) if bbox else None,
                    "parent_classes": parent_classes,
                }
            )
        evidence["t4_textbox_count"] = nbox
        evidence["t4_textboxes"] = textboxes
        print(f"prompt textboxes: {nbox}")
        for b in textboxes:
            vis = "visible" if b["visible"] else "hidden"
            print(
                f"  idx={b['idx']} {vis} bbox=({b['bbox_x']},{b['bbox_y']}) class={b['class_prefix']!r}"
            )

        # T5 — type a non-paid prompt via production helper sequence, then
        # measure slot state. Stop BEFORE the submit click — no Generate.
        print("Typing test prompt into .first textbox (production sequence, no submit)...")
        first_box = boxes.first
        await first_box.click()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")
        await page.keyboard.insert_text("probe test prompt — do not submit")
        await page.wait_for_timeout(500)
        evidence["t5_slot_count_after_typing"] = await page.locator(FRAME_SLOTS_STRUCT).count()
        post_typing: dict = {}
        await _enumerate_arrow_forward(page, post_typing)
        evidence["t5_submit_candidates_after_typing"] = post_typing
        await page.screenshot(path=str(out_dir / "05-t5-after-typing.png"))
        print(f"t5 slot count after typing (expect 0): {evidence['t5_slot_count_after_typing']}")
        print(f"t5 arrow_forward count: {post_typing['arrow_forward_count']}")
        for b in post_typing["arrow_forward_buttons"]:
            vis = "visible" if b["visible"] else "hidden"
            dis = " DISABLED" if b["disabled"] else " enabled"
            print(
                f"  idx={b['idx']} {vis}{dis} bbox=({b['bbox_x']},{b['bbox_y']}) text={b['text']!r}"
            )

        # Write evidence + summary.
        (out_dir / "evidence.json").write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print("\n=== summary ===")
        print(
            f"FRAME_SLOTS_STRUCT count transition: "
            f"{evidence['t0_slot_count']} -> {evidence['t1_slot_count_after_start_bind']} -> "
            f"{evidence['t2_slot_count_after_end_bind']}"
        )
        print(
            f"arrow_forward submit candidates: {evidence['t3_submit_candidates']['arrow_forward_count']}"
        )
        print(f"\nDOM evidence: {out_dir / 'evidence.json'}")
        print("Closing browser (no Generate clicked — zero credits).")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--probe-image",
        type=Path,
        required=True,
        help="9:16 image attached to both Start and End slots via the production helpers.",
    )
    parser.add_argument("--out-dir", type=Path, default=Path("tmp/i2v_post_bind_state"))
    args = parser.parse_args()
    return asyncio.run(capture(args.profile, args.probe_image, args.out_dir))


if __name__ == "__main__":
    sys.exit(main())
