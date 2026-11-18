r"""Why does an entity-carrying submit never reply, when a media one does? (2 clips)

`attach_character_entities` works: the chip commits with the right `data-entity-id`, Flow
fetches the character's voice sample, and a route-aborted capture showed the id riding the
``MZZa6b`` payload in its own reference slot. Then the submit produces **no**
``YhhmEf``/``eb1hJf``/``MZZa6b`` reply within 60 s. Three CLI runs, varying submode and
model, all identical.

Nothing in the CLI can see why, for two reasons:

  * it only listens for the submit rpcids, so a reply on any OTHER rpcid — which is where
    a server-side rejection would arrive — is discarded unread; and
  * the video path parks the page at ``about:blank`` before the incident capture runs
    (#722), so every bundle ships an empty DOM and a blank screenshot.

So this probe keeps BOTH halves: every ``batchexecute`` request body **and** every
response body in the window, whatever the rpcid, plus the composer's own DOM at submit
time.

**The design is an A/B in ONE session**, because a single failing run cannot tell a broken
entity from a broken page. Arm A attaches a MEDIA reference — measured working, six
generations. Arm B attaches the CHARACTER ENTITY. Same page, same prompt, same settings,
seconds apart. Whatever differs between the two payloads is the answer.

Hypotheses this separates (the one it cannot is noted at the end):

  1. Flow rejects and says so, on a non-submit rpcid  -> arm B's responses carry an error
  2. the request never leaves                          -> arm B has no submit request at all
  3. the payload is malformed vs arm A's               -> the diff shows it
  4. Flow accepts and is merely slow                   -> arm B replies after the CLI's 60 s

**COST: up to two real generations.** Nothing is aborted — the whole point is to see what
Flow actually answers. Run it on a funded profile.

    python scripts/dev/spike_migrated_entity_submit.py \
        --profile ffroliva --project <uuid> \
        --entity-id <uuid> --entity-name Kael --media-name kael_ref
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    COMPOSER,
    MigratedComposer,
)
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel  # noqa: E402

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402, isort: skip

PROMPT = (
    "A lone desert traveller on a high ridge at golden hour, full-frame 16:9 image "
    "filling the entire frame edge to edge, no bars, no border."
)
#: How long to keep listening after the click. Deliberately longer than the CLI's 60 s
#: budget, so "Flow is merely slow" is separated from "Flow never answers".
LISTEN_S = 150.0


def _rpcid(url: str) -> str | None:
    m = re.search(r"[?&]rpcids=([A-Za-z0-9]+)", url)
    return m.group(1) if m else None


class Recorder:
    """Every batchexecute request and response in the window, whatever the rpcid."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.responses: list[dict[str, Any]] = []
        self.armed = False

    def on_request(self, request: Any) -> None:
        url = str(getattr(request, "url", ""))
        if not self.armed or "batchexecute" not in url:
            return
        try:
            body = request.post_data
        except Exception:  # noqa: BLE001 - an undecodable body is itself worth recording
            body = None
        self.requests.append(
            {"t": round(time.monotonic(), 2), "rpcid": _rpcid(url), "body": (body or "")[:6000]}
        )

    async def on_response(self, response: Any) -> None:
        url = str(getattr(response, "url", ""))
        if not self.armed or "batchexecute" not in url:
            return
        try:
            text = await response.text()
        except Exception:  # noqa: BLE001 - a streamed/aborted body is not ours to read
            text = ""
        self.responses.append(
            {
                "t": round(time.monotonic(), 2),
                "rpcid": _rpcid(url),
                "status": getattr(response, "status", None),
                "body": text[:6000],
            }
        )


async def _arm(
    page: Any,
    composer: MigratedComposer,
    rec: Recorder,
    *,
    label: str,
    attach: Any,
) -> dict[str, Any]:
    """One submit, fully observed. `attach` is an awaitable that puts the reference on."""
    step(label, "clearing composer")
    await composer.clear_composer(page)
    await attach()
    chips = await composer.read_chips(page)
    step(label, f"chips: {json.dumps(chips)}")
    await composer.send_prompt(page, PROMPT, append=True)

    composer_html = await page.evaluate(
        "() => ((document.querySelector(\"[contenteditable='true']\") || {}).innerHTML || '')"
    )
    rec.requests.clear()
    rec.responses.clear()
    rec.armed = True
    t0 = time.monotonic()

    submit = page.locator("button").filter(
        has=page.locator("mat-icon").filter(has_text=re.compile(r"^\s*arrow_forward\s*$"))
    ).first
    found = bool(await submit.count())
    enabled = bool(found and await submit.is_enabled())
    step(label, f"submit found={found} enabled={enabled}")
    if found and enabled:
        await submit.click(timeout=5000)
        step(label, "submit clicked; listening")
        deadline = time.monotonic() + LISTEN_S
        while time.monotonic() < deadline:
            await page.wait_for_timeout(2000)
            if any(r["rpcid"] in {"YhhmEf", "eb1hJf", "MZZa6b"} for r in rec.responses):
                step(label, f"submit REPLY seen after {time.monotonic() - t0:.0f}s")
                break
    rec.armed = False

    # What the page shows now — a rejection usually says so on screen, and the CLI never
    # looks because it has already parked the page (#722).
    visible = await page.evaluate(
        "() => (document.body.innerText || '').split('\\n')"
        ".map(s => s.trim()).filter(Boolean).slice(-25)"
    )
    return {
        "chips": chips,
        "composer_html": composer_html,
        "submit_found": found,
        "submit_enabled": enabled,
        "requests": list(rec.requests),
        "responses": list(rec.responses),
        "page_tail": visible,
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--entity-id", required=True)
    ap.add_argument("--entity-name", required=True)
    ap.add_argument("--media-name", required=True, help="An existing media asset in the project.")
    args = ap.parse_args()

    findings: dict[str, Any] = {k: v for k, v in vars(args).items()}
    composer = MigratedComposer()
    rec = Recorder()

    async with build_client(resolve_profile_dir(args.profile)) as client:
        page = client._page  # noqa: SLF001 - dev instrument
        assert page is not None
        page.on("request", rec.on_request)
        page.on("response", rec.on_response)

        await composer.ensure_editor(page, args.project)
        # Ingredients + a reference-capable model, identical for both arms.
        await composer.apply_video_settings(
            page,
            GenerateVideoRequest(
                prompt=PROMPT,
                mode=Mode.R2V,
                aspect=Aspect.LANDSCAPE,
                model=VideoModel.OMNI_FLASH,
                reference_images=(),
                ref_names=(args.media_name,),
            ),
        )

        # --- ARM A: a media reference. Measured working; the control. ----------------
        async def attach_media() -> None:
            await composer._mention_by_name(page, args.media_name, expect_chips=1)  # noqa: SLF001

        findings["arm_a_media"] = await _arm(
            page, composer, rec, label="A/media", attach=attach_media
        )

        # --- ARM B: the character entity. The failing case. --------------------------
        async def attach_entity() -> None:
            await composer.attach_character_entities(
                page,
                entity_ids=(args.entity_id,),
                names=(args.entity_name,),
                clear=False,
            )

        findings["arm_b_entity"] = await _arm(
            page, composer, rec, label="B/entity", attach=attach_entity
        )

    out = default_out_path("spike_migrated_entity_submit")
    out.write_text(json.dumps(findings, indent=2), encoding="utf-8")

    for arm in ("arm_a_media", "arm_b_entity"):
        a = findings[arm]
        subs = [r for r in a["responses"] if r["rpcid"] in {"YhhmEf", "eb1hJf", "MZZa6b"}]
        step(
            arm,
            f"requests={len(a['requests'])} responses={len(a['responses'])} "
            f"submit_replies={len(subs)} rpcids_out="
            f"{sorted({r['rpcid'] for r in a['requests'] if r['rpcid']})}",
        )
    step("done", f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
