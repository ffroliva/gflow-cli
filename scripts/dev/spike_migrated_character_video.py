r"""Does a character entity carry into a VIDEO on flow.google.com? (SPENDS VEO CREDITS)

Context: `video t2v --reference-entity` exits 36 on the migrated host because of one
gate, `migrated_composer._unported_form` -> "character references" (#723). The attach
half is ported and verified; the gate exists because the submit reply `MZZa6b` was
measured (2026-09-07) with a NULL payload, so the observer waited its 60 s budget and
reported a timeout while the job ran on in Flow's queue. A downstream user reported the
same path working end to end on 0.79.1 (#639, 2026-09-27), reply carrying the media id.

This spike lifts ONLY that refusal, in-process — every other unported form is still
refused — raises the submit-reply budget to 240 s, records every MZZa6b reply, and then
runs the real CLI. Nothing is committed to `src/`.

Pre-registered reading:

* exit 0 and a clip on disk, MZZa6b reply names a media id -> the gate can go once the
  e2e exists; the observer is fine as is.
* MZZa6b payload null and exit 9 (timeout) -> the #723 diagnosis still holds; the fix is
  the status-poll fall-through. The clip is in Flow's gallery and was billed.
* refused before submit (e.g. chip verification) -> a different defect; nothing billed.

Cost: one generation at the chosen model/duration, billed in Veo credits.

    uv run python scripts/dev/spike_migrated_character_video.py -- \
        video t2v "Nora says hello" --reference-entity <id> --reference-entity-name Nora \
        --project <id> --profile <name> --json
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(_SRC))

from gflow_cli.api.transports import migrated_composer as mc  # noqa: E402
from gflow_cli.api.transports.batchexecute import parse_frames  # noqa: E402

_original_unported = mc._unported_form  # pyright: ignore[reportPrivateUsage]


def _entities_allowed(request: Any) -> str | None:
    """The real gate, judged on the request with its character references removed."""
    if request.reference_entities:
        stripped = dataclasses.replace(request, reference_entities=(), reference_entity_names=())
        verdict = _original_unported(stripped)
        print(f"[spike] gate  character refs allowed; rest of request -> {verdict}", flush=True)
        return verdict
    return _original_unported(request)


mc._unported_form = _entities_allowed  # pyright: ignore[reportPrivateUsage]
mc.SUBMIT_REPLY_BUDGET_S = 240.0

# Record every MZZa6b / YhhmEf reply body the page receives.
_original_submit = mc.MigratedComposer.submit_and_observe


async def _recording_submit(self: Any, page: Any, **kwargs: Any) -> Any:
    async def on_response(resp: Any) -> None:
        if "batchexecute" not in resp.url:
            return
        try:
            text = await resp.text()
        except Exception:  # noqa: BLE001 - an aborted body is not ours
            return
        for rid, payload in parse_frames(text):
            if rid in {"MZZa6b", "YhhmEf", "eb1hJf", "jwpduf"}:
                print(f"[spike] reply {rid}: {json.dumps(payload)[:240]}", flush=True)
        if "MZZa6b" in text and '"MZZa6b",null' in text.replace(" ", ""):
            print("[spike] reply MZZa6b: NULL payload (the #723 shape)", flush=True)

    page.on("response", on_response)
    try:
        return await _original_submit(self, page, **kwargs)
    finally:
        page.remove_listener("response", on_response)


mc.MigratedComposer.submit_and_observe = _recording_submit  # type: ignore[method-assign]

if __name__ == "__main__":
    from gflow_cli.cli import main

    args = sys.argv[1:]
    if args and args[0] == "--":
        args = args[1:]
    main(args=args, prog_name="gflow")
