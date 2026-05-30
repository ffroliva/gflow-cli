# ruff: noqa: E501
"""Zero-credit reproducer for the issue #125 model-select flakiness.

The paid i2v run failed to select veo-lite (`model_option_not_found`) and fell
back to omni-flash → T2V, yet `capture_i2v_intercept_submit.py --model veo-lite`
selects it fine. The two differ in WHERE `_wait_video_editor_ready` (a ~1s
settle) runs relative to `_select_video_model`:

  intercept probe : enter → dismiss → switch_video_mode → wait_ready → select
  production      : enter → wait_ready → dismiss → switch_video_mode → select

Hypothesis: production probes the model menu too soon after opening the
settings menu (no settle between switch and select), so the options haven't
rendered. This probe runs the PRODUCTION order and reports whether
`_select_video_model` logs `model_selected` or `model_option_not_found` — and
with `--settle-ms N` inserts a settle between switch and select to test the fix.

Zero credits: never clicks Generate. Reports the selected model + (on a miss)
the menu items that WERE present, then quits.

Usage:
    .venv/Scripts/python.exe scripts/dev/capture_i2v_model_select_repro.py \\
        --profile promo-denon82 --model veo-lite --settle-ms 0
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports.ui_automation_video import (
    VideoGenerationMixin,
)
from gflow_cli.api.video import VideoModel
from gflow_cli.paths import default_home, profile_subdir


async def capture(profile_name: str, model_alias: str, settle_ms: int) -> int:
    profile_dir = profile_subdir(default_home(), profile_name)
    if not profile_dir.exists():
        sys.exit(f"Profile dir does not exist: {profile_dir}")
    model = VideoModel.from_cli(model_alias)
    if model is None:
        sys.exit(f"Unknown model alias: {model_alias!r}")
    print(f"Profile: {profile_name}  model={model.value}  settle_ms={settle_ms}")

    async with FlowApiClient(profile_dir=profile_dir, headless=False) as client:
        transport = client.transport
        if transport is None:
            sys.exit("FlowApiClient.transport is None")
        page = await client._checkout_page()

        # PRODUCTION order (mirrors _generate_video_locked exactly).
        await transport._enter_editor(page, None)  # type: ignore[attr-defined]
        await VideoGenerationMixin._wait_video_editor_ready(page)
        await transport._dismiss_blocking_overlays(page, None)  # type: ignore[attr-defined]
        await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)

        if settle_ms > 0:
            print(f"Inserting {settle_ms}ms settle between switch and select...")
            await page.wait_for_timeout(settle_ms)

        # Enumerate menu items right before select (diagnostic).
        try:
            items = await page.locator("[role='menuitem']").all_inner_texts()
            print(f"menuitems visible before select ({len(items)}): {[t[:30] for t in items][:12]}")
        except Exception as e:
            print(f"menuitem enumeration failed: {e}")

        # Try selection. _select_video_model logs model_selected OR
        # model_option_not_found — both visible on stderr.
        try:
            await VideoGenerationMixin._select_video_model(
                page, model, out_dir=None, required=False
            )
        except Exception as e:  # noqa: BLE001
            print(f"_select_video_model raised: {e}")

        # Report what the model picker now shows (its label reflects the choice).
        try:
            items_after = await page.locator("[role='menuitem']").all_inner_texts()
            print(f"menuitems after select attempt ({len(items_after)})")
        except Exception:
            pass
        print("Done (no Generate — zero credits).")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--model", default="veo-lite")
    parser.add_argument("--settle-ms", type=int, default=0)
    args = parser.parse_args()
    return asyncio.run(capture(args.profile, args.model, args.settle_ms))


if __name__ == "__main__":
    sys.exit(main())
