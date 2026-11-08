# ruff: noqa: E501
"""Measure Omni 1.2 Flash's start+end submit on flow.google.com (issue #1178). Cost: $0.

The real ``run_video`` drives each case (model, duration, resolution, both frame
uploads, prompt, Send). A ``page.route`` handler installed first records every
submit-rpc body and ``route.abort()``s it, so Google never sees a submit and nothing
is billed. Uploads (``maseQ``) are free.

Per case it records: the rpc id, the model key, whether both frame ids ride the body,
whether gflow's request observer saw the request (``page.on("request")``), and the
error ``run_video`` raised (the production guard's verdict).

Cases: omni-flash 8s 360p is the POSITIVE CONTROL (measured ``*_first_last`` key,
2026-09-17): if it does not show ``nprQif`` + ``first_last`` + both ids, the method is
broken and the Omni 1.2 rows mean nothing. Then omni-1.2-flash at 8s and 10s, 360p and
720p.

Pre-registered reading (written before the run):
- Control OK, Omni 1.2 rows on ``nprQif`` with both ids and a key containing neither
  ``interpolation`` nor ``first_last``, and run_video raising WireFormatError: claim A
  is reproduced, and the key shape found is what the guard must accept.
- Omni 1.2 rows carrying the ``first_last`` token: claim A does not reproduce on this
  account; settles nothing about the reporter's account (Ultra).
- Route handler sees the body AND the observer fires for the same request: a route
  guard can refuse BEFORE send with the same inputs the observer has (claim B fix is
  feasible). Handler never sees it: route-abort cannot guard video; B stays open.
- No submit captured at all: unmeasured (the click produced no batchexecute).

Usage:
    uv run python scripts/dev/spike_omni12_interpolation_key.py --profile denon82 \
        --project 90fe42d6-b632-4150-a2c6-7f243ab01705
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports import migrated_composer as mc
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel
from gflow_cli.paths import default_home, profile_subdir

CASES: tuple[tuple[VideoModel, int, str], ...] = (
    (VideoModel.OMNI_FLASH, 8, "360p"),  # positive control
    (VideoModel.OMNI_1_2_FLASH, 8, "360p"),
    (VideoModel.OMNI_1_2_FLASH, 8, "720p"),
    (VideoModel.OMNI_1_2_FLASH, 10, "360p"),
    (VideoModel.OMNI_1_2_FLASH, 10, "720p"),
)


def _frames(out: Path) -> tuple[Path, Path]:
    start, end = out / "start.png", out / "end.png"
    Image.new("RGB", (720, 1280), (220, 60, 40)).save(start)
    Image.new("RGB", (720, 1280), (40, 90, 220)).save(end)
    return start, end


async def run_case(
    profile_dir: Path, project: str, model: VideoModel, seconds: int, res: str, out: Path
) -> dict[str, Any]:
    start, end = _frames(out)
    row: dict[str, Any] = {"model": model.value, "duration": seconds, "resolution": res}
    submits: list[dict[str, Any]] = []
    observed: list[str] = []

    def is_submit(url: str) -> bool:
        # URL-only, like the image guard: routing every batchexecute also catches gzip
        # bodies, and continue_() on those fails in Playwright 1.61 (measured here).
        return mc.url_rpcid(url) in mc.SUBMIT_RPCS

    async def block(route: Any, request: Any) -> None:
        body = mc.request_body(request)
        key = mc.MODEL_KEY.search(body)
        submits.append(
            {
                "rpcid": mc.url_rpcid(request.url),
                "model_key": key.group(0) if key else None,
                "body": body,
            }
        )
        await route.abort()  # nothing reaches Google; nothing is billed

    def watch(request: Any) -> None:
        body = mc.request_body(request)
        rpcid = mc.url_rpcid(request.url) or mc.body_rpcid(body)
        if rpcid in mc.SUBMIT_RPCS:
            observed.append(rpcid)

    req = GenerateVideoRequest(
        prompt="a slow dolly between the two frames",
        mode=Mode.I2V,
        aspect=Aspect.PORTRAIT,
        model=model,
        duration=seconds,
        resolution=res,
        start_image=start,
        end_image=end,
    )
    ids: dict[str, str] = {}
    real_start, real_end = mc.MigratedComposer.attach_start_frame, mc.MigratedComposer.attach_end_frame

    async def rec_start(self: Any, *a: Any, **k: Any) -> str:
        ids["start"] = await real_start(self, *a, **k)
        return ids["start"]

    async def rec_end(self: Any, *a: Any, **k: Any) -> str:
        ids["end"] = await real_end(self, *a, **k)
        return ids["end"]

    mc.MigratedComposer.attach_start_frame = rec_start  # type: ignore[method-assign]
    mc.MigratedComposer.attach_end_frame = rec_end  # type: ignore[method-assign]
    async with FlowApiClient(profile_dir=profile_dir, headless=False) as client:
        page = await client._checkout_page()
        try:
            await page.route(is_submit, block)
            page.on("request", watch)
            try:
                await mc.run_video(
                    page,
                    req,
                    project_id=project,
                    save=lambda *_a, **_k: None,  # type: ignore[arg-type,return-value]
                    poll_timeout_s=20,
                    download=False,
                    on_started=None,
                )
                row["run_video"] = "returned (unexpected under abort)"
            except Exception as exc:  # noqa: BLE001 - the verdict IS the exception
                row["run_video"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        finally:
            client._checkin_page(page)
            mc.MigratedComposer.attach_start_frame = real_start  # type: ignore[method-assign]
            mc.MigratedComposer.attach_end_frame = real_end  # type: ignore[method-assign]
    row["frame_ids"] = ids
    for s in submits:
        body = s.pop("body")
        s["body_bytes"] = len(body)
        s["carries_start_id"] = bool(ids.get("start")) and ids["start"] in body
        s["carries_end_id"] = bool(ids.get("end")) and ids["end"] in body
    row["route_handler_saw"] = submits
    row["observer_saw"] = observed
    return row


async def main_async(profile: str, project: str, out: Path) -> int:
    profile_dir = profile_subdir(default_home(), profile)
    if not profile_dir.exists():
        sys.exit(f"Profile dir does not exist: {profile_dir}")
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    try:
        for model, seconds, res in CASES:
            print(f"== {model.value} {seconds}s {res}", flush=True)
            row = await run_case(profile_dir, project, model, seconds, res, out)
            print(json.dumps(row, indent=2), flush=True)
            rows.append(row)
    finally:
        (out / "evidence.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--out-dir", default="scripts/dev/_spike_out/omni12_interpolation")
    args = parser.parse_args()
    sys.exit(asyncio.run(main_async(args.profile, args.project, Path(args.out_dir))))


if __name__ == "__main__":
    main()
