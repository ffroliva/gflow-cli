"""Spike: does flow.google.com put a generation refusal ON THE WIRE, and do we drop it?

**Question (#873 / #906 / #907).** On the migrated host a refusal ("unusual activity",
"couldn't generate") surfaces to gflow as a 60 s video `TransportTimeoutError` or an image
`WireFormatError("returned no ogiZ0b frame")`, while Flow's grid shows a failure tile.
Two PRs tried to scrape that tile's text. Hypothesis: the reason is in the batchexecute
reply, and `batchexecute.parse_frames` discards it — it keeps a `wrb.fr` frame only when
`item[2]` is a string, and an error envelope carries a null payload
(`["wrb.fr","MZZa6b",null,null,null,[5],"generic"]`, measured 2026-09-07).

**Method.** `FlowApiClient.generate_image` (lease held, gflow's own path), one image, a
benign prompt, a context route on `ogiZ0b`:

- arm `abort`: every `ogiZ0b` submit is `route.abort()`ed — never reaches Google.
- arm `tamper`: the FIRST `ogiZ0b` submit goes out with its reCAPTCHA Enterprise token
  corrupted (same length, tail reversed), so Google refuses it as it refuses a low score;
  any retry is aborted, so at most one bad token reaches Google.

Recorded per arm: HTTP status + raw reply of the submit, `requestfailed`, what
`parse_frames` returns for that reply, the error gflow raised (class, exit code,
retryable, detail), and a structural inventory of failure tiles in the grid.

**Cost.** Zero credits (image path). At most one image of daily quota if the tampered token
were somehow accepted. Two throwaway projects are created. Profile `ci-probe` (approved
2026-09-27 — a tampered token may raise that profile's WAF score for hours).

**Pre-registered reading.**

- `tamper` reply is non-200, or contains a `wrb.fr` row with a null payload and a status
  list, or an `er` row, AND `parse_frames` returns nothing for it: **hypothesis confirmed**
  — the refusal reason is on the wire and our parser discards it. The fix belongs in the
  parser + both submit observers, not in the DOM.
- `tamper` reply is a normal success frame: the token is not checked on submit;
  **settles nothing** about refusals.
- `tamper` gets no reply at all (same as `abort`): the refusal is not on the wire for this
  shape; a DOM-side signal would be needed after all.
- `abort` shows `requestfailed` and no response: "a reply was received" discriminates an
  abort from a refusal, as #873's re-review proposed.
- Failure tiles structurally identical across arms: confirms the #873 re-review finding
  that tile structure cannot tell a refusal from an abort.

    uv run python scripts/dev/spike_migrated_refusal_envelope.py --profile ci-probe
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
from urllib.parse import parse_qsl, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

from gflow_cli.api.image import Aspect, GenerateImageRequest  # noqa: E402
from gflow_cli.api.transports import migrated_composer as mc  # noqa: E402
from gflow_cli.api.transports.batchexecute import parse_frames  # noqa: E402

PROMPT = "a calm lake at dawn, gentle mist"
ARM_TIMEOUT_S = 420.0
_TOKEN = re.compile(r"[A-Za-z0-9_\-]{1500,}")

# Structure only: tag names, classes, ligatures, text LENGTH. The message node's text is
# Google's own copy and is kept (local, gitignored) because it names the refusal kind.
_TILES_JS = r"""
() => {
  const icons = Array.from(document.querySelectorAll('mat-icon, i.google-symbols'))
    .filter(i => (i.textContent || '').trim() === 'warning');
  return icons.map(icon => {
    let tile = icon.parentElement;
    while (tile && !(tile.tagName.includes('-') && tile.tagName !== 'MAT-ICON')) {
      tile = tile.parentElement;
    }
    if (!tile) return null;
    const ligs = Array.from(tile.querySelectorAll('mat-icon, i.google-symbols'))
      .map(i => (i.textContent || '').trim());
    const customs = Array.from(tile.querySelectorAll('*'))
      .map(e => e.tagName.toLowerCase()).filter(t => t.includes('-'));
    const attrs = Array.from(tile.attributes).map(a => a.name);
    return {tag: tile.tagName.toLowerCase(), cls: tile.className, attrs,
            ligatures: ligs, customTags: Array.from(new Set(customs)),
            textLen: (tile.innerText || '').length,
            text: (tile.innerText || '').slice(0, 300)};
  }).filter(Boolean);
}
"""


def _tamper_body(body: str) -> tuple[str | None, int]:
    """Corrupt the longest token-shaped string in ``f.req``; return (body, token_len)."""
    pairs = parse_qsl(body, keep_blank_values=True)
    for i, (k, v) in enumerate(pairs):
        if k != "f.req":
            continue
        tokens = sorted(_TOKEN.findall(v), key=len, reverse=True)
        if not tokens:
            return None, 0
        tok = tokens[0]
        bad = tok[:20] + tok[20:][::-1]
        pairs[i] = (k, v.replace(tok, bad))
        return urlencode(pairs), len(tok)
    return None, 0


async def _arm(client: Any, arm: str) -> dict[str, Any]:
    ctx, page = client._context, client._page  # noqa: SLF001 — dev instrument
    rec: dict[str, Any] = {"arm": arm, "submits": [], "responses": [], "failed": []}
    t0 = time.monotonic()

    async def route(r: Any, req: Any) -> None:
        body = req.post_data or ""
        rpcid = mc.url_rpcid(req.url) or mc.body_rpcid(body)
        if rpcid != mc.IMAGE_SUBMIT_RPC:
            await r.continue_()
            return
        n = len(rec["submits"])
        if arm == "tamper" and n == 0:
            new_body, tlen = _tamper_body(body)
            rec["submits"].append({"t": round(time.monotonic() - t0, 2), "action": "tamper",
                                   "token_len": tlen})
            if new_body is None:
                rec["submits"][-1]["action"] = "abort (no token found)"
                await r.abort()
                return
            await r.continue_(post_data=new_body)
            return
        rec["submits"].append({"t": round(time.monotonic() - t0, 2), "action": "abort"})
        await r.abort()

    async def on_response(resp: Any) -> None:
        if mc.url_rpcid(resp.url) != mc.IMAGE_SUBMIT_RPC:
            return
        try:
            text = await resp.text()
        except Exception as exc:  # noqa: BLE001
            text = f"<unreadable: {exc}>"
        rec["responses"].append({"t": round(time.monotonic() - t0, 2), "status": resp.status,
                                 "text": text[:4000],
                                 "parse_frames": repr(parse_frames(text))[:500]})

    def on_failed(req: Any) -> None:
        if mc.url_rpcid(req.url) == mc.IMAGE_SUBMIT_RPC:
            rec["failed"].append({"t": round(time.monotonic() - t0, 2),
                                  "failure": req.failure})

    await ctx.route("**/batchexecute*", route)
    page.on("response", on_response)
    page.on("requestfailed", on_failed)
    try:
        req = GenerateImageRequest(prompt=PROMPT, aspect=Aspect.PORTRAIT, count=1)
        await asyncio.wait_for(client.generate_image(req=req), ARM_TIMEOUT_S)
        rec["outcome"] = "SUCCESS (an image was generated)"
    except Exception as exc:  # noqa: BLE001 — the error IS the evidence
        rec["outcome"] = {"class": type(exc).__name__,
                          "exit_code": getattr(exc, "exit_code", None),
                          "retryable": getattr(exc, "retryable", None),
                          "detail": str(getattr(exc, "detail", exc))[:600]}
    rec["elapsed_s"] = round(time.monotonic() - t0, 1)
    try:
        await page.wait_for_timeout(4000)
        rec["failure_tiles"] = await page.evaluate(_TILES_JS)
    except Exception as exc:  # noqa: BLE001
        rec["failure_tiles"] = f"<unreadable: {exc}>"
    step(arm, f"outcome={rec['outcome']} submits={len(rec['submits'])} "
              f"responses={[r['status'] for r in rec['responses']]} failed={len(rec['failed'])}")
    return rec


async def _run(profile: str, arms: list[str]) -> int:
    profile_dir = resolve_profile_dir(profile)
    out = default_out_path("spike_migrated_refusal_envelope", ".json")
    record: dict[str, Any] = {"profile": profile, "arms": []}
    try:
        for arm in arms:
            async with build_client(profile_dir) as client:
                record["arms"].append(await _arm(client, arm))
    finally:
        out.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        step("out", str(out))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="migrated refusal envelope spike")
    ap.add_argument("--profile", default="ci-probe")
    ap.add_argument("--arms", default="abort,tamper")
    a = ap.parse_args()
    return asyncio.run(_run(a.profile, a.arms.split(",")))


if __name__ == "__main__":
    raise SystemExit(main())
