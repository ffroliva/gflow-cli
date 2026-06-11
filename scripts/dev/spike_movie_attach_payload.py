#!/usr/bin/env python3
r"""Movie-consistency spike — Tier 1 (0 credits): VERIFY referenceEntities ride.

Decisive credit-free test of the character-entity attach: drive the real
references-mode attach (Tudo tab -> select tile by thumbnail id -> 'Incluir no
comando'), then submit the prompt with the generate request INTERCEPTED and
ABORTED. Playwright aborts the request before it reaches Google, so NO credit is
spent — but we capture its post_data and can assert whether
`requests[].referenceEntities` carries the entityId.

This answers why the live e2e (2026-06-06) submitted with referenceEntities=[]:
does the Tudo-tab tile + 'Incluir no comando' actually stage the entity, and
does it survive the prompt submit?

Usage (headed, supervised):

    ! .venv\Scripts\python.exe scripts\dev\spike_movie_attach_payload.py \
        --profile denon82 --project 6ba50219-0fb5-4471-a96e-83257784dfd8 \
        --thumb 231b5419-80c8-4ebb-8e19-a5b545f18273 --name Stickman --locale pt
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.transports.ui_automation_video import (  # noqa: E402
    ADD_MEDIA_BUTTON,
    PICKER_INCLUDE_BUTTON,
    VideoGenerationMixin,
)

_GEN_ROUTE_GLOB = "**/video:batchAsyncGenerateVideo*"
_PROMPT_BOX = "div[role='textbox'][data-slate-editor='true']"
_SUBMIT_BTN = "button:has(i.google-symbols:text('arrow_forward'))"


async def _run(
    *,
    profile_dir: Path,
    project_id: str,
    thumb: str,
    name: str,
    entity_id: str,
    gesture: str,
    full: bool,
    model: str,
    locale: str,
    out_path: Path,
) -> int:
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    captured: dict[str, Any] = {}

    async with build_client(profile_dir, headless=False) as client:
        page = await client._checkout_page()  # noqa: SLF001

        async def _on_route(route: Any) -> None:
            req = route.request
            if not captured:  # capture the first generate request only
                try:
                    captured["url"] = req.url
                    captured["post_data"] = req.post_data
                except Exception as e:  # noqa: BLE001
                    captured["error"] = f"{type(e).__name__}: {e}"
            # ABORT — the request never reaches Google, so no credit is charged.
            await route.abort()

        await page.route(_GEN_ROUTE_GLOB, _on_route)
        try:
            url = routes.project_editor_url(locale, project_id)
            step("1", f"goto {url}", prefix="pay")
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(4_000)
            await page.keyboard.press("Escape")

            await VideoGenerationMixin._exit_agent_mode(page)  # noqa: SLF001
            await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)  # noqa: SLF001
            if full:
                # Replicate _generate_video_locked's settings sequence (model ->
                # submode -> aspect -> count -> duration -> Escape) for the chosen
                # model — used to verify a model (e.g. omni-flash) preserves the
                # referenceEntities on submit.
                from gflow_cli.api.video import Aspect, VideoModel

                vmodel = VideoModel.from_cli(model)
                await VideoGenerationMixin._select_video_model(  # noqa: SLF001
                    page, vmodel, out_dir=None, required=False
                )
            await VideoGenerationMixin._switch_video_sub_mode(page, "references", out_dir=None)  # noqa: SLF001
            if full:
                await VideoGenerationMixin._select_video_aspect(page, Aspect.PORTRAIT)  # noqa: SLF001
                await VideoGenerationMixin._set_output_count(page, 1)  # noqa: SLF001
                await VideoGenerationMixin._select_video_duration(page, 8)  # noqa: SLF001
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(600)
            await page.wait_for_timeout(800)

            # --- attach: open picker -----------------------------------------
            add = page.locator(ADD_MEDIA_BUTTON).first
            await add.wait_for(state="visible", timeout=8000)
            await add.click()
            await page.wait_for_timeout(900)

            if gesture == "personagens-rclick":
                # Drive the REAL transport attach (Personagens tab -> right-click
                # entity tile -> context-menu 'Incluir no comando'). End-to-end
                # proof that the shipped code stages a referenceEntity.
                await VideoGenerationMixin._attach_character_entities(  # noqa: SLF001
                    page, [(entity_id, name)], out_dir=out_dir
                )
            else:  # tudo-include (proven to stage a referenceImage, not entity)
                _ = name
                tile = page.locator(f"[role='option']:has(img[src*='{thumb}'])").first
                await tile.wait_for(state="visible", timeout=8000)
                await tile.scroll_into_view_if_needed(timeout=8000)
                await tile.click()
                await page.wait_for_timeout(400)
                # PICKER_INCLUDE_BUTTON is a tier tuple since #170; the spike
                # only needs "any tier matches", so a flat comma-join is fine.
                include = page.locator(", ".join(PICKER_INCLUDE_BUTTON)).first
                await include.wait_for(state="visible", timeout=8000)
                await include.click()
                await page.wait_for_timeout(800)
            await page.screenshot(path=str(out_dir / "P1_after_include.png"))

            # Capture the prompt box right after attach — is the entity chip there?
            try:
                box_html = await page.locator(_PROMPT_BOX).first.evaluate(
                    "el => el.outerHTML.slice(0, 4000)"
                )
                (out_dir / "P1_promptbox_after_include.html").write_text(box_html, encoding="utf-8")
                print(
                    f"[pay] promptbox chip-ish? {'fe_id_' in box_html or 'data-entity' in box_html}"
                )
            except Exception as e:  # noqa: BLE001
                print(f"[pay] promptbox capture failed: {e}")

            # --- submit via the REAL transport _send_prompt ------------------
            # The entity is staged in a separate drawer (not a prompt-box chip),
            # so the standard clearing _send_prompt is used unchanged.
            await client.transport._send_prompt(  # noqa: SLF001
                page, "Stands on a clifftop at sunset, waves at the camera.", out_dir
            )

            # Wait for the intercepted request.
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not captured:
                await asyncio.sleep(0.3)
            await page.screenshot(path=str(out_dir / "P2_after_submit.png"))
        finally:
            await page.unroute(_GEN_ROUTE_GLOB, _on_route)
            client._checkin_page(page)  # noqa: SLF001

    # Parse the captured payload.
    result: dict[str, Any] = {
        "spike": "movie-attach-payload",
        "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "url": captured.get("url"),
        "captured": bool(captured.get("post_data")),
    }
    pd = captured.get("post_data")
    if pd:
        try:
            body = json.loads(pd)
            reqs = body.get("requests") or []
            ents, imgs, keys = [], [], []
            for r in reqs:
                ents += [e.get("entityId") for e in (r.get("referenceEntities") or [])]
                imgs += [i.get("mediaId") for i in (r.get("referenceImages") or [])]
                if r.get("videoModelKey"):
                    keys.append(r.get("videoModelKey"))
            result["referenceEntities"] = ents
            result["referenceImages"] = imgs
            result["videoModelKeys"] = keys
            result["route"] = (captured.get("url") or "").split("/")[-1]
        except Exception as e:  # noqa: BLE001
            result["parse_error"] = f"{type(e).__name__}: {e}"
            result["post_data_head"] = pd[:500]
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[pay] json -> {out_path}", flush=True)
    print(f"[pay] route={result.get('route')} captured={result['captured']}", flush=True)
    print(f"[pay] referenceEntities = {result.get('referenceEntities')}", flush=True)
    print(f"[pay] referenceImages   = {result.get('referenceImages')}", flush=True)
    print(f"[pay] videoModelKeys    = {result.get('videoModelKeys')}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Verify referenceEntities ride (0 credits, route-abort)."
    )
    p.add_argument("--profile", default=os.environ.get("GFLOW_CLI_PROFILE", "denon82"))
    p.add_argument("--project", required=True)
    p.add_argument("--thumb", default="", help="thumbnail_media_id (tudo-include gesture only)")
    p.add_argument(
        "--model", default="veo-lite", help="model alias to select in --full (e.g. omni-flash)"
    )
    p.add_argument("--name", default="Stickman")
    p.add_argument("--entity-id", dest="entity_id", default="", help="entityId for fe_id_ tile")
    p.add_argument(
        "--gesture",
        choices=["tudo-include", "personagens-rclick"],
        default="tudo-include",
    )
    p.add_argument("--full", action="store_true", help="replicate the full settings sequence")
    p.add_argument("--locale", default="pt")
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    profile_dir = resolve_profile_dir(args.profile)
    out_path = (
        Path(args.out) if args.out else default_out_path("spike_movie_attach_payload", ".json")
    )
    step("--", f"profile={args.profile} project={args.project} thumb={args.thumb}", prefix="pay")
    try:
        return asyncio.run(
            _run(
                profile_dir=profile_dir,
                project_id=args.project,
                thumb=args.thumb,
                name=args.name,
                entity_id=args.entity_id,
                gesture=args.gesture,
                full=args.full,
                model=args.model,
                locale=args.locale,
                out_path=out_path,
            )
        )
    except KeyboardInterrupt:
        print("[pay] aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
