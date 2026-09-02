"""VERIFICATION — can *our* stack submit an extend, or is it reCAPTCHA-walled?

This is the single load-bearing unknown from the predict council (Architect A1,
Devil's Advocate blocker #1, and a confidence discount from all five personas).
Everything downstream — module placement, CLI surface, whether this is a
~200-line feature or a UI-automation project — hangs on the answer.

## The question

Every video generation in this repo rides `ui_automation_video.py`, which drives
Flow's UI and passively captures the request Flow itself emits. We have never
composed a video-generation body ourselves. Evidence points both ways:

  AGAINST — `docs/CHARACTER.md`: a byte-exact self-assembled `batchGenerateImages`
    POST with a minted token returned 403. "Generation is never browser-free."
    (Caveat: that is an IMAGE analogue, not a video precedent.)
  FOR    — `client.upsample_image` IS a self-assembled aisandbox POST with a
    minted token, and it works live.

Nothing in the repo explains why one passes and the other doesn't. So measure it.

## Cost

**A refusal costs 0 credits** — a 403/401 is rejected before generation, which is
why the CHARACTER.md spike spent nothing. A success costs 10 credits and yields a
real 8s segment. Cheap either way.

## Two variants, because there are two candidate body shapes

  --variant scene     The body Flow's own scene-builder UI sent (captured live
                      2026-08-31): sceneContext + metadata.sceneId + frame range.
                      Highest-probability success — it is byte-shaped like the
                      real one.
  --variant workflow  The shape hurara210/google-flow-cli assumes: metadata.workflowId,
                      no sceneContext, no frame range. Untested third-party code,
                      but it plausibly models the OTHER entry point the bundle
                      revealed (VIDEO_EDITOR_EXTEND vs SCENE_BUILDER_EXTEND_SUBMITTED),
                      which we have never captured. Its model key is corrected
                      here to a tier-legal one — theirs sends `_ultra`, which is
                      SERVICE_TIER_ADVANCED-only and UNAVAILABLE on this account.

Run `scene` first. If it 200s, the submit path is proven and `workflow` becomes
an optional extra capture for the second entry point.

Usage:
    python scripts/dev/spike_extend_ourstack_verify.py --profile ffroliva \
        --project 7d3d6bd9-... --media b9458021-... --scene d7d1cc78-... \
        --variant scene --submit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
import uuid
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts" / "dev"))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api import routes  # noqa: E402

EXTEND_URL = f"{routes.FLOW_API_BASE}/video:batchAsyncGenerateVideoExtendVideo"

# Tier-legal for SERVICE_TIER_INTERMEDIATE (10 credits). The `_ultra` variants
# are SERVICE_TIER_ADVANCED-only and read UNAVAILABLE on this account — see the
# credit table in docs/superpowers/spikes/2026-08-31-veo-extend-route-recon.md.
MODEL_KEY = "veo_3_1_extension_lite"


def _build_body(
    variant: str,
    *,
    project_id: str,
    media_id: str,
    scene_id: str,
    workflow_id: str,
    prompt: str,
    token: str,
    session_id: str,
) -> dict[str, Any]:
    seed = random.randint(1000, 9999)  # noqa: S311 — parity with Flow, not crypto
    request: dict[str, Any] = {
        "aspectRatio": "VIDEO_ASPECT_RATIO_LANDSCAPE",
        "textInput": {"structuredPrompt": {"parts": [{"text": prompt}]}},
        "videoModelKey": MODEL_KEY,
        "seed": seed,
    }
    ctx: dict[str, Any] = {"batchId": str(uuid.uuid4())}

    if variant == "scene":
        ctx["audioFailurePreference"] = "RETURN_SILENCED_VIDEOS"
        ctx["sceneContext"] = {"sceneId": scene_id, "position": 1}
        request["metadata"] = {"sceneId": scene_id}
        request["videoInput"] = {
            "mediaId": media_id,
            "startFrameIndex": 1,
            "endFrameIndex": 24,
        }
    else:  # workflow
        request["metadata"] = {"workflowId": workflow_id}
        request["videoInput"] = {"mediaId": media_id}

    return {
        "mediaGenerationContext": ctx,
        "clientContext": {
            "projectId": project_id,
            "tool": "PINHOLE",
            "userPaygateTier": "PAYGATE_TIER_ONE",
            "sessionId": session_id,
            "recaptchaContext": {
                "token": token,
                "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB",
            },
        },
        "requests": [request],
        "useV2ModelConfig": True,
    }


async def _run(a: argparse.Namespace) -> int:
    async with build_client(resolve_profile_dir(a.profile)) as client:
        # Read the scene back so the workflow variant uses a REAL workflow id
        # rather than a guess — a 400 on a bad id would prove nothing about the
        # reCAPTCHA wall, which is the only thing this spike is measuring.
        workflow_id = ""
        try:
            scene = await client.get_scene_workflows(a.scene, project_id=a.project)
            step("scene", f"{len(scene.workflows)} workflows in scene {a.scene}")
            for wf in scene.workflows:
                step(
                    "scene", f"  pos={wf.metadata.position} wf={wf.workflow_id} media={wf.media_id}"
                )
            if scene.workflows:
                workflow_id = scene.workflows[0].workflow_id
        except Exception as e:  # noqa: BLE001
            step("scene", f"read-back failed ({type(e).__name__}: {e}) — workflow variant unusable")

        if a.variant == "workflow" and not workflow_id:
            step("ABORT", "no workflow id available; a 400 here would not answer the question")
            return 1

        step("mint", "minting reCAPTCHA token via our own TokenMinter (action=VIDEO_GENERATION)")
        token = await client._mint_recaptcha_token("VIDEO_GENERATION")  # noqa: SLF001
        step("mint", f"token ok ({len(token)} chars)")

        body = _build_body(
            a.variant,
            project_id=a.project,
            media_id=a.media,
            scene_id=a.scene,
            workflow_id=workflow_id,
            prompt=a.prompt,
            token=token,
            session_id=f";{int(time.time() * 1000)}",
        )
        redacted = json.loads(json.dumps(body))
        redacted["clientContext"]["recaptchaContext"]["token"] = "<TOKEN>"
        step("body", f"variant={a.variant}\n{json.dumps(redacted, indent=2)}")

        if not a.submit:
            step("STOP", "--submit not given; nothing sent")
            return 0

        step("SUBMIT", "*** POSTing via client._post_json — OUR transport ***")
        result: dict[str, Any]
        try:
            resp = await client._post_json(  # noqa: SLF001
                EXTEND_URL, body, route_name="batchAsyncGenerateVideoExtendVideo"
            )
            step("RESULT", "HTTP 200 — OUR STACK CAN SUBMIT EXTENDS")
            credits = resp.get("remainingCredits") if isinstance(resp, dict) else None
            step("RESULT", f"remainingCredits={credits}")
            result = {"outcome": "SUCCESS", "response": resp}
        except Exception as e:  # noqa: BLE001
            step("RESULT", f"REFUSED — {type(e).__name__}: {e}")
            step("RESULT", "0 credits spent (refusal precedes generation)")
            result = {"outcome": "REFUSED", "error_class": type(e).__name__, "error": str(e)}

        out = default_out_path(f"spike_extend_ourstack_{a.variant}")
        out.write_text(
            json.dumps({"variant": a.variant, "body": redacted, **result}, indent=2, default=str),
            encoding="utf-8",
        )
        step("out", str(out))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="ffroliva")
    ap.add_argument("--project", required=True)
    ap.add_argument("--media", required=True, help="source media id to extend")
    ap.add_argument("--scene", required=True)
    ap.add_argument("--variant", choices=("scene", "workflow"), default="scene")
    ap.add_argument("--submit", action="store_true", help="BILLS 10 CREDITS ON SUCCESS")
    ap.add_argument("--prompt", default="the camera drifts slowly out over the open water")
    return asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
