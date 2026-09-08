"""What is in this project's queue, right now? — $0, read-only (#741).

The migrated frontend polls its own generations on a ~5 s timer (``jwpduf``) and
fetches finished records with ``as29s``. gflow has no surface that shows them, so a
run that lost its observer (#723 — a ``MZZa6b`` submit reply with a null payload) is
indistinguishable from a generation that never started. It is not: it queues, renders,
and lands in the project while the CLI reports exit 9.

This opens the project editor, listens to the page's *own* polls for ``--seconds``,
and prints every record it saw — media id, kind, status, byte size, model key, signed URL.
Nothing is submitted and nothing is clicked, so it spends no credit.

    uv run python scripts/dev/spike_migrated_queue_read.py <profile> <project-id> [--seconds 45]

Measured 2026-09-08 on ffroliva, project ``c5550ed7…`` (30 videos, 7 images), 30 s:

* **``Zzl0ze`` carries the listing** — all 69 records arrived on it, on the project load.
  ``jwpduf``/``as29s`` are the *progress* polls and appear only while something is
  actually generating, so an idle project reads its whole history from ``Zzl0ze`` alone.
* Records come in **two shapes**, and conflating them is why an early run reported
  ``status: null`` rows that looked like failures. Length 8 is a video generation and
  carries the model arm at ``[7][0]``; length 7 is an image and has no such arm, no
  status and no model. ``kind`` labels them.
* The rpcid inventory is printed to stderr and ``--dump`` writes every decoded frame.
  That inventory is the point for #719: a driver waiting on a name Flow no longer uses
  reports a 60 s timeout and nothing else, and only a list of what DID arrive can tell
  a renamed rpcid apart from a request that never went out.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gflow_cli.api.transports.batchexecute import _at, _is_record, parse_frames  # noqa: E402
from gflow_cli.api.transports.migrated_composer import MigratedComposer  # noqa: E402

from _spike_common import build_client, resolve_profile_dir  # noqa: E402, isort: skip

STATUS = {2: "running", 3: "done", 6: "submitted"}


def _all_records(node: Any) -> list[list[Any]]:
    """Every record in the payload — `generation_record` finds only the first, and a
    status poll reports the whole queue."""
    if not isinstance(node, list):
        return []
    if _is_record(node):
        return [node]
    out: list[list[Any]] = []
    for child in node:
        out.extend(_all_records(child))
    return out


def _summarise(rec: list[Any]) -> dict[str, Any]:
    details = _at(rec, 5)
    # Only a video generation carries this arm. An image record is one element shorter
    # and has no status or model at all — reported as a bare `null` it reads as a failed
    # generation, which is the exact confusion this instrument exists to remove.
    generation = _at(rec, 7, 0)
    status = _at(details, 8, 0)
    return {
        "media_id": rec[2],
        "workflow_id": rec[0],
        "kind": "video" if generation else "image",
        "status": STATUS.get(status, status),
        "size_bytes": _at(details, 13),
        "model": _at(generation, 12),
        "media_url": _at(generation, 8) or _at(details, 5),
    }


async def _main(
    profile: str,
    project_id: str,
    seconds: float,
    dump: Path | None,
    save_media: str | None,
    save_to: Path | None,
) -> int:
    seen: dict[str, dict[str, Any]] = {}
    frames: list[dict[str, Any]] = []
    #: Every rpcid seen, whether or not it carried a record. A driver that waits on one
    #: name cannot tell "renamed" from "never sent"; this can.
    rpcids: Counter[str] = Counter()

    async def on_response(response: Any) -> None:
        url = str(getattr(response, "url", ""))
        if "batchexecute" not in url:
            return
        try:
            text = await response.text()
        except Exception:  # noqa: BLE001 — an aborted body is not our frame
            return
        for _rpcid, payload in parse_frames(text):
            if dump is not None:
                frames.append({"rpcid": _rpcid, "payload": payload})
            rpcids[_rpcid] += 1
            for rec in _all_records(payload):
                row = _summarise(rec)
                # A later poll carries more (a URL, a size): keep the richest read.
                prev = seen.get(row["media_id"])
                if prev is None or row["media_url"] or row["status"] == "done":
                    seen[row["media_id"]] = row

    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 — dev instrument
        assert page is not None
        page.on("response", on_response)
        await MigratedComposer().ensure_editor(page, project_id)
        print(
            f"[spike] editor open; listening {seconds:.0f}s for the page's own polls",
            file=sys.stderr,
            flush=True,
        )
        await page.wait_for_timeout(int(seconds * 1000))
        if save_to is not None:
            # The signed CDN URL is cookie-gated: fetched outside the browser context it
            # returns the Google login page, so the GET rides the page's own request
            # context exactly as `MigratedComposer.download` does.
            row = seen.get(save_media or "")
            if row is None or not row["media_url"]:
                print(f"[spike] no signed URL for {save_media}", file=sys.stderr, flush=True)
                return 2
            resp = await page.request.get(row["media_url"], timeout=180_000, max_redirects=0)
            body = await resp.body()
            if body[4:8] != b"ftyp":
                print(f"[spike] not an mp4: {body[:4].hex()}", file=sys.stderr, flush=True)
                return 2
            save_to.write_bytes(body)
            print(f"[spike] {len(body)} B -> {save_to}", file=sys.stderr, flush=True)

    print(f"[spike] rpcids seen: {dict(rpcids.most_common())}", file=sys.stderr, flush=True)
    if dump is not None:
        dump.write_text(json.dumps(frames, ensure_ascii=False), encoding="utf-8")
        print(f"[spike] {len(frames)} frames -> {dump}", file=sys.stderr, flush=True)
    print(json.dumps(list(seen.values()), indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile")
    parser.add_argument("project_id")
    parser.add_argument("--seconds", type=float, default=45.0)
    parser.add_argument("--dump", type=Path, default=None, help="write every decoded frame here")
    parser.add_argument("--save-media", default=None, help="media id to download")
    parser.add_argument("--save-to", type=Path, default=None, help="where to write it")
    args = parser.parse_args()
    return asyncio.run(
        _main(args.profile, args.project_id, args.seconds, args.dump, args.save_media, args.save_to)
    )


if __name__ == "__main__":
    raise SystemExit(main())
