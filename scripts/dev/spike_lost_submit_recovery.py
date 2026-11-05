"""After a lost video submit reply, where can gflow still find the clip? (#1112) — PAID.

Cost: ONE video generation (~12 Veo credits on abra_r2v_8s), plus one ref upload.

About 1 in 7 flow.google.com video submits lose the ``MZZa6b`` reply body to Chrome's
DevTools buffer (``Network.getResponseBody: No data found``); Flow still makes and bills
the clip, and gflow exits 9. The loss cannot be triggered on demand, so this probe
ABLATES it: ``Response.text`` raises for the video submit reply (gflow sees exactly what
a real loss looks like), while the probe keeps the real body aside as ground truth.

It then measures BOTH recovery sides in the same run:

* **status side** — the generating page's own ``jwpduf``/``as29s`` polls, read on that
  page after gflow gave up: do they keep arriving, are their bodies readable, and does a
  record name our clip (new since the baseline, carrying our uploaded ref media id)?
* **listing side** — a second tab on the same context reloads the project every 20 s and
  reads ``Zzl0ze``: when does the new record appear, and when is it terminal?

When either side reports the clip done, it is downloaded with ``recover_clip`` (no new
generation) and its ids are compared with the ground truth.

    uv run python scripts/dev/spike_lost_submit_recovery.py <profile> -- \\
        video r2v "<prompt>" --ref <png> --reference-entity <id> \\
        --reference-entity-name <Name> --project <uuid> --aspect 16:9 --out-dir <dir>

Pre-registered readings (written 2026-10-27, before the run):

| status side | listing side | reading |
|---|---|---|
| readable polls name our clip to terminal | — | ship "keep listening": no reload, no 2nd page |
| polls stop / unreadable / never name it | record appears, reaches terminal | ship listing |
| neither identifies it | — | keep exit 9; recovery needs another source |
| matched id != ground truth | — | the matching rule is wrong; fix it before anything ships |
| gflow did not exit 9 | — | the ablation did not engage; the run settles nothing |

**t2v arm (#1141, readings written 2026-11-04, before the run).** The same command with
``video t2v "<unique prompt>" --project <uuid> --model veo-lite`` costs one lite clip. No
upload exists, so ``refs`` stays empty and three other keys are measured instead:

| observation | reading |
|---|---|
| a client uuid from the submit request is echoed in our record (``client_uuid_echo``) | exact key: match on it, the prompt is not needed |
| no echo; polls name exactly one in-flight record with our prompt, created after the click | match prompt + created-after-click; refuse on more than one |
| no echo; our record's ``created`` precedes the local click time by more than the skew seen | a clock comparison is unsafe; a pre-submit listing baseline is required |
| polls never name it; the listing does | listing fallback carries t2v, polls cannot |
| neither names it | keep exit 9, recovery needs another source |
| a matched media id differs from what the listing finally shows as new | the rule is wrong; ship nothing |
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from playwright.async_api import Error as PlaywrightError  # noqa: E402
from playwright.async_api import Response  # noqa: E402

from gflow_cli.api.transports import migrated_composer as mc  # noqa: E402
from gflow_cli.api.transports.batchexecute import _at, _is_record, parse_frames  # noqa: E402
from gflow_cli.api.transports.migrated_recover import recover_clip  # noqa: E402

from _spike_common import default_out_path  # noqa: E402, isort: skip

VIDEO_SUBMIT = {"MZZa6b", "YhhmEf", "eb1hJf", "nprQif"}
LISTING = "Zzl0ze"
WATCH_S = 420.0
RELOAD_EVERY_S = 20.0
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

T0 = time.monotonic()
report: dict[str, Any] = {"events": [], "truth": None, "baseline": [], "listing": {}}
_orig_text = Response.text


def now() -> float:
    return round(time.monotonic() - T0, 2)


def ev(kind: str, **kw: Any) -> None:
    report["events"].append({"t": now(), "kind": kind, **kw})
    print(f"[spike] {now():7.1f}s {kind} {kw}", file=sys.stderr, flush=True)


def _records(node: Any) -> list[list[Any]]:
    if not isinstance(node, list):
        return []
    if _is_record(node):
        return [node]
    return [r for child in node for r in _records(child)]


def _row(rec: list[Any]) -> dict[str, Any]:
    refs = _at(rec, 5, 6, 1, 1) or []
    return {
        "workflow_id": rec[0],
        "project_id": rec[1],
        "media_id": rec[2],
        "status": _at(rec, 5, 8, 0),
        "created": _at(rec, 5, 0, 0),
        "prompt": _at(rec, 5, 1),
        "model": _at(rec, 5, 6, 1, 0),
        "refs": [r[2] for r in refs if isinstance(r, list) and len(r) > 2],
        "entities": [e[0] for e in (_at(rec, 5, 6, 1, 8) or []) if isinstance(e, list)],
    }


def _rpc(url: str) -> str | None:
    return mc.url_rpcid(url) if "batchexecute" in url else None


async def _lossy_text(self: Response) -> str:
    """Ablation: the video submit reply body is 'lost', exactly like the DevTools miss."""
    if _rpc(self.url) in VIDEO_SUBMIT:
        raise PlaywrightError("spike: simulated Network.getResponseBody: No data found")
    return await _orig_text(self)


async def _probe(page: Any) -> None:
    """Status side on the generating page; runs alongside gflow's own listener."""
    client_uuids: set[str] = set()

    async def on_response(resp: Response) -> None:
        if "batchexecute" not in resp.url:
            return
        rid = _rpc(resp.url) or "?"
        try:
            text = await _orig_text(resp)
        except Exception as exc:  # noqa: BLE001 - a real loss is data here
            ev("body_unreadable", rpc=rid, error=type(exc).__name__)
            return
        if rid == mc.UPLOAD_RPC:
            ev("upload", media_id=mc._first_uuid(text))  # noqa: SLF001
        for frid, payload in parse_frames(text):
            found = _records(payload)
            rows = [_row(r) for r in found]
            for rec in found:
                echoed = client_uuids & set(UUID_RE.findall(json.dumps(rec)))
                if echoed:
                    ev("client_uuid_echo", rpc=frid, media_id=rec[2], uuids=sorted(echoed))
            if frid in VIDEO_SUBMIT and rows:
                report["truth"] = rows[0]
                ev("truth_submit_reply", rpc=frid, **rows[0])
            elif frid in mc.STATUS_RPCS:
                ev("status_poll", rpc=frid, records=rows)

    def on_request(req: Any) -> None:
        if _rpc(req.url) in VIDEO_SUBMIT:
            client_uuids.update(UUID_RE.findall(unquote(req.post_data or "")))
            ev(
                "submit_sent",
                rpc=_rpc(req.url),
                wall=time.time(),
                client_uuids=sorted(client_uuids),
            )

    page.on("response", on_response)
    page.on("request", on_request)


async def _listing(page2: Any, project_id: str) -> list[dict[str, Any]]:
    got: asyncio.Future[list[dict[str, Any]]] = asyncio.get_running_loop().create_future()

    async def on_response(resp: Response) -> None:
        if "batchexecute" not in resp.url or got.done():
            return
        try:
            text = await _orig_text(resp)
        except Exception:  # noqa: BLE001
            return
        for frid, payload in parse_frames(text):
            if frid == LISTING and not got.done():
                got.set_result([_row(r) for r in _records(payload)])

    page2.on("response", on_response)
    try:
        await page2.goto(mc.MIGRATED_PROJECT_URL.format(project_id=project_id))
        return await asyncio.wait_for(got, 30)
    finally:
        page2.remove_listener("response", on_response)


_orig_run_video = mc.run_video


async def _run_video(page: Any, request: Any, *, project_id: str | None, **kw: Any) -> Any:
    assert project_id, "pass --project so the listing has a project to read"
    # The baseline tab is closed before gflow runs: an open second tab steals the focus
    # the composer's upload chooser needs (first run: "no file chooser within 8s").
    page2 = await page.context.new_page()
    base = {r["media_id"] for r in await _listing(page2, project_id)}
    await page2.close()
    await page.bring_to_front()
    report["baseline"] = sorted(base)
    ev("baseline", records=len(base))
    await _probe(page)
    Response.text = _lossy_text  # type: ignore[method-assign]
    try:
        return await _orig_run_video(page, request, project_id=project_id, **kw)
    except Exception as exc:
        ev("gflow_raised", error=type(exc).__name__, detail=str(exc)[:200])
        report["gflow_error"] = type(exc).__name__
        # Decide on the REQUEST, never the reply: a real DevTools loss blinds this probe's
        # own read too (run 3: `body_unreadable MZZa6b`), so "no truth" is not "no submit".
        if not any(e["kind"] == "submit_sent" for e in report["events"]):
            raise  # nothing was submitted: there is no clip to look for
        page2 = await page.context.new_page()
        try:
            await _watch(page2, project_id, base)
        finally:
            await page2.close()
        raise
    finally:
        Response.text = _orig_text  # type: ignore[method-assign]


async def _watch(page2: Any, project_id: str, base: set[str]) -> None:
    deadline = time.monotonic() + WATCH_S
    while time.monotonic() < deadline:
        try:
            rows = await _listing(page2, project_id)
        except TimeoutError:  # one missed listing must not end the watch (#1141 run)
            ev("listing_timeout")
            continue
        new = [r for r in rows if r["media_id"] not in base]
        report["listing"][str(now())] = new
        ev("listing", new=[(r["media_id"], r["status"]) for r in new])
        if new and all(r["status"] in (3, 4) for r in new) and _status_terminal(base):
            break
        await asyncio.sleep(RELOAD_EVERY_S)
    done = [r for r in report["listing"][max(report["listing"], key=float)] if r["status"] == 3]
    if len(done) == 1:
        out = Path(default_out_path("lost_submit_clip", ".d"))
        out.mkdir(parents=True, exist_ok=True)
        clip = await recover_clip(
            page2, project_id=project_id, media_id=done[0]["media_id"], out_dir=out
        )
        ev("recovered", media_id=clip.media_id, bytes=clip.bytes, path=str(clip.path))


def _status_terminal(base: set[str]) -> bool:
    return any(
        r["media_id"] not in base and r["status"] in (3, 4)
        for e in report["events"]
        if e["kind"] == "status_poll"
        for r in e["records"]
    )


mc.run_video = _run_video  # type: ignore[assignment]

if __name__ == "__main__":
    from gflow_cli.cli import main as gflow_main

    try:
        gflow_main(args=sys.argv[sys.argv.index("--") + 1 :], standalone_mode=False)
        report["outcome"] = "gflow returned normally — ablation did not engage"
    except BaseException as exc:  # noqa: BLE001
        report["outcome"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    path = default_out_path("lost_submit_recovery")
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[spike] OUTCOME {report['outcome']}\n[spike] report -> {path}", flush=True)
