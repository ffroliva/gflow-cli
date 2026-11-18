"""Does `_select_video_model` really select — and really refuse? Zero credits.

Video is the credit-bearing arm (veo-quality 100 against veo-lite's 10), so the
guard added on 2026-08-26 must be verified without generating. It can be: model
selection happens in the settings panel, entirely BEFORE submit. This drives the
real production function against the real Flow DOM and never sends a prompt.

Three questions, all answered live:

1. Does every model Flow offers still select? (the guard must not break the
   working path — a false refusal is as bad as a silent fallback)
2. Does a model Flow does NOT offer refuse? `VEO_3_1_LITE_LOWER_PRIORITY` was
   not offered to denon82 on 2026-08-26, which makes it a real MISS to test
   rather than a synthetic one.
3. Does an AMBIGUOUS selector refuse rather than resolving `.first`? Injected by
   monkeypatching the registry with a deliberately-ambiguous selector — `Veo 3.1`
   matches Lite, Fast and Quality.

    uv run python scripts/dev/live_verify_video_model_select.py \
        --profile denon82 --project <pid>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spike_common import build_client, resolve_profile_dir  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from gflow_cli.api.transports import ui_automation_video as mod  # noqa: E402
from gflow_cli.api.video import VideoModel  # noqa: E402
from gflow_cli.errors import VideoModelSelectionError  # noqa: E402


async def _try_select(page: Any, transport: Any, model: VideoModel) -> tuple[str, str]:
    """Re-enter video mode, attempt a selection, report the outcome."""
    # A fresh editor entry each time: `_switch_to_video_mode` leaves the settings
    # menu OPEN by contract, and its trigger is the SAME button as the panel's,
    # so re-clicking without a reset toggles it shut. That exact sequencing error
    # is what made #539's video capture read empty for months.
    await transport._enter_editor(page, None, project_id=_PROJECT)
    await transport._switch_to_video_mode(page, out_dir=None)
    try:
        await mod.VideoGenerationMixin._select_video_model(page, model, out_dir=None)
    except VideoModelSelectionError as exc:
        return "REFUSED", str(exc)[:160]
    except Exception as exc:  # noqa: BLE001
        return f"OTHER({type(exc).__name__})", str(exc)[:160]
    finally:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)
    return "SELECTED", ""


_PROJECT = ""


async def main() -> int:
    global _PROJECT
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    _PROJECT = args.project

    rows: list[tuple[str, str, str, str]] = []
    async with build_client(resolve_profile_dir(args.profile)) as client:
        t = client.transport
        page = client._page

        # --- 1 & 2: every registered model, as-shipped ---
        for model in VideoModel:
            outcome, detail = await _try_select(page, t, model)
            expected = (
                "REFUSED" if model is VideoModel.VEO_3_1_LITE_LOWER_PRIORITY else "SELECTED"
            )
            rows.append((model.value, expected, outcome, detail))

        # --- 3: injected ambiguity ---
        original = mod.VIDEO_MODEL_OPTION_SELECTORS[VideoModel.VEO_3_1_FAST]
        mod.VIDEO_MODEL_OPTION_SELECTORS[VideoModel.VEO_3_1_FAST] = (
            "[role='menuitem']:has-text('Veo 3.1')"  # matches Lite + Fast + Quality
        )
        try:
            outcome, detail = await _try_select(page, t, VideoModel.VEO_3_1_FAST)
            rows.append(("veo_3_1_fast (AMBIGUOUS selector)", "REFUSED", outcome, detail))
        finally:
            mod.VIDEO_MODEL_OPTION_SELECTORS[VideoModel.VEO_3_1_FAST] = original

    print(f"\n{'case':<40}{'expected':<11}{'actual':<11}detail")
    print("-" * 120)
    failures = 0
    for case, expected, actual, detail in rows:
        ok = actual == expected
        failures += 0 if ok else 1
        print(f"{'OK ' if ok else 'FAIL'} {case:<36}{expected:<11}{actual:<11}{detail}")

    print(f"\n{len(rows) - failures}/{len(rows)} as expected. Zero credits spent (no submit).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
