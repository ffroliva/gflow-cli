"""Why does an entity-bound submit never reply? — logs EVERY batchexecute rpcid (#723).

**The question.** `migrated_composer.attach_character_entities` works: it drives the `@`
picker, commits a chip carrying ``data-reference-type="entity"`` with the requested id,
and Flow loads the character's voice sample. The submit that follows then never produces a
``YhhmEf`` / ``eb1hJf`` / ``MZZa6b`` reply — three runs, 60 s each, cause unknown. The
guard in `_unported_form` therefore still refuses character references outright, so the
whole point of a Character entity (its voice, server-side, durable) is unreachable on this
host.

**The hypothesis this probe exists to kill or confirm.** `SUBMIT_RPCS` watches exactly
three rpcids. An entity-bound generation is a different form; if the app submits it on a
**fourth** rpcid, the observer waits forever on RPCs that were never going to fire, and
that is indistinguishable from "the submit did nothing". Nothing in an incident bundle
could show this, because the video path parks the page at about:blank before the capture
runs (#722) — which is why this reads the wire live instead.

So: log every batchexecute request AND response, with its rpcid, stamped with the phase it
fired in, straight through the submit. If an unrecognised rpcid appears after the click,
the fix is a one-line addition to `SUBMIT_RPCS`. If literally nothing fires, the click is
not reaching the button and the fix is in the submit gesture instead. Either answer ends a
guess that has stood since 2026-09-07.

**Cost.** This one DOES click submit, so a generation may start and bill. Run it on a
profile whose credits you are willing to spend. It is the cheapest way to answer a
question that has already cost three blind 60-second runs.

Usage:
    uv run python scripts/dev/spike_entity_submit_rpcs.py <profile> <project_id> \
        --entity-id <uuid> --entity-name <Name> [--wait 90]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    MODEL_KEY,
    SUBMIT_RPCS,
    MigratedComposer,
    _ligature,
)
from gflow_cli.api.video import (  # noqa: E402
    _VIDEO_MODEL_FROM_CLI,
    Aspect,
    GenerateVideoRequest,
    Mode,
    VideoModel,
)

from _spike_common import build_client, default_out_path, resolve_profile_dir  # noqa: E402, isort: skip

PROMPT = (
    "Warm golden-hour footage, full-frame 16:9 image filling the entire frame edge to "
    "edge, no bars, no border. Setting: a high desert ridge of cracked red stone, still "
    "air. Geometry: chest-up close framing, the camera fixed and completely still. "
    "Action: he stands still and speaks to camera, calm and unhurried. Dialogue: he "
    "says, low and unhurried: 'The wind has not turned once.' A single continuous shot. "
    "Avoid: camera movement, any cut, letterbox bars, border, on-screen text."
)


def _stage(msg: str) -> None:
    print(f"[spike] {msg}", flush=True)


def _rpcid(url: str) -> str | None:
    try:
        return urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("rpcids", [None])[0]
    except Exception:  # noqa: BLE001 - a malformed URL is not our frame
        return None


async def _probe(page: Any, project_id: str, entity_id: str, name: str, wait_s: float,
                 ref_image: Path | None = None, model: VideoModel | None = None) -> dict:
    seen: list[dict[str, Any]] = []
    phase = {"now": "idle"}

    def on_request(request: Any) -> None:
        url = str(getattr(request, "url", ""))
        if "batchexecute" not in url:
            return
        rid = _rpcid(url)
        row: dict[str, Any] = {
            "dir": "request",
            "phase": phase["now"],
            "rpcid": rid,
            "watched": rid in SUBMIT_RPCS,
        }
        # The submit body carries the MODEL KEY Flow derived from the submode plus the
        # picker choice (e.g. `abra_r2v_8s`, `veo_3_1_r2v_lite_low_priority`). When the
        # backend rejects an entity-bound run with a null payload and no message, that
        # key is the only place the chosen model is visible — and a model the character
        # form does not support would look exactly like this.
        if rid in SUBMIT_RPCS:
            try:
                body = request.post_data
            except Exception:  # noqa: BLE001 - some requests expose no body
                body = None
            if body:
                keys = sorted(set(MODEL_KEY.findall(body)))
                row["model_keys"] = keys
                row["body_head"] = body[:700]
        seen.append(row)

    async def on_response(response: Any) -> None:
        url = str(getattr(response, "url", ""))
        if "batchexecute" not in url:
            return
        rid = _rpcid(url)
        try:
            body = await response.text()
        except Exception:  # noqa: BLE001 - streamed/aborted body
            body = ""
        seen.append({
            "dir": "response",
            "phase": phase["now"],
            "rpcid": rid,
            "watched": rid in SUBMIT_RPCS,
            "status": getattr(response, "status", None),
            "bytes": len(body),
            "head": body[:400],
        })

    page.on("request", on_request)
    page.on("response", lambda r: asyncio.create_task(on_response(r)))

    composer = MigratedComposer()
    phase["now"] = "ensure_editor"
    await composer.ensure_editor(page, project_id)

    # apply_video_settings is NOT optional here, and skipping it is its own bug: it
    # selects the **Ingredients** submode whenever reference_entities is set, and the
    # composer's own comment records that a character chip submitted from under Frames
    # "clicked submit and got no reply at all". A spike that omits this stage measures
    # the omission, not the port.
    phase["now"] = "apply_settings"
    # With --ref-image this is a genuine R2V run carrying a media reference AND a
    # character, which is the combination attach_character_entities documents as
    # supported ("an r2v run can carry media references AND characters"). Without it,
    # a T2V request sits in the Ingredients submode the entity forces, and the app
    # derives its model key from that submode plus the picker choice — so a t2v key
    # under Ingredients is a plausible cause of a backend rejection, and untested.
    request = GenerateVideoRequest(
        prompt=PROMPT,
        mode=Mode.R2V if ref_image else Mode.T2V,
        aspect=Aspect.LANDSCAPE,
        model=model,  # None -> Flow's own UI default, untouched picker
        duration=8,
        reference_images=(ref_image,) if ref_image else (),
        reference_entities=(entity_id,),
        reference_entity_names=(name,),
    )
    await composer.apply_video_settings(page, request)
    _stage(
        f"settings applied: mode={request.mode.value}, "
        f"model={model or 'FLOW DEFAULT'}, 16:9, 8s"
    )

    reference_ids: tuple[str, ...] = ()
    if ref_image:
        phase["now"] = "attach_reference_image"
        reference_ids = await composer.attach_references(page, project_id, (ref_image,))
        _stage(f"reference image attached: {reference_ids}")

    phase["now"] = "attach_entity"
    _stage(f"attaching entity {name} ({entity_id})")
    await composer.attach_character_entities(
        page, entity_ids=(entity_id,), names=(name,), clear=not reference_ids
    )
    chips_after_attach = await composer.read_chips(page)
    _stage(f"chips after attach: {chips_after_attach}")

    phase["now"] = "prompt"
    await composer.send_prompt(page, PROMPT, append=True)

    phase["now"] = "submit"
    _stage(f"clicking submit, then watching for {wait_s}s")
    # The same locale-invariant gesture submit_and_observe uses: the icon ligature,
    # never a text label (AGENTS.md forbids text-label selectors outright).
    submit = page.locator("button").filter(has=_ligature(page, "arrow_forward")).first
    await submit.click(timeout=10_000)
    _stage("submit clicked")
    await page.wait_for_timeout(int(wait_s * 1000))

    phase["now"] = "done"
    rpcids = sorted({e["rpcid"] for e in seen if e["rpcid"]})
    after_submit = sorted({e["rpcid"] for e in seen if e["phase"] == "submit" and e["rpcid"]})
    unwatched = [r for r in after_submit if r not in SUBMIT_RPCS]
    return {
        "project_id": project_id,
        "entity_id": entity_id,
        "entity_name": name,
        "watched_rpcs": list(SUBMIT_RPCS),
        "chips_after_attach": chips_after_attach,
        "all_rpcids_seen": rpcids,
        "rpcids_after_submit": after_submit,
        "UNWATCHED_after_submit": unwatched,
        "verdict": (
            f"an UNWATCHED rpcid fired after submit: {unwatched} — add it to SUBMIT_RPCS"
            if unwatched
            else (
                "no batchexecute fired after submit at all — the click is not reaching "
                "the button, or the app refused client-side"
                if not after_submit
                else "only watched rpcids fired — the reply shape is the problem, not the id"
            )
        ),
        "submit_model_keys": sorted(
            {k for e in seen if e.get("model_keys") for k in e["model_keys"]}
        ),
        "events": seen,
    }


async def _main(profile: str, project_id: str, entity_id: str, name: str,
                wait_s: float, out_path: str, ref_image: Path | None,
                model: VideoModel | None) -> int:
    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 — dev instrument
        assert page is not None
        report = await _probe(page, project_id, entity_id, name, wait_s, ref_image, model)
        Path(out_path).write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        _stage(f"VERDICT: {report['verdict']}")
        _stage(f"report written to {out_path}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("profile")
    p.add_argument("project_id")
    p.add_argument("--entity-id", required=True)
    p.add_argument("--entity-name", required=True)
    p.add_argument("--wait", type=float, default=90.0)
    p.add_argument("--ref-image", default=None,
                   help="local image to attach as an R2V media reference alongside the "
                        "character; makes this a genuine r2v run rather than t2v-in-Ingredients")
    p.add_argument("--model", default=None,
                   help="omni-flash | veo-lite | veo-fast | veo-quality; omit to leave "
                        "Flow's own picker untouched, which is what a human gets")
    p.add_argument("--out", default=None)
    a = p.parse_args()
    out = a.out or str(default_out_path("entity_submit_rpcs"))
    ref = Path(a.ref_image) if a.ref_image else None
    mdl = _VIDEO_MODEL_FROM_CLI[a.model] if a.model else None
    return asyncio.run(
        _main(a.profile, a.project_id, a.entity_id, a.entity_name, a.wait, out, ref, mdl)
    )


if __name__ == "__main__":
    raise SystemExit(main())
