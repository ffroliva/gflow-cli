r"""Which wire carries the character LIFECYCLE on flow.google.com? (hand-driven recorder)

Question (2026-10-16, issue #639 / characters row): labs ``flow.createEntity`` and
``flow.projectInitialData`` are retired (404 "Flow RPCs have been deprecated",
KNOWN_ISSUES.md). gflow therefore cannot create, list, show, patch or delete a
character on an account Flow serves from flow.google.com. We need the calls the
migrated frontend itself makes for:

  1. create a character (new entity)
  2. set its name / personality / **voice** (preset)
  3. give it an image by **Upload** (not generation) — the import path for a
     character moved between projects or accounts
  4. list the project's characters / open one
  5. delete one

This is rung 3 of the spike ladder in Python (the PowerShell HAR harness does not
run on Linux): a headed Chrome on the profile, opened through ``FlowApiClient`` so
the profile lease is held, records the network while a HUMAN drives the editor.

Control: the script polls ``--control-dir``. Creating a file there named
``mark-<label>`` records a timestamped marker plus a structural DOM snapshot
(custom-element tags, mat-icon ligatures, inputs; never display text as an anchor);
a file named ``stop`` ends the capture.

Recorded per request (flow.google.com / labs.google / aisandbox-pa / upload hosts):
method, host, path, batchexecute rpcids, the decoded ``f.req`` (the ``at`` XSRF
token is never recorded), status, and the response head. No headers, no cookies.
Output is JSONL under ``scripts/dev/_spike_out/`` (gitignored) — it carries
prompts and ids, so it is NOT shareable unredacted.

Cost: navigation and DOM reads are free. Whatever the human clicks is what is
spent: an Upload is free, a portrait *generation* costs image quota (0 credits).

Pre-registered reading:

* an rpcid fires on "create" and its reply carries a new uuid
  -> that rpcid creates the entity
* an rpcid fires on a name/voice change carrying the voice name
  -> that rpcid patches the entity (voice is a preset id)
* Upload fires an upload rpc, then an entity-update rpc carrying the media id
  -> an imported character can be built from files, no generation
* create/patch go to aisandbox-pa REST instead of batchexecute
  -> backend still shared; only labs tRPC retired
* nothing new fires on an action
  -> **unmeasured for that action** (it may not have committed); settle by
     repeating it with a mark before and after

    uv run python scripts/dev/spike_migrated_character_lifecycle.py \
        --profile ziyedbegmail --control-dir /tmp/charspike
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

_HOSTS = ("flow.google.com", "labs.google", "aisandbox-pa.googleapis.com", "content-")
_SKIP_SUFFIX = (".js", ".css", ".woff2", ".woff", ".png", ".jpg", ".svg", ".ico", ".wav")
_BODY_CAP = 6000
_RESP_CAP = 200_000

_DOM_JS = r"""() => {
  const tally = a => Object.entries(a.reduce((m, k) => (m[k] = (m[k] || 0) + 1, m), {}))
    .sort((x, y) => y[1] - x[1]);
  const all = [...document.querySelectorAll('*')];
  return {
    url: location.href,
    custom_tags: tally(all.map(e => e.tagName.toLowerCase()).filter(t => t.includes('-')
      && !t.startsWith('mat-') && !t.startsWith('cdk-'))).slice(0, 80),
    ligatures: tally([...document.querySelectorAll('mat-icon, .google-symbols')]
      .map(e => (e.textContent || '').trim()).filter(t => /^[a-z0-9_]+$/.test(t))),
    inputs: [...document.querySelectorAll('input, textarea, [contenteditable="true"]')]
      .map(e => ({tag: e.tagName.toLowerCase(), type: e.getAttribute('type'),
                  cls: (e.className && e.className.baseVal === undefined ? e.className : '')
                         .toString().slice(0, 80),
                  accept: e.getAttribute('accept')})),
    dialogs: document.querySelectorAll('[role="dialog"], mat-dialog-container').length,
    entity_chips: [...document.querySelectorAll('[data-entity-id]')]
      .map(e => e.getAttribute('data-entity-id')),
  };
}"""


def _interesting(url: str) -> bool:
    parts = urlsplit(url)
    if not any(h in parts.netloc for h in _HOSTS):
        return False
    return not parts.path.lower().endswith(_SKIP_SUFFIX)


def _rpcids(url: str) -> list[str]:
    q = parse_qs(urlsplit(url).query)
    return [r for v in q.get("rpcids", []) for r in v.split(",") if r]


def _decode_body(post: str | None) -> Any:
    if not post:
        return None
    try:
        form = parse_qs(post, keep_blank_values=True)
    except ValueError:
        return post[:_BODY_CAP]
    if "f.req" in form:
        # `at` is the XSRF token: never recorded.
        freq = form["f.req"][0]
        # An upload carries a base64 image in the middle; keep both ends so the
        # fields AFTER the payload (e.g. which entity it binds to) survive.
        if len(freq) > 2 * _BODY_CAP:
            return {"f.req": freq[:_BODY_CAP], "f.req_tail": freq[-_BODY_CAP:]}
        return {"f.req": freq}
    return post[:_BODY_CAP]


async def _main(profile: str, control: Path, start_url: str, minutes: float) -> int:
    profile_dir = resolve_profile_dir(profile)
    control.mkdir(parents=True, exist_ok=True)
    for f in control.iterdir():
        f.unlink()
    out = default_out_path(f"migrated_character_lifecycle_{profile}", ".jsonl")
    sink = out.open("a", encoding="utf-8")
    t0 = time.monotonic()

    def emit(rec: dict[str, Any]) -> None:
        rec["t"] = round(time.monotonic() - t0, 3)
        sink.write(json.dumps(rec, ensure_ascii=False) + "\n")
        sink.flush()

    step("profile", f"{profile} -> {profile_dir}")
    step("out", str(out))
    async with build_client(profile_dir) as client:
        context = client._context  # noqa: SLF001 - spike watches the live context
        page = await context.new_page()

        def on_request(req: Any) -> None:
            if not _interesting(req.url):
                return
            parts = urlsplit(req.url)
            emit(
                {
                    "ev": "req",
                    "method": req.method,
                    "host": parts.netloc,
                    "path": parts.path[:160],
                    "rpcids": _rpcids(req.url),
                    "body": _decode_body(req.post_data) if req.method != "GET" else None,
                }
            )

        async def on_response(resp: Any) -> None:
            if not _interesting(resp.url):
                return
            parts = urlsplit(resp.url)
            try:
                text = await resp.text()
            except Exception:  # noqa: BLE001 - streamed/consumed bodies are fine to miss
                text = ""
            emit(
                {
                    "ev": "resp",
                    "status": resp.status,
                    "host": parts.netloc,
                    "path": parts.path[:160],
                    "rpcids": _rpcids(resp.url),
                    "len": len(text),
                    "head": text[:_RESP_CAP],
                }
            )

        context.on("request", on_request)
        context.on("response", lambda r: asyncio.create_task(on_response(r)))

        await page.goto(start_url, wait_until="domcontentloaded", timeout=60_000)
        step("landed", page.url)
        emit({"ev": "mark", "label": "landed", "dom": await page.evaluate(_DOM_JS)})
        step(
            "ready", f"drive the browser; mark via {control}/mark-<label>, stop via {control}/stop"
        )

        deadline = t0 + minutes * 60
        while time.monotonic() < deadline:
            await asyncio.sleep(1)
            for f in sorted(control.iterdir()):
                name = f.name
                f.unlink()
                if name == "stop":
                    step("stop", "requested")
                    deadline = 0
                    break
                if name.startswith("mark-"):
                    label = name[5:]
                    pages = [p for p in context.pages if not p.is_closed()]
                    doms = []
                    for p in pages:
                        try:
                            doms.append(await p.evaluate(_DOM_JS))
                        except Exception as exc:  # noqa: BLE001 - a navigating page
                            doms.append({"error": str(exc)[:200]})
                    emit({"ev": "mark", "label": label, "dom": doms})
                    step("mark", label)
        sink.close()
    step("wrote", str(out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--control-dir", required=True, type=Path)
    ap.add_argument("--start-url", default="https://flow.google.com/")
    ap.add_argument("--minutes", type=float, default=30.0)
    args = ap.parse_args()
    raise SystemExit(
        asyncio.run(_main(args.profile, args.control_dir, args.start_url, args.minutes))
    )
