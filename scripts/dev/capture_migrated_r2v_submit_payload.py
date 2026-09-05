"""Does "Add to prompt" attach a reference, or only write text? — route-aborted, $0.

Drives the real migrated composer: Ingredients sub-mode, an `@` mention picked from the
composer's own picker, "Add to prompt", a prompt, then **submit with the generate request
aborted**, and prints what the page was about to send.

**Why this exists.** The 2026-09-05 attach recon
(`2026-09-05-migrated-r2v-attach-surface.md`) established that references on the migrated
host attach through an `@` picker in the composer, not through labs-shaped slots. It did
NOT establish what the confirm does. Two possibilities, and they demand different ports:

* "Add to prompt" **attaches an entity** — the submit carries reference ids, and a port can
  assert them the way the labs backstop `_assert_entities_attached` does; or
* it only **inserts prompt text** — the submit carries a decorated prompt and nothing else,
  in which case an r2v run could silently generate an *unreferenced* clip and the port has
  to defend against exactly that.

Nothing short of the outgoing payload distinguishes them, and
[[credit-free-route-abort-verification]] is how this repo reads one without paying.

**Credit safety.** The labs technique routes `video:batchAsyncGenerateVideo*`; this host
submits over `batchexecute` (rpcid ``YhhmEf``), so the filter had to change. It is
deliberately **blunt**: from the moment the route is armed — immediately before the submit
click — *every* `batchexecute` POST is aborted, not just the one matching the rpcid. A
missed filter here costs a real generation, and nothing else on the page needs to succeed
after the click. The run is torn down straight after.

    uv run python scripts/dev/capture_migrated_r2v_submit_payload.py <profile> <project-id>
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
    _ligature,
)

from _spike_common import build_client, resolve_profile_dir  # noqa: E402, isort: skip

PROMPT = "a man crying"
#: Filters the mention picker. "Me" resolved to a real entity on the probe account.
QUERY = "Me"


def _stage(msg: str) -> None:
    print(f"[stage] {msg}", file=sys.stderr, flush=True)


async def _pick_first_reference(page: Any) -> dict[str, Any]:
    """Insert a mention chip, using the gesture that actually works.

    Two things had to be right, both learned the hard way:
      * `keyboard.type`, NOT `insert_text` — the latter dispatches input events with no
        real keystrokes, so the ProseMirror mention plugin opens a picker with no query
        behind it and every later gesture operates on dead state.
      * a typed query, not a click — a synthetic click on an asset reads as a click-away
        and dismisses the overlay (a human mouse works; Playwright's does not).
    """
    out: dict[str, Any] = {}
    await page.locator(COMPOSER).first.click(timeout=5000)
    await page.keyboard.type("@", delay=120)
    await page.wait_for_timeout(2000)
    await page.keyboard.type(QUERY, delay=120)
    await page.wait_for_timeout(3000)

    out["chips"] = await page.evaluate(
        """() => [...document.querySelectorAll('.mention-chip')].map(c => ({
            text: (c.textContent || '').trim(),
            mention_id: c.getAttribute('data-mention-id'),
            entity_id: c.getAttribute('data-entity-id'),
            reference_type: c.getAttribute('data-reference-type'),
        }))"""
    )
    _stage(f"chips in composer: {len(out['chips'])}")
    out["composer_html"] = await page.evaluate(
        "() => ((document.querySelector(\"[contenteditable='true']\") || {}).innerHTML || '')"
        ".slice(0, 1200)"
    )
    return out


def _decode(post_data: str | None) -> Any:
    """batchexecute posts `f.req=[[[rpcid, "<json string>", ...]]]` form-encoded."""
    if not post_data:
        return None
    try:
        form = urllib.parse.parse_qs(post_data)
        req = form.get("f.req", [None])[0]
        if not req:
            return {"raw": post_data[:2000]}
        outer = json.loads(req)
        inner = outer[0][0]
        rpcid, payload = inner[0], inner[1]
        return {"rpcid": rpcid, "payload": json.loads(payload) if payload else None}
    except Exception as exc:  # noqa: BLE001 — observation only
        return {"decode_error": f"{type(exc).__name__}: {exc}", "raw": post_data[:2000]}


async def _probe(page: Any, project_id: str) -> dict[str, Any]:
    composer = MigratedComposer()
    await composer.ensure_editor(page, project_id)

    # Playwright request INTERCEPTION is not usable here: `page.route()` never returned
    # within 20 s on this page, idle or busy (measured, four runs). The credit-safe lever
    # instead is the network itself — a passive request listener records the payload, and
    # the context is taken OFFLINE immediately before the submit click, so the request is
    # attempted, recorded, and fails locally without ever reaching Google.
    captured: list[dict[str, Any]] = []

    def _on_request(request: Any) -> None:
        if "data/batchexecute" not in request.url or request.method != "POST":
            return
        captured.append({"url": request.url[:120], "decoded": _decode(request.post_data)})

    page.on("request", _on_request)
    _stage("request listener attached")

    _stage("editor ready; opening pane")
    pane = await composer._open_pane(page)  # noqa: SLF001 — dev instrument
    await composer._select(page, pane, axis="mode", lig="videocam")  # noqa: SLF001
    await composer._select(page, pane, axis="submode", lig="chrome_extension")  # noqa: SLF001
    await composer._close_pane(page, strict=False)  # noqa: SLF001
    # `_close_pane` counts `.cdk-overlay-pane` only. A `.cdk-overlay-backdrop` can outlive
    # it and still intercept pointer events — one run died with "backdrop ... intercepts
    # pointer events" on the composer click. Same class as the #669 overlay bug; worked
    # around here, worth a look in production.
    for _ in range(10):
        if not await page.locator(".cdk-overlay-backdrop").count():
            break
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
    _stage(f"backdrops left: {await page.locator('.cdk-overlay-backdrop').count()}")

    _stage("submode set; picking reference")
    report: dict[str, Any] = {"reference": await _pick_first_reference(page)}

    await page.locator(COMPOSER).first.click(timeout=5000)
    await page.keyboard.insert_text(f" {PROMPT}")
    await page.wait_for_timeout(400)

    # Resolve the submit button BEFORE arming: with every batchexecute aborted the page
    # is degraded, and a locator resolved in that state was where the previous two runs
    # stopped producing output.
    _stage("resolving submit button (pre-arm)")
    submit = page.locator("button").filter(has=_ligature(page, "arrow_forward")).first
    report["submit_found"] = bool(await asyncio.wait_for(submit.count(), timeout=20))
    _stage(f"submit_found={report['submit_found']}; ARMING route abort")

    context = page.context
    await context.set_offline(True)
    _stage("context OFFLINE; clicking submit (request cannot reach Google)")
    try:
        if report["submit_found"]:
            await asyncio.wait_for(submit.click(timeout=5000), timeout=25)
            _stage("submit clicked; waiting for the aborted request")
            await page.wait_for_timeout(5000)
    finally:
        # unroute can block on in-flight handlers; the capture is already in `captured`
        # and is written out by the caller before teardown, so a hang here costs nothing.
        await context.set_offline(False)
        _stage("back online")

    report["captured_requests"] = len(captured)
    report["submits"] = [c for c in captured if (c["decoded"] or {}).get("rpcid") == "YhhmEf"]
    report["other_rpcids"] = sorted(
        {(c["decoded"] or {}).get("rpcid") for c in captured} - {"YhhmEf", None}
    )
    report["note"] = "generate request ABORTED — no credit spent"
    return report


async def _main(profile: str, project_id: str, out_path: str) -> int:
    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 — dev instrument
        assert page is not None
        report = await _probe(page, project_id)
        # Written INSIDE the context: the previous run reached the capture and then hung
        # in teardown, losing it. The file is the deliverable; stdout is a convenience.
        out = Path(out_path)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        _stage(f"report written to {out}")
    print(json.dumps(report, indent=2, ensure_ascii=False)[:12000])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile")
    parser.add_argument("project_id")
    parser.add_argument("--out", default="r2v_payload.json")
    args = parser.parse_args()
    return asyncio.run(_main(args.profile, args.project_id, args.out))


if __name__ == "__main__":
    raise SystemExit(main())
