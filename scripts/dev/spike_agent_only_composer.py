r"""How does #799's agent-only composer take a prompt, and how does its output surface?

Cost: phases `inventory` and `settings` are $0 (navigation, DOM reads, open/close the
Settings pane WITHOUT saving). `--submit-image` submits ONE image prompt: daily image
quota, zero credits. `--submit-video` submits ONE 4 s video and APPROVES the credit gate
(7 credits measured 2026-09-15); `--approve-pending` approves a gate already in the chat
instead of typing a new request. Nothing is deleted; generated media stays in the project.

    python scripts/dev/spike_agent_only_composer.py --profile <name> \
        --project <flow.google.com project id> [--submit-image | --submit-video [--approve-pending]]

Pre-registered readings (written before the first run):

| Observation | Reading |
|---|---|
| composer is a ProseMirror contenteditable, `Start generation` button present | the typing/submit path is ProseMirror, not labs' Slate — `send_prompt` must be ported |
| Settings pane shows `[role=tab]`/radio controls for aspect + count per image/video section | per-request settings are drivable through the pane (Save required) |
| Settings pane controls are not tabs/radios | the labs `_count_tabs_locator` cannot be reused; record what they are |
| after submit, a confirm-before-generating control appears | driver must approve (or set "Never" in settings) |
| new `<img>` whose src carries a stable media id appears within 180 s | count-delta scraping by media id carries over to this host |
| no new `<img>` in 180 s, but chat text changed | output surfaces differently; the capture says how — NOT evidence generation is impossible |
| no chat change at all | submit did not land; says nothing about the feature |
"""

from __future__ import annotations

import argparse
import asyncio
import json
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

_INVENTORY_JS = r"""
() => {
  const visible = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const ligs = (el) => [...el.querySelectorAll('mat-icon, i')].map(i => (i.textContent || '').trim()).filter(Boolean);
  const chain = (el) => {
    const out = [];
    for (let n = el; n && n !== document.body && out.length < 6; n = n.parentElement)
      if (n.tagName.includes('-')) out.push(n.tagName.toLowerCase());
    return out;
  };
  const media = [...document.querySelectorAll('img, video, source')].map(e => ({
    tag: e.tagName.toLowerCase(), src: (e.currentSrc || e.src || '').slice(0, 300),
    w: e.naturalWidth || e.videoWidth || 0, h: e.naturalHeight || e.videoHeight || 0,
    chain: chain(e), visible: visible(e),
  }));
  const controls = [...document.querySelectorAll(
    "button, [role=tab], [role=radio], [role=option], [role=switch], [role=combobox], mat-select, mat-button-toggle, input, [contenteditable=true]"
  )].filter(visible).map(e => ({
    tag: e.tagName.toLowerCase(), role: e.getAttribute('role'), aria: e.getAttribute('aria-label'),
    pressed: e.getAttribute('aria-pressed'), selected: e.getAttribute('aria-selected'),
    checked: e.getAttribute('aria-checked'), ligatures: ligs(e),
    classes: [...e.classList].filter(c => !c.startsWith('mat-') && !c.startsWith('mdc-')).slice(0, 4),
    text: (e.innerText || '').trim().slice(0, 40), chain: chain(e),
  }));
  const custom = {};
  for (const e of document.querySelectorAll('*')) {
    const t = e.tagName.toLowerCase(); if (t.includes('-')) custom[t] = (custom[t] || 0) + 1;
  }
  return {
    url: location.href, lang: document.documentElement.lang,
    prosemirror: document.querySelectorAll('.ProseMirror').length,
    agent_chip: document.querySelectorAll('button.agent-mode-chip').length,
    dialogs: [...document.querySelectorAll('[role=dialog], [role=alert], [aria-live]')].filter(visible)
      .map(d => ({ role: d.getAttribute('role'), live: d.getAttribute('aria-live'), text: (d.innerText || '').slice(0, 300) })),
    chat_text: [...document.querySelectorAll('[class*=message], [class*=turn], [class*=chat]')]
      .filter(visible).map(e => (e.innerText || '').trim()).filter(Boolean).slice(-8).map(t => t.slice(0, 400)),
    media, controls, custom_tags: custom,
  };
}
"""

#: The credit gate, measured 2026-09-15: `flow-permission-message` holds a
#: `div[role=radiogroup]` of `div[role=radio].option-row`s — Approve (`check`), Always approve
#: (`check`), Reject (`close`), in that order. Answered or superseded gates stay in the chat
#: with `.read-only`, and the session survives a reload, so only a live row is a gate.
APPROVE_OPTION = (
    "flow-permission-message div[role='radio'].option-row:not(.read-only)"
    ":has(mat-icon:text-is('check'))"
)

#: Full outline of the LAST chat bubble: every element with its tag, role, classes, and
#: its own direct text. Anchor-free on purpose — the first two gate detectors missed.
_GATE_JS = r"""
() => {
  const b = [...document.querySelectorAll('flow-chat-bubble')].pop();
  if (!b) return null;
  const own = (e) => [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(' ').slice(0, 60);
  return [...b.querySelectorAll('*')].map(e => ({
    tag: e.tagName.toLowerCase(), role: e.getAttribute('role'), aria: e.getAttribute('aria-label'),
    classes: [...e.classList].slice(0, 5), text: own(e), fonticon: e.getAttribute('fonticon'),
    clickable: getComputedStyle(e).cursor === 'pointer',
  })).filter(x => x.text || x.role || x.clickable || x.tag.includes('-'));
}
"""


def _tap_network(page: Any, findings: dict[str, Any]) -> None:
    """Record every batchexecute rpcid, and run the classic driver's decoders on the ones it
    already reads. Question: does the agent composer fire the same RPCs, so the wire (not the
    DOM) can stay the completion signal? Media ids only — no bodies, no URLs are kept."""
    from gflow_cli.api.transports.batchexecute import (
        generation_record,
        image_records,
        parse_frames,
    )
    from gflow_cli.api.transports.migrated_composer import (
        IMAGE_SUBMIT_RPC,
        STATUS_RPCS,
        SUBMIT_RPCS,
    )

    known = {IMAGE_SUBMIT_RPC, *SUBMIT_RPCS, *STATUS_RPCS}
    calls: list[dict[str, Any]] = findings.setdefault("rpc_calls", [])
    t0 = time.monotonic()

    async def on_response(response: Any) -> None:
        url = str(response.url)
        if "batchexecute" not in url:
            return
        rpcids = url.split("rpcids=", 1)[1].split("&", 1)[0] if "rpcids=" in url else "?"
        entry: dict[str, Any] = {
            "t": round(time.monotonic() - t0),
            "rpcids": rpcids,
            "status": response.status,
        }
        if known & set(rpcids.split(",")):
            try:
                decoded: list[str] = []
                for rpcid, payload in parse_frames(await response.text()):
                    try:
                        if rpcid == IMAGE_SUBMIT_RPC:
                            decoded += [
                                f"image:{r.media_id}" for r in image_records(rpcid, payload)
                            ]
                        elif rpcid in known:
                            rec = generation_record(rpcid, payload)
                            decoded.append(f"gen:{rec.media_id}:{rec.status}")
                    except Exception as exc:  # noqa: BLE001 - the decode failure IS the finding
                        decoded.append(f"{rpcid}:undecodable:{type(exc).__name__}")
                entry["decoded"] = decoded
            except Exception as exc:  # noqa: BLE001
                entry["decoded"] = [f"body_unreadable:{type(exc).__name__}"]
        calls.append(entry)

    page.on("response", on_response)


GENERATE = "button:has(mat-icon:text-is('arrow_forward'))"
SETTINGS = "button:has(mat-icon:text-is('tune'))"
BACK = "button:has(mat-icon:text-is('arrow_back'))"


def _in_a2ui(control: dict[str, Any]) -> bool:
    """Rendered by the agent inside a chat reply (not the prompt box or page chrome)."""
    return any("a2ui" in tag for tag in control["chain"])


class ProbeFailedError(RuntimeError):
    """A step this probe cannot complete. Never downgraded to a verdict."""


async def _snap(page: Any, tag: str, findings: dict[str, Any]) -> dict[str, Any]:
    inv = await page.evaluate(_INVENTORY_JS)
    shot = default_out_path(f"spike_agent_only_{tag}", ".png")
    await page.screenshot(path=str(shot))
    inv["screenshot"] = shot.name
    findings[tag] = inv
    step(
        tag,
        f"controls={len(inv['controls'])} media={len(inv['media'])} "
        f"prosemirror={inv['prosemirror']} chip={inv['agent_chip']} dialogs={len(inv['dialogs'])}",
    )
    return inv


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    submit = ap.add_mutually_exclusive_group()
    submit.add_argument(
        "--submit-image", action="store_true", help="spend one image (quota, 0 credits)"
    )
    submit.add_argument(
        "--submit-video", action="store_true", help="spend one short video (CREDITS)"
    )
    ap.add_argument(
        "--approve-pending",
        action="store_true",
        help="with --submit-video: do not type; approve the gate already on the page",
    )
    ap.add_argument(
        "--prompt", default="a small blue paper boat on a calm pond, soft morning light"
    )
    ap.add_argument("--wait-s", type=float, default=180.0)
    args = ap.parse_args()
    if args.approve_pending and not args.submit_video:
        ap.error("--approve-pending only applies with --submit-video")

    findings: dict[str, Any] = {"project": args.project, "question": "#799 agent-only composer"}
    url = f"https://flow.google.com/project/{args.project}"
    async with build_client(resolve_profile_dir(args.profile)) as client:
        page = await client._context.new_page()  # noqa: SLF001 - spike reads the live context
        _tap_network(page, findings)
        step("goto", url)
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(8_000)
        if "flow.google.com/project/" not in page.url:
            raise ProbeFailedError(f"did not land on a project page (got {page.url})")

        await _snap(page, "inventory", findings)

        # --- Settings pane: open, read, leave WITHOUT saving -----------------------
        try:
            await page.locator(SETTINGS).last.click(timeout=8_000)
            await page.wait_for_timeout(2_000)
            await _snap(page, "settings_open", findings)
            backs = page.locator(BACK)
            if await backs.count():
                await backs.last.click(timeout=5_000)
            else:
                await page.keyboard.press("Escape")
            await page.wait_for_timeout(1_500)
            await _snap(page, "settings_closed", findings)
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed silently
            findings["settings_error"] = str(exc)[:400]
            step("settings", f"FAILED: {str(exc)[:200]}")

        if args.submit_image or args.submit_video:
            video = args.submit_video
            directive = (
                f"Make me a 4 second video of {args.prompt} in a 9:16 aspect ratio."
                if video
                else f"Make me a picture of {args.prompt} in a 9:16 aspect ratio."
            )
            findings["directive"] = directive
            before = await _snap(page, "before_submit", findings)
            before_srcs = {m["src"] for m in before["media"]}
            before_a2ui = {json.dumps(c, sort_keys=True) for c in before["controls"] if _in_a2ui(c)}
            if not args.approve_pending:
                editor = page.locator(".ProseMirror").last
                await editor.click(timeout=8_000)
                await page.keyboard.insert_text(directive)
                await page.wait_for_timeout(500)
                await page.locator(GENERATE).last.click(timeout=8_000)
            t0 = time.monotonic()
            findings["timeline"] = []
            gate_clicked = False
            # Written every poll: a stopped run must not lose what it already saw.
            partial = default_out_path("spike_agent_only_composer_partial")
            while time.monotonic() - t0 < args.wait_s:
                await page.wait_for_timeout(5_000)
                inv = await page.evaluate(_INVENTORY_JS)
                elapsed = round(time.monotonic() - t0)
                new = [m for m in inv["media"] if m["src"] and m["src"] not in before_srcs]
                in_flight = await page.locator(".stop-button").count()
                # The confirm-before-generating gate. Observed 2026-09-15 (first video run):
                # the agent renders Approve / Always approve / Reject as an option list in
                # its reply — NOT <button>s, so the first version of this probe never saw
                # it. Anchor on the `check` ligature inside the a2ui renderer instead.
                gate = [
                    c
                    for c in inv["controls"]
                    if _in_a2ui(c) and json.dumps(c, sort_keys=True) not in before_a2ui
                ]
                findings.setdefault("gate_dom", await page.evaluate(_GATE_JS))
                findings["timeline"].append(
                    {
                        "t": elapsed,
                        "in_flight": in_flight,
                        "new_media": new[:12],
                        "gate_buttons": gate,
                        "chat_tail": inv["chat_text"][-3:],
                    }
                )
                step(
                    "poll",
                    f"t={elapsed}s in_flight={in_flight} new_media={len(new)} gate={len(gate)}",
                )
                partial.write_text(json.dumps(findings, indent=2), encoding="utf-8")
                approve = page.locator(APPROVE_OPTION)
                if not gate_clicked and await approve.count():
                    findings["gate_dom"] = await page.evaluate(_GATE_JS)
                    await _snap(page, "gate_seen", findings)
                    step("gate", f"approve options={await approve.count()}; clicking the first")
                    await approve.first.click(timeout=8_000)
                    gate_clicked = True
                    findings["gate_clicked_at_s"] = elapsed
                    continue
                if not gate_clicked and not in_flight and not new and elapsed >= 25:
                    # The turn ended with neither an approve anchor we recognise nor
                    # media: record the reply's full outline and stop spending time.
                    findings["idle_reply_outline"] = await page.evaluate(_GATE_JS)
                    await _snap(page, "idle_reply", findings)
                    step("idle", "turn idle, no approve anchor matched, no media: outline saved")
                    break
                wanted = (
                    [m for m in new if (m["tag"] in ("video", "source") or "/video/" in m["src"])]
                    if video
                    else [m for m in new if m["tag"] == "img"]
                )
                if wanted and not in_flight and elapsed > 20:
                    await page.wait_for_timeout(5_000)
                    break
            await _snap(page, "after_submit", findings)

    out = default_out_path("spike_agent_only_composer")
    out.write_text(json.dumps(findings, indent=2), encoding="utf-8")
    step("done", f"wrote {out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except ProbeFailedError as exc:
        print(f"[spike] PROBE FAILED: {exc}", file=sys.stderr, flush=True)
        sys.exit(3)
