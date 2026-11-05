r"""What do Enter, Escape and Backspace do in the @ picker on a miss? (PR #1143) $0.

Every submit rpc is ABORTED in the browser and recorded, so nothing generates.

PR #1143 claims three things the code cannot settle:
  1. with no picker open, Enter submits the generation;
  2. the picker owns the query, so Escape cancels a miss without leaving text;
  3. Escape leaves attached chips alone.
The 2026-10-01 spike measured the opposite of what claim 1 implies for an empty picker:
Enter on "No assets found" did nothing and the dialog stayed open.

    python scripts/dev/spike_mention_empty_picker.py --profile <p> --project <uuid>

Arms, each from a cleared composer, in image and video mode:
  enter_escape   @ + miss, Enter, then Escape     (old gesture, then the PR's cleanup)
  escape_only    @ + miss, Escape                 (the PR's path on an empty picker)
  backspace      @ + miss, Backspace x len + 1    (control: today's cleanup)
  plain_enter    text with no @, Enter            (claim 1 as written)
  chip_escape    a chip, then @ + miss, Escape    (claim 3)
  retry          escape_only, then @ again        (does the picker reopen?)
  control_submit a real click on submit, aborted  (run first: can this probe see a submit?)
  idle           wait 15 s, touch nothing     (does Flow retry an aborted submit by itself?)
  pr_path        the tree's own _mention_by_name: a real name, then a miss
  retry_old      miss, Backspace cleanup, @ + a real asset name
  retry_new      miss, Escape cleanup, @ + a real asset name

Pre-registered readings (2026-11-04, before the run):

| Observation | Reading |
|---|---|
| plain_enter records a submit | claim 1 holds: Enter with no picker bills |
| enter_escape records a submit | an empty picker does not stop Enter; the PR's guard is needed |
| no arm records a submit | claim 1 is not reproduced here; the guard is harmless but its premise is unproven |
| escape_only leaves the query in the composer | claim 2 false: a retry pollutes the prompt, block the PR |
| escape_only leaves a clean composer, dialog closed | claim 2 holds |
| chip_escape loses the chip | claim 3 false, the guard raises on a working run |
| retry opens no picker | Escape breaks the retry the PR depends on |
| control_submit records no submit | the probe is blind: every "no submit" above settles nothing |
| retry_old offers 0 and retry_new offers the asset | today's Backspace cleanup breaks the retry, Escape fixes it |
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

from gflow_cli.api.image import Aspect, GenerateImageRequest  # noqa: E402
from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    COMPOSER,
    IMAGE_SUBMIT_RPC,
    PICKER_OPTION,
    SUBMIT_RPCS,
    MigratedComposer,
    request_body,
)
from gflow_cli.api.video import GenerateVideoRequest  # noqa: E402

from _spike_common import build_client, default_out_path, resolve_profile_dir  # noqa: E402, isort: skip

MISS = "zzqxnoassethasthis"
SUBMITS = (*SUBMIT_RPCS, IMAGE_SUBMIT_RPC)


async def snapshot(page: Any, composer: MigratedComposer, label: str) -> dict[str, Any]:
    box = page.locator(COMPOSER).first
    return {
        "at": label,
        "composer_text": (await box.inner_text()).strip()[:120],
        "chips": len(await composer.read_chips(page)),
        "options": await page.locator(PICKER_OPTION).count(),
        "dialogs": await page.locator("[role='dialog']:visible").count(),
        "overlays": await page.locator(".cdk-overlay-pane:visible").count(),
        "focus": await page.evaluate(
            "() => document.activeElement && document.activeElement.tagName"
        ),
        "url_path": page.url.split("flow.google.com")[-1][:60],
    }


async def type_miss(page: Any) -> None:
    await page.keyboard.type("@", delay=120)
    await page.wait_for_timeout(2200)
    await page.keyboard.type(MISS, delay=60)
    await page.wait_for_timeout(2500)


async def real_caption(page: Any) -> str:
    """An existing asset's own name, read from the open picker."""
    await page.keyboard.type("@", delay=120)
    await page.wait_for_timeout(2500)
    name = await page.locator(f"{PICKER_OPTION} .asset-title").first.inner_text()
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(800)
    return name.strip()[:24]


async def captions(page: Any) -> list[str]:
    """Up to six existing asset names, read from the open picker."""
    await page.keyboard.type("@", delay=120)
    await page.wait_for_timeout(2500)
    names = await page.locator(f"{PICKER_OPTION} .asset-title").all_inner_texts()
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(800)
    return [n.strip()[:24] for n in names if n.strip()][:6]


async def type_name(page: Any, name: str) -> None:
    await page.keyboard.type("@", delay=120)
    await page.wait_for_timeout(2200)
    await page.keyboard.type(name, delay=60)
    await page.wait_for_timeout(2500)


async def fresh(page: Any, composer: MigratedComposer) -> None:
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(400)
    await composer.clear_composer(page)
    await page.locator(COMPOSER).first.click(timeout=5000)


async def arm(page: Any, composer: MigratedComposer, name: str, seen: list[str]) -> dict[str, Any]:
    await fresh(page, composer)
    await page.keyboard.type("spike text ", delay=30)
    rows: list[dict[str, Any]] = []
    mark = len(seen)
    if name == "pr_path":
        names = await captions(page)
        rows.append({"at": "captions", "n": len(names)})
        for caption in names:
            try:
                await composer._mention_by_name(page, caption, expect_chips=1)  # noqa: SLF001
                break
            except Exception as exc:  # noqa: BLE001 - an unmentionable asset is data
                rows.append({"at": "name_failed", "error": type(exc).__name__})
                await fresh(page, composer)
        rows.append(await snapshot(page, composer, "chip_attached"))
        try:
            await composer._mention_by_name(page, MISS, expect_chips=2)  # noqa: SLF001
            rows.append({"at": "miss", "error": "no refusal"})
        except Exception as exc:  # noqa: BLE001 - the refusal is what is measured
            rows.append({"at": "miss_refused", "error": type(exc).__name__})
        rows.append(await snapshot(page, composer, "after_refusal"))
    elif name == "idle":
        await page.wait_for_timeout(15000)
        rows.append(await snapshot(page, composer, "after_idle"))
    elif name == "control_submit":
        submit = await composer._pre_submit_gate(page)  # noqa: SLF001 - dev instrument
        await submit.click(timeout=5000)
        await page.wait_for_timeout(5000)
        rows.append(await snapshot(page, composer, "after_click"))
    elif name in {"retry_old", "retry_new"}:
        caption = await real_caption(page)
        await type_miss(page)
        if name == "retry_new":
            await page.keyboard.press("Escape")
        else:
            for _ in range(len(MISS) + 1):
                await page.keyboard.press("Backspace")
        await page.wait_for_timeout(1000)
        rows.append(await snapshot(page, composer, "after_cleanup"))
        await type_name(page, caption)
        rows.append(await snapshot(page, composer, "after_retry_query"))
    elif name == "plain_enter":
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(8000)
        rows.append(await snapshot(page, composer, "after_enter"))
    elif name == "enter_escape":
        await type_miss(page)
        rows.append(await snapshot(page, composer, "miss"))
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(8000)
        rows.append(await snapshot(page, composer, "after_enter"))
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(1200)
        rows.append(await snapshot(page, composer, "after_escape"))
    elif name in {"escape_only", "retry"}:
        await type_miss(page)
        rows.append(await snapshot(page, composer, "miss"))
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(1200)
        rows.append(await snapshot(page, composer, "after_escape"))
        if name == "retry":
            await page.keyboard.type("@", delay=120)
            await page.wait_for_timeout(2200)
            rows.append(await snapshot(page, composer, "after_second_at"))
    elif name == "backspace":
        await type_miss(page)
        rows.append(await snapshot(page, composer, "miss"))
        for _ in range(len(MISS) + 1):
            await page.keyboard.press("Backspace")
        await page.wait_for_timeout(1200)
        rows.append(await snapshot(page, composer, "after_backspace"))
    elif name == "chip_escape":
        caption = await real_caption(page)
        await type_name(page, caption)
        await page.keyboard.press("ArrowDown")
        await page.wait_for_timeout(300)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(2500)
        rows.append(await snapshot(page, composer, "chip_attached"))
        await page.keyboard.type(" ", delay=60)
        await type_miss(page)
        rows.append(await snapshot(page, composer, "miss"))
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(1200)
        rows.append(await snapshot(page, composer, "after_escape"))
    return {"arm": name, "rows": rows, "submits": seen[mark:]}


async def run(args: argparse.Namespace) -> None:
    composer = MigratedComposer()
    seen: list[str] = []
    report: dict[str, Any] = {"project": args.project[:8], "modes": {}}

    async def abort_submit(route: Any, req: Any) -> None:
        body = request_body(req)
        hit = next((r for r in SUBMITS if r in req.url or r in body), None)
        if hit is None:
            await route.continue_()
            return
        seen.append(hit)
        await route.abort()

    async with build_client(resolve_profile_dir(args.profile)) as client:
        page = client._page  # noqa: SLF001 - dev instrument
        assert page is not None
        await page.route("**/batchexecute**", abort_submit)
        report["ensure_editor"] = await composer.ensure_editor(page, args.project)
        for mode in args.modes.split(","):
            if mode == "image":
                await composer.apply_image_settings(
                    page, GenerateImageRequest(prompt="x", aspect=Aspect.LANDSCAPE)
                )
            else:
                await composer.apply_video_settings(page, GenerateVideoRequest(prompt="x"))
            rows = []
            for name in args.arms.split(","):
                try:
                    rows.append(await arm(page, composer, name, seen))
                except Exception as exc:  # noqa: BLE001 - a failed arm is data here
                    rows.append({"arm": name, "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
                print(json.dumps(rows[-1], ensure_ascii=True)[:600], flush=True)
            report["modes"][mode] = rows
        await fresh(page, composer)
    path = default_out_path("spike_mention_empty_picker", ".json")
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"report -> {path}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--modes", default="image,video")
    parser.add_argument(
        "--arms", default="escape_only,backspace,retry,enter_escape,chip_escape,plain_enter"
    )
    asyncio.run(run(parser.parse_args()))
