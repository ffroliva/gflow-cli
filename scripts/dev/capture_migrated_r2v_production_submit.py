"""Why did a real `gflow video r2v` submit go out with a **t2v** model key? — $0.

Drives the **production** r2v path — `apply_video_settings`, `attach_references`,
`send_prompt(append=True)` — with the caller's own model and reference files, then blocks
the submit inside the page and dumps the decoded body the app was about to send.

**Why this exists.** A live run on 2026-09-06 attached two references (chip count verified
twice: `migrated.references_attached count=2`, and the pre-submit re-read) and still
submitted `MZZa6b` carrying `veo_3_1_t2v_lite_4s_low_priority`. Three readings, and they
demand different fixes:

1. **The 4s tier has no r2v variant.** No `--duration` was passed, so `4s` is whatever the
   editor remembered; if ingredients are unavailable at that tier the app degrades to t2v
   and bills a clip with no references. The fix is then a pre-submit capability refusal,
   not a post-submit body assertion — the missing ingredient-capability axis
   `2026-08-14-video-model-capability-matrix.md` already flagged.
2. **The Ingredients submode did not stick.** `apply_video_settings` selects it, and the
   composer's own comment records that the app derives the mode key from that submode —
   the same run sends `veo_3_1_r2v_lite_low_priority` under Ingredients and a mode-less
   key under Frames. A submode that silently reverted produces exactly this.
3. **The cohort simply names r2v keys differently** and the reference ids ride the body
   anyway — in which case the guard's key check is too strict and is refusing a run that
   would have worked.

Two observations separate all three, and this captures both: the **pane read-back** after
settings are applied (which submode is checked, which duration), and the **decoded submit
body** (are the uploaded media ids in it, yes or no).

Run it twice — once inheriting the editor's duration, once with `--duration 8` — and the
pair answers hypothesis 1 on its own.

**Credit safety.** Same mechanism `capture_migrated_r2v_submit_payload.py` proved at
`submits: 0` across every run, and for the same measured reasons: `page.route()` /
`context.route()` never return on this page, and `set_offline(True)` makes the app skip
the request entirely (it checks `navigator.onLine`), so neither can be used. Instead
`window.fetch` and `XMLHttpRequest.send` are wrapped **inside the page** immediately
before the click: a `data/batchexecute` body is recorded and the call never made, so the
request is never created at the network layer at all.

Uploads DO happen (they are how the media ids are minted, exactly as a real run does), so
this adds assets to the project. It spends no video credit.

    uv run python scripts/dev/capture_migrated_r2v_production_submit.py <profile> <project-id> \
        --ref me.jpg --ref other.png --model veo-lite-lp [--duration 8]
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
    MigratedComposer,
    _ligature,
)
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel  # noqa: E402

from _spike_common import build_client, default_out_path, resolve_profile_dir  # noqa: E402, isort: skip

PROMPT = "the presenter from the first reference holds the product from the second"


def _stage(msg: str) -> None:
    print(f"[stage] {msg}", file=sys.stderr, flush=True)


def _decode(post_data: str | None) -> dict[str, Any] | None:
    """`f.req=<percent-encoded JSON>` -> {rpcid, payload}."""
    if not post_data:
        return None
    try:
        fields = urllib.parse.parse_qs(post_data)
        req = (fields.get("f.req") or [""])[0]
        outer = json.loads(req)
        inner = outer[0][0]
        rpcid, payload = inner[0], inner[1]
        return {"rpcid": rpcid, "payload": json.loads(payload) if payload else None}
    except Exception as exc:  # noqa: BLE001 — observation only
        return {"decode_error": f"{type(exc).__name__}: {exc}", "raw": post_data[:2000]}


def _walk_strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        out: list[str] = []
        for item in node:
            out.extend(_walk_strings(item))
        return out
    return []


#: Every radio in the settings pane with its ligature and checked state. Structural only —
#: ligature + `aria-checked`, no display text — so it reads the same in any locale. This is
#: how "was Ingredients actually selected, and at what duration?" is answered without
#: trusting that `_select`'s read-back still held at submit time.
_PANE_STATE_JS = """() => [...document.querySelectorAll(
    ".cdk-overlay-pane [role='radiogroup']"
)].map(g => ({
    radios: [...g.querySelectorAll("[role='radio']")].map(r => ({
        lig: [...r.querySelectorAll('i, mat-icon')].map(i => (i.textContent || '').trim())
             .filter(Boolean).join('|'),
        checked: r.getAttribute('aria-checked'),
        label: (r.textContent || '').trim().slice(0, 24),
    })),
}))"""


async def _pane_state(page: Any, composer: MigratedComposer) -> Any:
    """Reopen the settings pane and read every axis back, then close it."""
    try:
        await composer._open_pane(page)  # noqa: SLF001 — dev instrument
        state = await page.evaluate(_PANE_STATE_JS)
        await composer._close_pane(page, strict=False)  # noqa: SLF001
        return state
    except Exception as exc:  # noqa: BLE001 — observation only
        return {"pane_read_error": f"{type(exc).__name__}: {exc}"}


async def _probe(
    page: Any,
    project_id: str,
    refs: tuple[Path, ...],
    model: str | None,
    duration: int | None,
) -> dict[str, Any]:
    composer = MigratedComposer()
    report: dict[str, Any] = {
        "refs": [p.name for p in refs],
        "model_requested": model,
        "duration_requested": duration,
    }

    request = GenerateVideoRequest(
        prompt=PROMPT,
        mode=Mode.R2V,
        aspect=Aspect.PORTRAIT,
        reference_images=refs,
        model=VideoModel.from_cli(model),
        duration=duration,
    )

    await composer.ensure_editor(page, project_id)
    _stage("editor ready; applying the production settings")
    await composer.apply_video_settings(page, request)
    # Read the pane back AFTER it was applied and closed. Three hypotheses need
    # separating and this is what separates two of them: whether the Ingredients submode
    # actually stuck, and which duration the editor is really on.
    report["pane_after_settings"] = await _pane_state(page, composer)

    _stage("uploading + mentioning the references (production path)")
    media_ids = await composer.attach_references(page, project_id, refs)
    report["media_ids"] = list(media_ids)

    await composer.send_prompt(page, PROMPT, append=True)
    report["chips_before_submit"] = await composer.read_chips(page)
    report["composer_html"] = await page.evaluate(
        "() => ((document.querySelector(\"[contenteditable='true']\") || {}).innerHTML || '')"
        ".slice(0, 1200)"
    )

    # Resolve the submit button BEFORE arming: once every batchexecute is blocked the page
    # is degraded, and a locator resolved in that state is where earlier probes stalled.
    submit = page.locator("button").filter(has=_ligature(page, "arrow_forward")).first
    report["submit_found"] = bool(await asyncio.wait_for(submit.count(), timeout=20))
    if report["submit_found"]:
        report["submit_enabled"] = await submit.is_enabled()

    _stage("blocking in-page fetch/XHR; the submit can no longer reach Google")
    await page.evaluate(
        """() => {
            window.__captured = [];
            const isTarget = (u) => String(u).includes('data/batchexecute');
            const of = window.fetch;
            window.fetch = function (input, init) {
                const url = (input && input.url) || input;
                const body = (init && init.body) || (input && input.body) || null;
                if (isTarget(url)) {
                    window.__captured.push({via: 'fetch', url: String(url),
                                            body: body ? String(body) : null});
                    return Promise.reject(new Error('blocked by probe'));
                }
                return of.apply(this, arguments);
            };
            const oo = XMLHttpRequest.prototype.open;
            const os = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function (m, u) {
                this.__url = u; return oo.apply(this, arguments);
            };
            XMLHttpRequest.prototype.send = function (b) {
                if (isTarget(this.__url)) {
                    window.__captured.push({via: 'xhr', url: String(this.__url),
                                            body: b ? String(b) : null});
                    return;  // never sent
                }
                return os.apply(this, arguments);
            };
        }"""
    )

    captured: list[dict[str, Any]] = []
    try:
        if report["submit_found"]:
            _stage("clicking submit (request cannot be created)")
            await asyncio.wait_for(submit.click(timeout=5000), timeout=25)
            await page.wait_for_timeout(6000)
    finally:
        in_page = await page.evaluate("() => window.__captured || []")
        for c in in_page:
            captured.append({"url": str(c.get("url", ""))[:120], "decoded": _decode(c.get("body"))})
        _stage(f"in-page captures: {len(in_page)}")

    submits = [c for c in captured if (c["decoded"] or {}).get("rpcid") in {"YhhmEf", "MZZa6b"}]
    report["captured_rpcids"] = sorted({str((c["decoded"] or {}).get("rpcid")) for c in captured})
    report["submits"] = submits

    # --- the whole question, answered as a boolean per id ----------------------------
    verdict: dict[str, Any] = {}
    for c in submits:
        payload = (c["decoded"] or {}).get("payload")
        strings = _walk_strings(payload)
        blob = json.dumps(payload, ensure_ascii=False)
        verdict = {
            "rpcid": (c["decoded"] or {}).get("rpcid"),
            "model_keys_in_body": sorted({s for s in strings if s.startswith(("veo_", "abra_"))}),
            "reference_ids_present": {mid: (mid in blob) for mid in media_ids},
            "all_reference_ids_present": all(mid in blob for mid in media_ids),
        }
    report["verdict"] = verdict
    report["note"] = "submit BLOCKED in-page — no video credit spent"
    return report


async def _main(
    profile: str,
    project_id: str,
    refs: tuple[Path, ...],
    model: str | None,
    duration: int | None,
    out_path: str,
) -> int:
    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 — dev instrument
        assert page is not None
        report = await _probe(page, project_id, refs, model, duration)
        out = Path(out_path)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        _stage(f"report written to {out}")
    print(json.dumps(report.get("verdict") or report, indent=2, ensure_ascii=False)[:8000])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile")
    parser.add_argument("project_id")
    parser.add_argument("--ref", action="append", required=True, help="repeat per reference file")
    parser.add_argument("--model", default=None, help="e.g. veo-lite-lp; omit to inherit the UI")
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help="seconds to force (4/6/8). Omit to inherit whatever the editor remembers — "
        "which is how the 4s in veo_3_1_t2v_lite_4s_low_priority got there.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="where to write the report (default: a timestamped file in the "
        "gitignored scripts/dev/_spike_out/). These reports carry decoded "
        "batchexecute payloads — prompt text, project and media ids — so the "
        "default deliberately never lands in the tracked tree.",
    )
    args = parser.parse_args()
    args.out = args.out or str(default_out_path("migrated_r2v_production_submit"))
    refs = tuple(Path(r) for r in args.ref)
    missing = [str(r) for r in refs if not r.is_file()]
    if missing:
        parser.error(f"reference file(s) not found: {', '.join(missing)}")
    return asyncio.run(
        _main(args.profile, args.project_id, refs, args.model, args.duration, args.out)
    )


if __name__ == "__main__":
    raise SystemExit(main())
