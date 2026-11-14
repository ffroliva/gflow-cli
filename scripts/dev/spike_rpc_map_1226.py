"""#1226 spike ($0): which batchexecute rpc ids does each Flow UI action send?

Opens one profile's Flow in gflow's own transport and stays up, driven by a command file
(one JSON object per line) so a run can explore the UI and log the wire in the same
browser. Every ``batchexecute`` request and reply in the context is logged as SHAPE only:
strings become ``<uuid>``/``<url>``/``<tok>``/``<s:N>``, except enum-like tokens
(``project_title``, model keys) that carry no user data. Nothing printed or written holds
a token, cookie, ``at``, full media id or signed URL. Output: ``scripts/dev/_spike_out/1226/``
(gitignored).

Billing guard (a context route, so it sees every page's request before it leaves):
- always aborted: the known billed submits (``YhhmEf eb1hJf MZZa6b nprQif ogiZ0b
  fZytfe``) and the two upscales (``SPrCad p0UkFb``), named in the URL or the body;
- with ``{"guard": "new"}``: additionally abort any request carrying an id this session
  has not seen yet;
- with ``{"guard": "strict"}``: abort new ids AND the known writes (``maseQ jHPbke
  o8DA4 QI2zvc``) too: used for character create and for any delete click on an object this
  spike did not create, whose request is then "seen, not sent".
Credits are read before and after by the operator (``gflow credits user --json``).

Pre-registered reading (written before the run):
- An id is attributed to an action only if it appears in that action's window AND not
  in the idle window logged right before it (ids the page sends on its own).
- An action whose window holds no batchexecute is recorded "no RPC (client-side)": a
  finding, not a miss — provided the positive control (the project-open window shows
  ``Zzl0ze``) held in the same run; without it the run says nothing.
- An aborted id is "seen, not sent": its request shape is known, its reply is not.
- "Billed: no" only for an id that was SENT and the balance did not drop over the run.
- The delete RPC is the id unique to the delete window AND the deleted object is gone
  from the next ``Zzl0ze``/projects listing. An id in the window without that check is
  "candidate", not "delete".

Commands (``cmd.jsonl``, appended by the operator; each line runs once, in order):
  {"step": "label"}                 start a new attribution window
  {"guard": "billed"|"new"|"strict"}  billing guard mode (default ``billed``)
  {"allow": [ids]}                  count ids as seen, so ``new``/``strict`` let them pass
  {"goto": url} {"click": sel} {"fill": [sel, text]} {"press": key} {"hover": sel}
  {"wait": ms} {"shot": name} {"dom": css} {"eval": js} {"upload": [sel, path]}
  {"chooser": [sel, path]}          click sel and answer the file chooser it opens
  {"quit": true}

Usage:
    python scripts/dev/spike_rpc_map_1226.py --profile denon82
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from gflow_cli.api.transports import migrated_composer as mc
from gflow_cli.api.transports import migrated_extend as mx
from gflow_cli.api.transports.batchexecute import parse_frames, rpc_errors
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.config import get_settings

OUT = Path("scripts/dev/_spike_out/1226")
BILLED = {"YhhmEf", "eb1hJf", "MZZa6b", "nprQif", "ogiZ0b", "fZytfe", "SPrCad", "p0UkFb"}
WRITES = {"maseQ", "jHPbke", "o8DA4", "QI2zvc"}
B64_PNG = "iVBORw0KGgo"
UUID = re.compile(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
ENUM = re.compile(r"^(?:[a-z][a-z0-9]*(?:_[a-z0-9]+)+|[A-Z][A-Z0-9_]{2,40})$")

state: dict[str, Any] = {"step": "boot", "guard": "billed", "seen": set()}


def shape(node: Any, depth: int = 0) -> Any:
    """A redacted skeleton of a decoded payload: structure kept, user data dropped."""
    if depth > 9:
        return "…"
    if isinstance(node, list):
        items = [shape(n, depth + 1) for n in node[:12]]
        return [*items, f"…+{len(node) - 12}"] if len(node) > 12 else items
    if isinstance(node, dict):
        return {k: shape(v, depth + 1) for k, v in list(node.items())[:8]}
    if isinstance(node, str):
        if node.startswith(("[", "{")):
            try:
                return {"json": shape(json.loads(node), depth + 1)}
            except ValueError:
                pass
        if UUID.fullmatch(node):
            return "<uuid>"
        if UUID.search(node):
            return UUID.sub("<uuid>", node) if len(node) < 60 else "<s+uuid>"
        if node in ("projects/*", "tools/PINHOLE"):
            return node
        if node.startswith("http"):
            return "<url>"
        if len(node) > 60:
            kind = "png-b64" if B64_PNG in node[:80] else "tok"
            return f"<{kind}:{len(node)}>"
        return node if ENUM.fullmatch(node) else f"<s:{len(node)}>"
    if isinstance(node, (int, float)) and not isinstance(node, bool) and abs(node) >= 10_000:
        return "<n>"
    return node


def log(kind: str, **fields: Any) -> None:
    entry = {"t": round(time.time(), 2), "step": state["step"], "kind": kind, **fields}
    with (OUT / "wire.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


async def route(r: Any, request: Any) -> None:
    body = mc.request_body(request)
    ids = mx.batch_rpcids(mx.url_rpcids(str(request.url)), body)
    frames = [[str(mx._at(f, 0)), shape(mx._at(f, 1))] for f in mx._frames(body)]  # noqa: SLF001
    new = [i for i in ids if i not in state["seen"]]
    guard = state["guard"]
    abort = (
        bool(BILLED.intersection(ids))
        or (guard in ("new", "strict") and bool(new))
        or (guard == "strict" and bool(WRITES.intersection(ids)))
    )
    query = parse_qs(urlsplit(str(request.url)).query)
    src = UUID.sub("<uuid>", query.get("source-path", [""])[0])
    log("req", ids=ids, aborted=abort, qkeys=sorted(query), src=src, frames=frames)
    if abort:
        await r.abort()
        return
    state["seen"].update(ids)
    await r.fallback()


async def on_response(response: Any) -> None:
    url = str(response.url)
    if "batchexecute" not in url:
        return
    try:
        text = await response.text()
    except Exception as exc:  # noqa: BLE001 - logged, never fatal
        log("resp_unreadable", ids=mx.url_rpcids(url), error=type(exc).__name__)
        return
    log(
        "resp",
        status=response.status,
        ids=mx.url_rpcids(url),
        frames=[[rid, shape(p)] for rid, p in parse_frames(text)],
        errors=[[e.rpcid, e.code, list(e.reasons)] for e in rpc_errors(text)],
    )


def on_request(request: Any) -> None:
    """Non-batchexecute XHR/fetch: method + host + redacted path (uploads, downloads)."""
    if request.resource_type not in ("xhr", "fetch") or "batchexecute" in request.url:
        return
    host, _, path = request.url.split("://", 1)[-1].partition("/")
    path = UUID.sub("<uuid>", path.split("?", 1)[0])
    path = re.sub(r"[A-Za-z0-9_\-]{40,}", "<tok>", path)
    log("xhr", method=request.method, host=host, path="/" + path[:120])


DOM_JS = """(css) => [...document.querySelectorAll(css)].filter(e => e.offsetParent !== null)
  .slice(0, 80).map(e => ({tag: e.tagName.toLowerCase(),
    aria: e.getAttribute('aria-label'), icon: [...e.querySelectorAll('mat-icon,i')]
      .map(i => i.textContent.trim()).join(','), role: e.getAttribute('role'),
    text: (e.innerText || '').trim().slice(0, 40),
    cls: (e.className || '').toString().slice(0, 60)}))"""


async def run(page: Any, cmd: dict[str, Any], n: int) -> Any:
    if "step" in cmd:
        state["step"] = cmd["step"]
        log("step")
        return "ok"
    if "allow" in cmd:
        state["seen"].update(cmd["allow"])
        return "ok"
    if "guard" in cmd:
        state["guard"] = cmd["guard"]
        return "ok"
    if "goto" in cmd:
        await page.goto(cmd["goto"], wait_until="domcontentloaded")
        return page.url.split("?")[0]
    if "click" in cmd:
        await page.locator(cmd["click"]).first.click(timeout=15_000)
        return "ok"
    if "hover" in cmd:
        await page.locator(cmd["hover"]).first.hover(timeout=15_000)
        return "ok"
    if "fill" in cmd:
        await page.locator(cmd["fill"][0]).first.fill(cmd["fill"][1], timeout=15_000)
        return "ok"
    if "press" in cmd:
        await page.keyboard.press(cmd["press"])
        return "ok"
    if "upload" in cmd:
        await page.locator(cmd["upload"][0]).first.set_input_files(cmd["upload"][1])
        return "ok"
    if "chooser" in cmd:
        async with page.expect_file_chooser(timeout=15_000) as fc:
            await page.locator(cmd["chooser"][0]).first.click(timeout=15_000)
        await (await fc.value).set_files(cmd["chooser"][1])
        return "ok"
    if "wait" in cmd:
        await page.wait_for_timeout(cmd["wait"])
        return "ok"
    if "shot" in cmd:
        await page.screenshot(path=str(OUT / f"{n:03d}-{cmd['shot']}.png"))
        return "ok"
    if "dom" in cmd:
        return await page.evaluate(DOM_JS, cmd["dom"])
    if "eval" in cmd:
        return await page.evaluate(cmd["eval"])
    return "unknown command"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    cmd_file = OUT / "cmd.jsonl"
    cmd_file.touch()
    done = len(cmd_file.read_text(encoding="utf-8").splitlines())  # only new lines run
    transport = UiAutomationTransport()
    await transport.setup(get_settings().profile_subdir(a.profile))
    page = transport._page  # noqa: SLF001
    assert page is not None
    ctx = page.context
    await ctx.route("**/batchexecute**", route)
    ctx.on("response", on_response)
    ctx.on("request", on_request)
    (OUT / "ready").write_text("1")
    try:
        while True:
            lines = cmd_file.read_text(encoding="utf-8").splitlines()
            for line in lines[done:]:
                done += 1
                try:
                    cmd = json.loads(line)
                    if cmd.get("quit"):
                        return
                    result: Any = await run(page, cmd, done)
                except Exception as exc:  # noqa: BLE001 - reported to the operator
                    result = f"ERROR {type(exc).__name__}: {str(exc)[:300]}"
                (OUT / f"result-{done:03d}.json").write_text(json.dumps(result, indent=1))
            await asyncio.sleep(0.3)
    finally:
        await transport.teardown()


if __name__ == "__main__":
    asyncio.run(main())
