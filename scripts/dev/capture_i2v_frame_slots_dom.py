# ruff: noqa: E501
"""Capture the I2V editor's frame-slot DOM and verify FRAME_SLOTS_STRUCT matches.

For issue #63: validate (without burning credits) that the structural-first
selector cascade in ``_attach_frame`` (PR #70) actually matches the I2V Start
and End frame slots on a non-English Chrome profile. The structural Tier 1
selector is ``swap_horiz`` icon-anchor + child ``div[aria-haspopup='dialog']``;
the text-tier fallback (``has-text('Start'/'End')``) is English-only.

Reuses production helpers via ``FlowApiClient`` /
``VideoGenerationMixin._wait_video_editor_ready`` /
``_switch_to_video_mode`` — no selector drift.

Zero credits: enters the editor, switches to video mode, waits for the editor
to mount, counts how many ``FRAME_SLOTS_STRUCT`` and ``FRAME_SLOT_BY_LABEL``
matches Playwright resolves, and writes a JSON evidence file + a screenshot.

Usage:
    .venv/Scripts/python.exe scripts/dev/capture_i2v_frame_slots_dom.py \\
        --profile ffroliva \\
        --out-dir tmp/i2v_frame_dom

Environment variables (override args):
    GFLOW_CLI_LOCALE      — sets the Chrome locale; default unset (en-US).
    GFLOW_CLI_PROFILE     — fallback profile name if ``--profile`` omitted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Add ``src/`` to ``sys.path`` so this script works without ``pip install -e``.
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports.ui_automation_video import (
    FRAME_SLOT_BY_LABEL,
    FRAME_SLOTS_STRUCT,
    VideoGenerationMixin,
)
from gflow_cli.paths import default_home, profile_subdir

# PR #70's original anchor — kept inline for forensic comparison. It was removed
# from production code by #63 because it matched zero elements (the `swap_horiz`
# icon uses class `material-icons`, not `google-symbols`).
SWAP_CONTAINER_PR70 = "div:has(> button:has(i.google-symbols:text-is('swap_horiz')))"


async def capture(profile_name: str, out_dir: Path) -> int:
    profile_dir = profile_subdir(default_home(), profile_name)
    if not profile_dir.exists():
        sys.exit(f"Profile dir does not exist: {profile_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    locale = os.environ.get("GFLOW_CLI_LOCALE", "<unset>")
    print(f"Profile: {profile_name}  ({profile_dir})")
    print(f"GFLOW_CLI_LOCALE: {locale}")
    print(f"Out dir: {out_dir}")

    async with FlowApiClient(profile_dir=profile_dir, headless=False) as client:
        transport = client.transport
        if transport is None:
            sys.exit("FlowApiClient.transport is None — transport wiring broken")
        page = await client._checkout_page()
        print("FlowApiClient ready, page checked out.")

        print("Entering editor...")
        await transport._enter_editor(page, None)  # type: ignore[attr-defined]
        print("Dismissing blocking overlays...")
        await transport._dismiss_blocking_overlays(page, None)  # type: ignore[attr-defined]
        print("Switching to video mode...")
        await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)
        print("Waiting for video editor SPA to mount...")
        await VideoGenerationMixin._wait_video_editor_ready(page)

        # Frame slots only appear AFTER the "frames" sub-mode is selected in the
        # settings panel — that's what `generate_video` does for I2V.  We mirror
        # the production sequence here to give the probe a real I2V editor.
        print("Switching to I2V 'frames' sub-mode (settings panel)...")
        await VideoGenerationMixin._switch_video_sub_mode(page, "frames", out_dir=None)
        print("Closing settings panel (Escape) — slots live in the main editor...")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(600)

        # Probe the selector tiers. `SWAP_CONTAINER_PR70` is the broken PR #70
        # anchor — kept for forensic comparison (must match 0 on real Flow DOMs).
        # `FRAME_SLOTS_STRUCT` is the post-#63 production primary; the text
        # fallback only fires on EN profiles.
        swap_pr70_count = await page.locator(SWAP_CONTAINER_PR70).count()
        struct_count = await page.locator(FRAME_SLOTS_STRUCT).count()
        text_start = await page.locator(FRAME_SLOT_BY_LABEL.format(label="Start")).count()
        text_end = await page.locator(FRAME_SLOT_BY_LABEL.format(label="End")).count()
        print()
        print("=== Selector cascade match counts ===")
        print(f"  SWAP_CONTAINER_PR70 (historical, broken)     → {swap_pr70_count}")
        print(f"  FRAME_SLOTS_STRUCT  (post-#63 primary)        → {struct_count}")
        print(f"  FRAME_SLOT_BY_LABEL (Start text, EN-only)     → {text_start}")
        print(f"  FRAME_SLOT_BY_LABEL (End text, EN-only)       → {text_end}")
        print()

        # If Tier 1 found at least 2 dialog-divs inside the swap_horiz
        # container we have evidence #63 is closed on this locale.
        verdict = "PASS" if struct_count >= 2 else "FAIL"
        print(f"Tier 1 (structural) verdict for issue #63 on locale {locale!r}: {verdict}")

        # Dump the matched element outerHTML for forensic value.
        struct_html: list[str] = []
        if struct_count > 0:
            struct_html = await page.locator(FRAME_SLOTS_STRUCT).evaluate_all(
                "els => els.map(el => el.outerHTML)"
            )

        # Forensic dump: when the cascade misses, the slots may still exist with
        # a different anchor (different icon ligature or different DOM shape).
        # Pull every aria-haspopup='dialog' div + every google-symbols ligature
        # near the prompt-textbox area so we can discover the real anchor.
        # Filter to elements inside the bottom prompt-bar region (last 500px of
        # the viewport height) to avoid 100s of unrelated matches.
        forensics = await page.evaluate(
            """() => {
                const vh = window.innerHeight;
                const inPromptArea = (el) => {
                    const r = el.getBoundingClientRect();
                    return r.top > vh * 0.55 && r.top < vh && r.left < window.innerWidth;
                };
                const haspopups = [...document.querySelectorAll("div[aria-haspopup='dialog']")]
                    .filter(inPromptArea)
                    .map(el => ({
                        outerHTML: el.outerHTML.slice(0, 600),
                        text: (el.innerText || '').trim().slice(0, 100),
                        rect: (() => { const r = el.getBoundingClientRect(); return {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)}; })(),
                    }));
                const ligatures = [...document.querySelectorAll('i.google-symbols, i.material-symbols-outlined, i.material-icons')]
                    .filter(inPromptArea)
                    .map(el => ({
                        text: (el.innerText || '').trim(),
                        outerHTML: el.outerHTML.slice(0, 300),
                        parent: el.parentElement ? el.parentElement.tagName + (el.parentElement.id ? '#' + el.parentElement.id : '') : null,
                    }));
                return {haspopups, ligatures};
            }"""
        )

        print("\n=== Forensic DOM dump (bottom 45% of viewport) ===")
        print(f"  div[aria-haspopup='dialog'] near prompt: {len(forensics['haspopups'])}")
        for h in forensics["haspopups"]:
            print(f"    text={h['text']!r}  rect={h['rect']}")
        print(
            f"  google-symbols / material-symbols ligatures near prompt: {len(forensics['ligatures'])}"
        )
        for lig in forensics["ligatures"]:
            print(f"    ligature={lig['text']!r}  parent={lig['parent']}")

        evidence = {
            "profile_name": profile_name,
            "locale": locale,
            "swap_container_pr70_count": swap_pr70_count,
            "frame_slots_struct_count": struct_count,
            "frame_slot_by_label_start_count": text_start,
            "frame_slot_by_label_end_count": text_end,
            "verdict": verdict,
            "frame_slots_struct_outer_html": struct_html,
            "forensics_haspopup_dialog": forensics["haspopups"],
            "forensics_ligatures": forensics["ligatures"],
        }
        (out_dir / "frame_slots_evidence.json").write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        await page.screenshot(path=str(out_dir / "video_editor.png"), full_page=True)
        print(f"Evidence: {out_dir / 'frame_slots_evidence.json'}")
        print(f"Screenshot: {out_dir / 'video_editor.png'}")
        return 0 if verdict == "PASS" else 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        default=os.environ.get("GFLOW_CLI_PROFILE", "ffroliva"),
        help="Chromium profile name (default: env GFLOW_CLI_PROFILE or 'ffroliva')",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("tmp/i2v_frame_dom"),
        help="Where to write the JSON evidence + screenshot",
    )
    args = parser.parse_args()
    code = asyncio.run(capture(args.profile, args.out_dir))
    sys.exit(code)


if __name__ == "__main__":
    main()
