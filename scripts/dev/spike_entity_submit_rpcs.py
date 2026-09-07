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
    SUBMIT_RPCS,
    MigratedComposer,
    _ligature,
)
from gflow_cli.api.video import (  # noqa: E402
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


async def _probe(page: Any, project_id: str, entity_id: str, name: str, wait_s: float) -> dict:
    seen: list[dict[str, Any]] = []
    phase = {"now": "idle"}

    def on_request(request: Any) -> None:
        url = str(getattr(request, "url", ""))
        if "batchexecute" not in url:
            return
        rid = _rpcid(url)
        seen.append({
            "dir": "request",
            "phase": phase["now"],
            "rpcid": rid,
            "watched": rid in SUBMIT_RPCS,
        })

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
    request = GenerateVideoRequest(
        prompt=PROMPT,
        mode=Mode.T2V,
        aspect=Aspect.LANDSCAPE,
        model=VideoModel.OMNI_FLASH,
        duration=8,
        reference_entities=(entity_id,),
        reference_entity_names=(name,),
    )
    await composer.apply_video_settings(page, request)
    _stage("video settings applied (Ingredients submode, omni-flash, 16:9, 8s)")

    phase["now"] = "attach_entity"
    _stage(f"attaching entity {name} ({entity_id})")
    await composer.attach_character_entities(
        page, entity_ids=(entity_id,), names=(name,), clear=True
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
        "events": seen,
    }


async def _main(profile: str, project_id: str, entity_id: str, name: str,
                wait_s: float, out_path: str) -> int:
    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 — dev instrument
        assert page is not None
        report = await _probe(page, project_id, entity_id, name, wait_s)
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
    p.add_argument("--out", default=None)
    a = p.parse_args()
    out = a.out or str(default_out_path("entity_submit_rpcs"))
    return asyncio.run(_main(a.profile, a.project_id, a.entity_id, a.entity_name, a.wait, out))


if __name__ == "__main__":
    raise SystemExit(main())
