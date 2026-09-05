"""Which rpc fires when a reference is attached? — passive, no submit, $0.

Records every ``batchexecute`` POST the migrated editor makes, stamped with the phase it
fired in, across: idle → `@` typed → "Add to prompt" clicked → prompt typed. **Nothing is
submitted**, so unlike the submit-payload probe there is not even a theoretical path to
spending a credit.

**Why this exists.** The attach recon
(`docs/superpowers/spikes/2026-09-05-migrated-r2v-attach-surface.md`) established that
references attach through an `@` picker, captured its selectors, and then failed four
times to make anything stick — clicking an asset dismisses the picker, and clicking
"Add to prompt" leaves the composer empty. So no submit payload could be produced, and the
question that gates the whole r2v port — *does the confirm attach an entity server-side,
or only write prompt text?* — stayed open.

But the traffic already carries the answer. Two rpcids, ``UpteDb`` and ``DTaVef``, showed
up in every capture and are unaccounted for. If one of them fires **on the confirm click**
carrying an asset id, the attach is server-side, the composer being empty is a red herring,
and the port asserts the rpc rather than scraping the prompt box. If none fires, the
mechanism really is prompt-text and the port has to defend against a run that silently
generates without its references.

Phase stamping is the whole point: an rpc that also fires while idle proves nothing.

    uv run python scripts/dev/capture_migrated_attach_rpcs.py <profile> <project-id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    COMPOSER,
    MigratedComposer,
)

from _spike_common import build_client, resolve_profile_dir  # noqa: E402, isort: skip


def _stage(msg: str) -> None:
    print(f"[stage] {msg}", file=sys.stderr, flush=True)


def _decode(post_data: str | None) -> dict[str, Any]:
    """batchexecute posts ``f.req=[[[rpcid, "<json string>", ...]]]``, form-encoded."""
    if not post_data:
        return {"rpcid": None}
    try:
        req = urllib.parse.parse_qs(post_data).get("f.req", [None])[0]
        if not req:
            return {"rpcid": None, "raw": post_data[:400]}
        inner = json.loads(req)[0][0]
        rpcid, payload = inner[0], inner[1]
        return {"rpcid": rpcid, "payload": json.loads(payload) if payload else None}
    except Exception as exc:  # noqa: BLE001 — observation only
        return {"rpcid": "<undecodable>", "error": str(exc)[:120], "raw": post_data[:400]}


async def _composer_state(page: Any) -> dict[str, Any]:
    """ALL contenteditables, not just the first.

    "The composer is empty" was read off `document.querySelector` — the FIRST match. With
    the picker open there may be more than one contenteditable (the picker carries its own
    search box), in which case that reading says nothing about where the text went. Report
    every one, plus which holds focus, so the conclusion cannot rest on the wrong element.
    """
    return await page.evaluate(
        """() => {
            const all = [...document.querySelectorAll("[contenteditable='true']")];
            return {
                count: all.length,
                nodes: all.map((c, i) => ({
                    i,
                    focused: document.activeElement === c,
                    cls: (c.className || '').toString().slice(0, 60),
                    text: (c.textContent || '').trim().slice(0, 100),
                    html: (c.innerHTML || '').slice(0, 200),
                })),
                active_element: document.activeElement
                    ? document.activeElement.tagName.toLowerCase()
                      + '.' + (document.activeElement.className || '').toString().slice(0, 40)
                    : null,
            };
        }"""
    )


async def _probe(page: Any, project_id: str) -> dict[str, Any]:
    composer = MigratedComposer()
    phase = {"now": "startup"}
    calls: list[dict[str, Any]] = []

    def _on_request(request: Any) -> None:
        if "data/batchexecute" not in request.url or request.method != "POST":
            return
        decoded = _decode(request.post_data)
        calls.append({"phase": phase["now"], "rpcid": decoded.get("rpcid"), "decoded": decoded})

    page.on("request", _on_request)

    await composer.ensure_editor(page, project_id)

    pane = await composer._open_pane(page)  # noqa: SLF001 — dev instrument
    await composer._select(page, pane, axis="mode", lig="videocam")  # noqa: SLF001
    await composer._select(page, pane, axis="submode", lig="chrome_extension")  # noqa: SLF001
    await composer._close_pane(page, strict=False)  # noqa: SLF001

    # Baseline: what fires when nothing is happening. An rpc seen here is noise.
    phase["now"] = "idle"
    _stage("idle baseline (6s)")
    await page.wait_for_timeout(6000)

    phase["now"] = "after_at"
    # `keyboard.type`, NOT `insert_text`. Production's send_prompt uses insert_text on
    # purpose (a newline must not submit), but that dispatches input events with no real
    # keystrokes — a ProseMirror mention plugin opens on the character and then has no
    # query to track, which is the best explanation for the picker appearing while the
    # `@` never lands and Enter does nothing.
    _stage("typing @ with real key events")
    await page.locator(COMPOSER).first.click(timeout=5000)
    await page.keyboard.type("@", delay=120)
    await page.wait_for_timeout(2000)
    # Every asset the Recent list offers here is a Video (this project has generated
    # nothing else) plus one Avatar. If Ingredients rejects a video, Enter clearing the
    # picker is a REJECTION, not a broken gesture — so filter to the Avatar and retry.
    await page.keyboard.type("Me", delay=120)
    await page.wait_for_timeout(2500)
    report_at = await _composer_state(page)

    report: dict[str, Any] = {
        "composer_after_at": report_at,
        "assets_offered": [
            t.strip()[:50] for t in await page.locator("button.asset-item").all_text_contents()
        ][:8],
        "active_without_click": await page.locator("button.asset-item.asset-item-active").count(),
    }

    # A mention picker is normally keyboard-driven, and the confirm click turned out to
    # be a no-op (no rpc, no composer change). Try the standard ProseMirror interaction
    # before concluding anything about the mechanism.
    # ArrowDown then Enter — the gesture the account owner confirmed works by hand. A
    # mouse click works for a human too, but a synthetic Playwright click reads as a
    # click-away and dismisses the overlay, which is what every earlier attempt hit.
    phase["now"] = "after_arrowdown"
    _stage("ArrowDown")
    await page.keyboard.press("ArrowDown")
    # ArrowDown fires UpteDb — the highlighted asset's detail load. Enter sent before that
    # settles was the previous run's failure, so wait it out rather than racing it.
    await page.wait_for_timeout(3500)
    report["active_after_arrowdown"] = await page.locator(
        "button.asset-item.asset-item-active"
    ).count()
    report["composer_after_arrowdown"] = await _composer_state(page)

    phase["now"] = "after_arrowdown_enter"
    _stage("Enter")
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(3500)
    report["composer_after_asset_click"] = await _composer_state(page)
    report["active_after_click"] = await page.locator(
        "button.asset-item.asset-item-active"
    ).count()

    phase["now"] = "after_confirm_click"
    confirm = page.locator("button.detail-add-to-prompt-btn")
    report["confirm_found"] = bool(await confirm.count())
    if await confirm.count():
        _stage("clicking 'Add to prompt'")
        await confirm.first.click(timeout=5000)
        await page.wait_for_timeout(3000)
    report["composer_after_confirm"] = await _composer_state(page)

    phase["now"] = "after_prompt_typed"
    _stage("typing a prompt")
    await page.locator(COMPOSER).first.click(timeout=5000)
    await page.keyboard.insert_text("a man crying")
    await page.wait_for_timeout(2500)
    report["composer_after_prompt"] = await _composer_state(page)

    phase["now"] = "done"
    by_phase: dict[str, list[str]] = {}
    for c in calls:
        by_phase.setdefault(c["phase"], []).append(c["rpcid"] or "?")
    report["rpcids_by_phase"] = by_phase
    # Only rpcs that fired on the confirm and NOT while idle can be the attach.
    idle = {c["rpcid"] for c in calls if c["phase"] == "idle"}
    report["confirm_only_rpcids"] = sorted(
        {c["rpcid"] for c in calls if c["phase"] == "after_confirm_click"} - idle - {None}
    )
    report["confirm_payloads"] = [
        c["decoded"] for c in calls if c["phase"] == "after_confirm_click"
    ]
    report["note"] = "NOTHING SUBMITTED — no submit click exists in this probe"
    return report


async def _main(profile: str, project_id: str, out_path: str) -> int:
    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 — dev instrument
        assert page is not None
        report = await _probe(page, project_id)
        Path(out_path).write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        _stage(f"report written to {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile")
    parser.add_argument("project_id")
    parser.add_argument("--out", default="attach_rpcs.json")
    args = parser.parse_args()
    return asyncio.run(_main(args.profile, args.project_id, args.out))


if __name__ == "__main__":
    raise SystemExit(main())
