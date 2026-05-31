# ruff: noqa: E501
"""Probe v4 for issue #125: intercept the submit XHR and inspect its body.

Probes v1-v3 confirmed:
- The "Add to Prompt" selector resolves correctly on pt-BR (idx 5 "Incluir
  no comando", bbox_y=474). NOT a selector bug.
- ``FRAME_SLOTS_STRUCT.count()`` transitions 2 -> 1 -> 0 as Start/End bind.
  Slot DOM IS a reliable bind signal.
- Only one ``arrow_forward`` submit button exists (text: "Criar"), correctly
  disabled with no prompt, enabled after typing. NOT an ambiguous-submit bug.
- Only one Slate textbox exists. NOT a wrong-textbox bug.
- Slots stay bound (count 0) after typing the prompt. Typing does NOT
  detach the bindings.

But the original paid i2v run captured a T2V request body. So the bug must
be at the actual submit-click moment — a race between React state
propagation and the click, or Flow's JS reading stale state when the
mediaId-binding hasn't propagated yet.

This v4 probe nails it down at ZERO credits via a request route handler:

1. Enter editor, switch to video + I2V frames, attach Start + End via
   the production helpers.
2. Type the prompt.
3. Install ``page.route("**/v1/video:batchAsync**", abort)`` so the
   submit XHR is BLOCKED at the browser before it leaves the network
   layer. Also install ``page.on("request", capture)`` so we still see
   the request the browser TRIED to send.
4. Click the submit button. Wait briefly.
5. Inspect the captured request: URL endpoint name + body's
   startImage / endImage / referenceImages presence.

If the captured URL is ``batchAsyncGenerateVideoText`` and image_inputs
are null, we've reproduced issue #125 with zero credit consumed (the
request never reached Veo because route.abort() killed it in-browser).

Usage:
    .venv/Scripts/python.exe scripts/dev/capture_i2v_intercept_submit.py \\
        --profile promo-denon82 \\
        --probe-image C:\\path\\to\\start.jpg \\
        --out-dir tmp/i2v_intercept
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports.ui_automation import SUBMIT_BUTTON_SELECTORS
from gflow_cli.api.transports.ui_automation_video import (
    FRAME_SLOTS_STRUCT,
    VideoGenerationMixin,
    _summarize_request_image_inputs,
)
from gflow_cli.api.video import VideoModel
from gflow_cli.paths import default_home, profile_subdir

PROMPT_INPUT_SELECTOR = 'div[role="textbox"][data-slate-editor="true"]'
ENDPOINT_GLOB = "**/v1/video:batchAsync**"


async def capture(
    profile_name: str,
    probe_image: Path,
    prompt_text: str,
    reorder: bool,
    model_alias: str | None,
    start_only: bool,
    out_dir: Path,
) -> int:
    profile_dir = profile_subdir(default_home(), profile_name)
    if not profile_dir.exists():
        sys.exit(f"Profile dir does not exist: {profile_dir}")
    if not probe_image.exists():
        sys.exit(f"Probe image not found: {probe_image}")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Profile: {profile_name}  ({profile_dir})")
    print(f"Probe image: {probe_image}")
    print(
        f"Sequence: {'PROMPT_FIRST then attaches' if reorder else 'ATTACHES first then prompt (production)'}"
    )
    print(f"Out dir: {out_dir}")

    evidence: dict[str, Any] = {
        "profile": profile_name,
        "probe_image": str(probe_image),
        "sequence": "prompt_first" if reorder else "attaches_first",
        "prompt_text_len": len(prompt_text),
    }

    async with FlowApiClient(profile_dir=profile_dir, headless=False) as client:
        transport = client.transport
        if transport is None:
            sys.exit("FlowApiClient.transport is None")
        page = await client._checkout_page()

        await transport._enter_editor(page, None)  # type: ignore[attr-defined]
        await transport._dismiss_blocking_overlays(page, None)  # type: ignore[attr-defined]
        await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)
        await VideoGenerationMixin._wait_video_editor_ready(page)
        # Select model BEFORE attaching frames — mirrors _generate_video_locked
        # order. Critical for the issue #125 model+i2v compatibility probe:
        # omni-flash silently drops refs at submit, veo-3.1-* preserves them.
        if model_alias:
            chosen = VideoModel.from_cli(model_alias)
            if chosen is None:
                sys.exit(f"Unknown CLI model alias: {model_alias!r}")
            evidence["selected_model"] = model_alias
            print(f"Selecting model {model_alias!r} ({chosen.value})...")
            await VideoGenerationMixin._select_video_model(page, chosen, out_dir=None)
        await VideoGenerationMixin._switch_video_sub_mode(page, "frames", out_dir=None)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(600)

        async def type_prompt() -> None:
            print("Typing prompt...")
            boxes = page.locator(PROMPT_INPUT_SELECTOR)
            box = boxes.first
            await box.click()
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Delete")
            await page.keyboard.insert_text(prompt_text)
            await page.wait_for_timeout(500)

        async def bind_frames() -> None:
            print("Attaching Start...")
            await VideoGenerationMixin._attach_frame(page, 0, "Start", probe_image, out_dir=out_dir)
            if start_only:
                print("Skipping End (start-only mode).")
                return
            print("Attaching End...")
            await VideoGenerationMixin._attach_frame(page, 1, "End", probe_image, out_dir=out_dir)

        # Sequence selection — this is what the probe is testing.
        if reorder:
            await type_prompt()
            await bind_frames()
        else:
            await bind_frames()
            await type_prompt()

        evidence["slot_count_at_t_pre_submit"] = await page.locator(FRAME_SLOTS_STRUCT).count()
        await page.screenshot(path=str(out_dir / "01-pre-submit.png"))

        # Install network-layer interceptor + request observer.
        captured_requests: list[dict[str, Any]] = []

        async def on_request(req: Any) -> None:
            if "batchAsync" in req.url or "video:batch" in req.url:
                summary = _summarize_request_image_inputs(req)
                captured_requests.append(
                    {
                        "url": req.url,
                        "method": req.method,
                        "image_inputs": summary,
                    }
                )
                print(f"  REQUEST observed: {req.url}")

        async def on_route(route: Any) -> None:
            # Block the request from reaching Veo so no credit consumed.
            url = route.request.url
            if "video:batchAsync" in url:
                print(f"  ABORTING: {url}")
                await route.abort()
            else:
                await route.continue_()

        await page.route(ENDPOINT_GLOB, on_route)
        page.on("request", on_request)

        # Now click submit via the production selector cascade.
        print("Clicking submit (XHR will be aborted before reaching Veo)...")
        for sel in SUBMIT_BUTTON_SELECTORS:
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state="visible", timeout=2_000)
                if await btn.is_disabled():
                    print(f"  submit '{sel}' still disabled, skipping")
                    continue
                await btn.click()
                print(f"  clicked via {sel}")
                break
            except Exception as e:
                print(f"  miss {sel}: {e}")

        # Give the browser ~3s to fire the request before we close.
        await asyncio.sleep(3)
        await page.screenshot(path=str(out_dir / "02-post-submit-attempt.png"))

        evidence["slot_count_at_t_post_submit"] = await page.locator(FRAME_SLOTS_STRUCT).count()
        evidence["captured_requests"] = captured_requests

        (out_dir / "evidence.json").write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print("\n=== summary ===")
        print(f"Pre-submit slot count:  {evidence['slot_count_at_t_pre_submit']}  (expect 0)")
        print(f"Post-submit slot count: {evidence['slot_count_at_t_post_submit']}")
        print(f"Captured generate requests: {len(captured_requests)}")
        for r in captured_requests:
            ep = r["url"].split("/")[-1].split("?")[0]
            ii = r["image_inputs"]
            print(
                f"  {ep}  startImage={ii.get('startImage')!r}  endImage={ii.get('endImage')!r}  refs={ii.get('referenceCount')}"
            )

        print(f"\nDOM evidence: {out_dir / 'evidence.json'}")
        print("All requests to Veo were aborted at the browser — zero credits.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--probe-image", type=Path, required=True)
    parser.add_argument(
        "--prompt",
        default="probe test — do not submit, route blocked",
        help="Prompt text typed into the editor.",
    )
    parser.add_argument(
        "--reorder",
        action="store_true",
        help="Type prompt BEFORE binding frames (tests the ordering hypothesis).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="CLI model alias (omni-flash, veo-lite, etc.). Omit to inherit Flow's last-used.",
    )
    parser.add_argument(
        "--start-only",
        action="store_true",
        help="Bind only the Start frame (skip End). Tests whether start-only i2v works on the chosen model.",
    )
    parser.add_argument("--out-dir", type=Path, default=Path("tmp/i2v_intercept"))
    args = parser.parse_args()
    return asyncio.run(
        capture(
            args.profile,
            args.probe_image,
            args.prompt,
            args.reorder,
            args.model,
            args.start_only,
            args.out_dir,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
