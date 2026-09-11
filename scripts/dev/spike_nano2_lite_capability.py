r"""Nano Banana 2 Lite: does selecting it BIND, and what does it produce? (0 credits)

The capability spike `tests/flow_selectors/test_model_governance.py` demanded before this
tier ships. Its waiver read:

    "Nano Banana 2 Lite" -- Discovered 2026-08-26. A lower-tier image model we do not
    expose. Waived pending a capability spike (cost/quality) before we ship it.

PR #787 removes that waiver. This is the spike it was waiting for.

## Rung 1 first -- what the repo already established

- **Image generation costs 0 Flow credits** (the 2026-08-26 model-cost-quota catalogue).
  So "cost" for an image tier is DAILY QUOTA, not credits.
- **"Daily quota has no such oracle. It is observable *only on exhaustion*, via the 429."**
  Same doc. So the cost half of "cost/quality" is NOT measurable here without burning an
  account's day, and this spike does not pretend otherwise -- it says so and stops.
- Both `2026-08-26-model-attribution-provenance.md` and the cost/quota catalogue already
  list "Cost/quota of Nano Banana 2 Lite" as explicitly NOT established.

## The sharper question rung 1 exposed

`migrated_composer.py` builds its result with `model_name_type=request.model.value` -- it
ECHOES the model we asked for. The labs path reads `generated["modelNameType"]` from the
response; this one does not. And `batchexecute.image_records` decodes media id, workflow
id, url, seed, prompt and dimensions -- **no model field at all**.

So on the migrated host, "it generated an image with --model nano2-lite" proves the UI did
not error. It does NOT prove the tier bound. A picker click that silently did not apply is
a known bug class here -- the provenance spec names it, and cites
`_assert_image_entities_attached` as the precedent for verifying a UI action at the wire.

    A REPLY THAT REPEATS OUR REQUEST IS NOT AN OBSERVATION.

So: capture the raw `ogiZ0b` reply for one generation per tier and search it, structurally,
for the wire model tokens. Whatever is found (or is not) answers both the binding question
and whether attribution on this host is verifiable at all.

## Pre-registered readings -- written before the run

| Outcome | Reading |
|---|---|
| lite carries HARBOR_SEAL, control NARWHAL | the picker binds; #787's mapping is real |
| both replies carry the SAME token | the lite selection silently falls back |
| neither reply carries any token | the wire does not attribute images at all here |
| a reply is unparseable, or a run fails | unmeasured for that arm; report it, never average |

Quality is reported as measured artifacts (dimensions, bytes, and the files themselves for
a human look) and explicitly NOT as a verdict: two images from one prompt cannot rank two
tiers, and saying they can is how a preference becomes a fact.

## Cost

Two image generations: **0 Flow credits**, some daily quota. No video. Nothing deleted.
The prompt is benign and fixed so the two arms are comparable.

    python scripts/dev/spike_nano2_lite_capability.py --profile ci-probe --project <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

from gflow_cli.api.image import Aspect, GenerateImageRequest  # noqa: E402
from gflow_cli.api.image import Model as ImageModel  # noqa: E402


#: Resolved by NAME, not by attribute: `HARBOR_SEAL` exists only on the branch that adds
#: it (#787), and a spike that cannot be imported on `develop` is a spike nobody re-runs.
#: A missing tier is reported as a skipped arm rather than an ImportError.
def _model(name: str) -> ImageModel | None:
    return getattr(ImageModel, name, None)


#: The submit RPC whose reply carries the generated media.
IMAGE_SUBMIT_RPC = "ogiZ0b"

#: Every wire tier name this repo knows, plus the two menu labels. Searched for as
#: TOKENS in the reply — the body itself is never recorded, only which tokens appeared
#: and how often, because these payloads carry prompts and signed URLs.
_MODEL_TOKENS = (
    "HARBOR_SEAL",
    "NARWHAL",
    "GEM_PIX_2",
    "IMAGEN_3_5",
    "Nano Banana 2 Lite",
    "Nano Banana 2",
    "Nano Banana Pro",
)

PROMPT = "a single green pear on a plain white background, soft even studio light"


def _token_hits(text: str) -> dict[str, int]:
    """Which model tokens appear in the reply, and how often. Never the text itself."""
    return {tok: len(re.findall(re.escape(tok), text)) for tok in _MODEL_TOKENS if tok in text}


async def run_arm(profile_dir: Path, project_id: str, model: ImageModel) -> dict[str, Any]:
    replies: list[dict[str, Any]] = []

    async with build_client(profile_dir) as client:
        page = client.transport._page  # noqa: SLF001

        def on_response(resp: Any) -> None:
            if IMAGE_SUBMIT_RPC not in str(getattr(resp, "url", "")):
                return

            async def _read() -> None:
                try:
                    text = await resp.text()
                except Exception:  # noqa: BLE001 - a listener must never break the run
                    return
                replies.append(
                    {
                        "len": len(text),
                        "model_tokens": _token_hits(text),
                        # Structural only: the JSON keys present at the top of the frame,
                        # so a future reader can see WHERE a model field would live.
                        "keys_seen": sorted(set(re.findall(r'"([a-zA-Z]{3,30})":', text)))[:40],
                    }
                )

            asyncio.ensure_future(_read())  # noqa: RUF006

        page.on("response", on_response)
        t0 = time.monotonic()
        step(model.value, "generating")
        try:
            image = await client.generate_image(
                project_id=project_id,
                req=GenerateImageRequest(prompt=PROMPT, model=model, aspect=Aspect.SQUARE, count=1),
            )
            err = None
        except Exception as exc:  # noqa: BLE001 - a failed arm is a datum
            image, err = None, f"{type(exc).__name__}: {str(exc)[:200]}"
        await asyncio.sleep(1.0)  # let the reply reader finish
        page.remove_listener("response", on_response)

        out: dict[str, Any] = {
            "requested_model": model.value,
            "elapsed_s": round(time.monotonic() - t0, 1),
            "error": err,
            "replies": replies,
        }
        if image is not None:
            out["result"] = {
                "media_name": image.media_name,
                "seed": image.seed,
                # NOTE: echoed from our request on this host -- that is the finding,
                # not a measurement. Recorded so the two can be compared side by side.
                "model_name_type_ECHOED": image.model_name_type,
                "dimensions": list(image.dimensions),
                "aspect_ratio": image.aspect_ratio,
                "display_name": image.display_name,
                "is_signed_url": image.is_signed_url,
            }
        return out


async def verify_attribution(
    profile_dir: Path, project_id: str, media_by_model: dict[str, str]
) -> dict[str, Any]:
    """Re-open the project and look for the model Flow RECORDED against each media.

    The submit reply carries no attribution, so this asks the other surface: the
    project load. Every ``batchexecute`` reply is searched for the media ids created
    above and for the wire model tokens, and only the CO-OCCURRENCE is recorded — never
    the body, which carries prompts and signed URLs.

    A media id and a model token in the same reply is a lead, not a binding: proximity
    in one payload does not prove the pair is associated. Whether a reply contains both
    is still the difference between "attribution exists somewhere" and "it does not".
    """
    found: list[dict[str, Any]] = []

    async with build_client(profile_dir) as client:
        page = client.transport._page  # noqa: SLF001

        def on_response(resp: Any) -> None:
            url = str(getattr(resp, "url", ""))
            if "batchexecute" not in url:
                return

            async def _read() -> None:
                try:
                    text = await resp.text()
                except Exception:  # noqa: BLE001
                    return
                tokens = _token_hits(text)
                ids = {name: mid for name, mid in media_by_model.items() if mid in text}
                if tokens or ids:
                    rpcids = re.findall(r"rpcids=([A-Za-z0-9]+)", url)
                    found.append(
                        {
                            "rpcid": rpcids[0] if rpcids else "?",
                            "len": len(text),
                            "model_tokens": tokens,
                            "media_ids_present": sorted(ids),
                        }
                    )

            asyncio.ensure_future(_read())  # noqa: RUF006

        page.on("response", on_response)
        step("verify", f"re-opening project {project_id}")
        await page.goto(
            f"https://flow.google.com/project/{project_id}",
            wait_until="domcontentloaded",
            timeout=45_000,
        )
        await asyncio.sleep(12.0)
        page.remove_listener("response", on_response)

    return {"replies_with_a_hit": found}


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    args = ap.parse_args()

    profile_dir = resolve_profile_dir(args.profile)
    arms: list[dict[str, Any]] = []
    for name in ("HARBOR_SEAL", "NARWHAL"):
        model = _model(name)
        if model is None:
            step(name, "SKIPPED - this build does not define the tier")
            arms.append({"requested_model": name, "skipped": "tier not in this build"})
            continue
        arms.append(await run_arm(profile_dir, args.project, model))

    media_by_model = {
        a["requested_model"]: a["result"]["media_name"]
        for a in arms
        if a.get("result", {}).get("media_name")
    }
    attribution = (
        await verify_attribution(profile_dir, args.project, media_by_model)
        if media_by_model
        else {"replies_with_a_hit": [], "note": "no media created; nothing to verify"}
    )

    result = {
        "question": (
            "does --model nano2-lite BIND on the migrated host, and what does it produce? "
            "(the capability spike the model-governance waiver required)"
        ),
        "cost": "two image generations: 0 Flow credits, daily quota only",
        "prompt": PROMPT,
        "note_on_cost": (
            "Daily quota is observable ONLY on exhaustion (429 naming the model) per "
            "docs/superpowers/specs/2026-08-26-model-cost-quota-catalog.md, so the COST "
            "half of 'cost/quality' is not measured here and is not inferred."
        ),
        "arms": arms,
        "attribution_on_project_load": attribution,
    }
    out = default_out_path("spike_nano2_lite_capability", ".json")
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    step("done", f"wrote {out}")
    for arm in arms:
        if "skipped" in arm:
            step(arm["requested_model"], f"SKIPPED - {arm['skipped']}")
            continue
        step(
            arm["requested_model"],
            f"error={arm['error']} replies={len(arm['replies'])} "
            f"tokens={[r['model_tokens'] for r in arm['replies']]}",
        )
    step("verify", json.dumps(attribution["replies_with_a_hit"])[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
