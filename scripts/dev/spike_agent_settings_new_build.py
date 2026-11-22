"""Spike: the agent-only driver's settings, gate and reply on Flow's 2026-11-20 build (#1387).

#1387 is blocked on an account served the agent-only composer on the new build. A
recoverable account (one with the Agent switch) in agent mode renders the same agent
surface, so this probe puts one there and drives the driver's own code on it:

1. `apply_defaults` / `restore_defaults` with the agent panel CLOSED, so the compact
   prompt box's gear opens Agent settings (#1387: the driver can run on a page whose
   panel gflow just closed);
2. the same with the panel OPEN, from the panel header's gear;
3. with `--generate`, two back-to-back `run_agent_video` turns, 16:9 then 9:16, each 4 s,
   which exercise the credit gate, the reply and the clip download, and are #1369's
   carry-over check: the second clip must come out in its own aspect, not the first's.
   `--generate-panel closed` starts them with the panel closed (the reply and gate
   anchors on a page whose panel gflow just closed); the default starts them open;
   `--generate-images` adds two `run_agent_images` turns from the same panel state
   (16:9 x2 on Nano Banana 2.1, then 9:16 x1 on Nano Banana Pro);
4. the classic composer is restored in `finally`, unless `--leave-agent-mode`, which leaves
   agent mode on with the panel open, for a CLI run through the editor gate.

Cost: $0 without `--generate` (layout clicks and settings writes, all restored). With
`--generate`, two 4-second clips at the account's default video model.

Pre-registered readings (written before the first run):

| Observation | Reading |
|---|---|
| apply/restore pass in both panel states and the rows read the same at the end as at the start | the settings port works on the new build |
| apply raises `UiSelectorDriftError` | a selector in the port is wrong; the error names it |
| a clip comes out in the other request's aspect | #1369's carry-over reproduces on the new build |
| both clips come out in their own aspect | no carry-over in this run |

    uv run python scripts/dev/spike_agent_settings_new_build.py --profile NAME --project UUID [--generate [--generate-panel closed]] [--leave-agent-mode]
"""

# ruff: noqa: E501 - the pre-registered reading table keeps its shape

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from gflow_cli.api.image import Aspect as ImageAspect
from gflow_cli.api.image import GenerateImageRequest
from gflow_cli.api.image import Model as ImageModel
from gflow_cli.api.transports import agent_only_composer as aoc
from gflow_cli.api.transports._mc_common import (
    AGENT_MODE_CHIP,
    AGENT_MODE_CHIP_ANY,
    AGENT_PANEL,
    AGENT_PANEL_CLOSE,
    AGENT_PANEL_OPEN,
    AGENT_SETTINGS_GEAR,
    IMAGE_MODEL_MENU_MATCHERS,
    MIGRATED_PROJECT_URL,
    READY_ANCHOR,
    VIDEO_MODEL_MENU_MATCHERS,
)
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.video import Aspect, GenerateVideoRequest, VideoModel
from gflow_cli.config import get_settings

OUT_ROOT = Path(__file__).parent / "_spike_out"


async def _visible_gears(page: Any) -> int:
    gears = page.locator(AGENT_SETTINGS_GEAR)
    return sum([1 for i in range(await gears.count()) if await gears.nth(i).is_visible()])


async def _close_panel(page: Any) -> None:
    panel = page.locator(AGENT_PANEL).first
    if await panel.is_visible():
        await page.locator(AGENT_PANEL_CLOSE).click(timeout=10_000)
        await panel.wait_for(state="hidden", timeout=15_000)
        await page.wait_for_timeout(1500)


async def _open_panel(page: Any) -> None:
    """Expand the agent panel from the compact box (the 2026-11-20 spike's own control)."""
    if await page.locator(AGENT_PANEL).first.is_visible():
        return
    await page.locator(AGENT_PANEL_OPEN).first.click(timeout=10_000)
    await page.locator(AGENT_PANEL).first.wait_for(state="visible", timeout=15_000)
    await page.wait_for_timeout(1500)


async def _classic(page: Any) -> bool:
    await _close_panel(page)
    pressed = page.locator(AGENT_MODE_CHIP).first
    if await pressed.count():
        await pressed.click(timeout=10_000)
        await page.wait_for_timeout(2500)
    return bool(await page.locator(READY_ANCHOR).first.is_visible())


async def _agent_mode(page: Any) -> None:
    await _close_panel(page)
    if not await page.locator(AGENT_MODE_CHIP).count():
        await page.locator(AGENT_MODE_CHIP_ANY).first.click(timeout=10_000)
        await page.locator(READY_ANCHOR).first.wait_for(state="hidden", timeout=15_000)
    await page.wait_for_timeout(2500)


async def _rows(page: Any) -> dict[str, str]:
    composer = aoc.AgentOnlyComposer()
    await composer._open_unified(page)  # noqa: SLF001 - the driver's own steps
    out = {"confirm": await composer._unified_confirm_value(page)}  # noqa: SLF001
    for section, row in aoc.DEFAULTS_ROW.items():
        caption = page.locator(row).first.locator(aoc.DEFAULTS_ROW_CAPTION)
        out[section] = " ".join((await caption.inner_text()).split())
    await composer._leave_unified(page)  # noqa: SLF001
    return out


async def _state(page: Any) -> dict[str, Any]:
    return {
        "panel_visible": await page.locator(AGENT_PANEL).first.is_visible(),
        "visible_gears": await _visible_gears(page),
        "unified": await aoc.AgentOnlyComposer()._is_unified(page),  # noqa: SLF001
    }


async def _apply_restore(page: Any, section: str, **values: Any) -> dict[str, Any]:
    composer = aoc.AgentOnlyComposer()
    out: dict[str, Any] = {"before": await _state(page)}
    try:
        snapshot = await composer.apply_defaults(page, section=section, **values)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - the class and detail are the observation
        out["raised"] = _described(exc)
        return out
    out["snapshot"] = snapshot.__dict__
    try:
        out["applied_rows"] = await _rows(page)
    except Exception as exc:  # noqa: BLE001 - the observation; the restore still runs
        out["raised"] = _described(exc)
    # Applied means the account holds the probe's values: restore whatever the read did.
    try:
        await composer.restore_defaults(page, snapshot)
        out["restored"] = True
    except Exception as exc:  # noqa: BLE001 - kept apart from the observation's own error
        out["restore_raised"] = _described(exc)
    return out


def _described(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:300]}"


def _dims(path: Path) -> str | None:
    """The video track's width x height, read from its MP4 `tkhd` box (16.16 fixed point)."""
    data = path.read_bytes()
    at = data.find(b"tkhd")
    while at != -1:
        box = data[at - 4 : at - 4 + int.from_bytes(data[at - 4 : at], "big")]
        width = int.from_bytes(box[-8:-4], "big") >> 16
        height = int.from_bytes(box[-4:], "big") >> 16
        if width and height:  # an audio track's tkhd is 0 x 0
            return f"{width}x{height}"
        at = data.find(b"tkhd", at + 4)
    return None


async def _generate(page: Any, project: str, out: Path, aspect: Aspect) -> dict[str, Any]:
    async def save(name: str, data: bytes) -> Path:
        target = out / (name if name.endswith(".mp4") else f"{name}.mp4")
        target.write_bytes(data)
        return target

    request = GenerateVideoRequest(
        prompt="a paper boat drifting on a calm pond at dusk, no text", aspect=aspect, duration=4
    )
    panel_before = await page.locator(AGENT_PANEL).first.is_visible()
    started = time.monotonic()
    try:
        result = await aoc.run_agent_video(
            page,
            request,
            project_id=project,
            save=save,
            poll_timeout_s=600,
            download=True,
            on_started=None,
        )
    except Exception as exc:  # noqa: BLE001 - the class and detail are the observation
        await page.screenshot(path=str(out / f"failed_{aspect.value}.png"))
        return {
            "aspect": aspect.value,
            "panel_before": panel_before,
            "raised": f"{type(exc).__name__}: {str(exc)[:300]}",
            "on_failure": {
                "panel_visible": await page.locator(AGENT_PANEL).first.is_visible(),
                "chat_bubbles": await page.locator(aoc.LAST_REPLY).count(),
                "live_gates": await page.locator(aoc.LIVE_GATE).count(),
                "all_gates": await page.locator("flow-permission-message").count(),
            },
        }
    path = result.local_path
    return {
        "aspect": aspect.value,
        "panel_before": panel_before,
        "panel_after": await page.locator(AGENT_PANEL).first.is_visible(),
        "unanswered_gates_after": await page.locator(aoc.LIVE_GATE).count(),
        "seconds": round(time.monotonic() - started),
        "quoted_credits": result.quoted_credits,
        "dims": _dims(path) if path else None,
    }


async def _generate_images(
    page: Any, project: str, aspect: ImageAspect, count: int, model: ImageModel
) -> dict[str, Any]:
    panel_before = await page.locator(AGENT_PANEL).first.is_visible()
    request = GenerateImageRequest(
        prompt="a paper boat on a calm pond at dusk, no text",
        aspect=aspect,
        model=model,
        count=count,
    )
    started = time.monotonic()
    try:
        images = await aoc.run_agent_images(page, request, project_id=project)
    except Exception as exc:  # noqa: BLE001 - the class and detail are the observation
        return {
            "aspect": aspect.value,
            "panel_before": panel_before,
            "raised": f"{type(exc).__name__}: {str(exc)[:300]}",
        }
    return {
        "aspect": aspect.value,
        "model": model.value,
        "asked": count,
        "got": len(images),
        "seconds": round(time.monotonic() - started),
        "panel_before": panel_before,
        "dims": [f"{w}x{h}" for w, h in (i.dimensions for i in images)],
        "unanswered_gates_after": await page.locator(aoc.LIVE_GATE).count(),
    }


async def main(
    profile: str, project: str, generate: bool, generate_panel: str, leave: bool, images: bool
) -> None:
    out = OUT_ROOT / f"agent_settings_{time.strftime('%Y%m%dT%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    snaps: dict[str, Any] = {}
    transport = UiAutomationTransport()
    await transport.setup(get_settings().profile_subdir(profile))
    page = transport._page  # noqa: SLF001 - a spike reads the live page
    assert page is not None
    try:
        await page.goto(
            MIGRATED_PROJECT_URL.format(project_id=project), wait_until="domcontentloaded"
        )
        await page.locator(f"{AGENT_MODE_CHIP_ANY}, {READY_ANCHOR}, {AGENT_PANEL}").first.wait_for(
            state="attached", timeout=45_000
        )
        await page.wait_for_timeout(4000)
        snaps["start_classic"] = await _classic(page)
        await _agent_mode(page)
        snaps["agent_mode"] = await _state(page)
        snaps["rows_start"] = await _rows(page)
        await _close_panel(page)
        snaps["video_panel_closed"] = await _apply_restore(
            page,
            "video",
            aspect_ligature="crop_9_16",
            count=2,
            model=VIDEO_MODEL_MENU_MATCHERS[VideoModel.VEO_3_1_LITE],
            confirm="never",
        )
        await _open_panel(page)
        snaps["image_panel_open"] = await _apply_restore(
            page,
            "image",
            aspect_ligature="crop_portrait",
            count=3,
            model=IMAGE_MODEL_MENU_MATCHERS[ImageModel.GEM_PIX_2],
            confirm="never",
        )
        snaps["rows_end"] = await _rows(page)
        snaps["rows_unchanged"] = snaps["rows_start"] == snaps["rows_end"]
        if generate:
            await (_close_panel(page) if generate_panel == "closed" else _open_panel(page))
            snaps["clip_16_9"] = await _generate(page, project, out, Aspect.LANDSCAPE)
            snaps["clip_9_16"] = await _generate(page, project, out, Aspect.PORTRAIT)
            snaps["rows_after_clips"] = await _rows(page)
        if images:
            await (_close_panel(page) if generate_panel == "closed" else _open_panel(page))
            snaps["images_landscape_x2"] = await _generate_images(
                page, project, ImageAspect.LANDSCAPE, 2, ImageModel.NARWHAL
            )
            # The first turn opened the panel; each image turn starts from the requested state.
            await (_close_panel(page) if generate_panel == "closed" else _open_panel(page))
            snaps["images_portrait_x1"] = await _generate_images(
                page, project, ImageAspect.PORTRAIT, 1, ImageModel.GEM_PIX_2
            )
            snaps["rows_after_images"] = await _rows(page)
    finally:
        try:
            if not leave:
                snaps["restored_classic"] = await _classic(page)
        finally:
            try:
                (out / "snapshots.json").write_text(
                    json.dumps(snaps, indent=1, ensure_ascii=False), encoding="utf-8"
                )
            finally:
                await transport.teardown()
    print(json.dumps(snaps, indent=1, ensure_ascii=False))
    print("evidence:", out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="#1387: agent-only driver settings on the new build"
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument(
        "--generate", action="store_true", help="also run two 4 s clips (spends credits)"
    )
    parser.add_argument(
        "--generate-panel",
        choices=("open", "closed"),
        default="open",
        help="the agent panel's state before the first clip and before each image turn",
    )
    parser.add_argument(
        "--generate-images",
        action="store_true",
        help="also run two image turns: 16:9 x2, then 9:16 x1 on another model",
    )
    parser.add_argument("--leave-agent-mode", action="store_true")
    args = parser.parse_args()
    asyncio.run(
        main(
            args.profile,
            args.project,
            args.generate,
            args.generate_panel,
            args.leave_agent_mode,
            args.generate_images,
        )
    )
