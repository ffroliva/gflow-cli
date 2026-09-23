"""#865/#871: recover a stranded clip from the per-clip route — reference shape for
`gflow data download <media_id>`.

Measured 2026-09-17 against both orphans from #865, $0:

* `flow.google.com/project/<project_id>/edit/<media_id>` is a real route — clicking a
  grid tile navigates there, and BOTH ids are already in the local catalog. So no tile
  has to be found: the virtualized grid renders only ~13 of 183 records, which would
  otherwise leave any older clip unreachable.
* Loading that route fires `as29s`, whose reply carries the signed
  `flow-content.google/video/<workflow_id>?Expires&KeyName&Signature`. `as29s` is already
  in `STATUS_RPCS` and `generation_record()` already decodes that slot.
* Bytes are verified against the record's own `size_bytes`, NOT just `ftyp`: the poster
  token's `=m18`/`=m22` renditions are valid mp4s of the wrong file (#281 class).

No DOM selector of any kind is on this path, so nothing here can drift with Flow's markup
or the account locale.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gflow_cli.api.transports.batchexecute import generation_record, parse_frames  # noqa: E402

from _spike_common import build_client, resolve_profile_dir  # noqa: E402, isort: skip

CLIP_URL = "https://flow.google.com/project/{project_id}/edit/{media_id}"


async def _main(profile: str, project_id: str, media_id: str, out: Path, wait_s: float) -> int:
    found: dict[str, Any] = {}

    async def on_response(response: Any) -> None:
        if "batchexecute" not in str(getattr(response, "url", "")):
            return
        try:
            text = await response.text()
        except Exception:  # noqa: BLE001
            return
        for rid, payload in parse_frames(text):
            try:
                rec = generation_record(rid, payload)
            except Exception:  # noqa: BLE001 - not every frame is a record
                continue
            if rec.media_id != media_id or not rec.video_url:
                continue
            if "flow-content" in rec.video_url and "url" not in found:
                found.update(url=rec.video_url, size=rec.size_bytes, wf=rec.workflow_id, rpc=rid)

    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 - dev instrument
        assert page is not None
        page.on("response", on_response)
        url = CLIP_URL.format(project_id=project_id, media_id=media_id)
        print(f"[spike] goto {url}", file=sys.stderr, flush=True)
        await page.goto(url, wait_until="domcontentloaded", timeout=90_000)

        deadline = asyncio.get_running_loop().time() + wait_s
        while "url" not in found and asyncio.get_running_loop().time() < deadline:
            await page.wait_for_timeout(500)
        if "url" not in found:
            print(f"[spike] no signed url for {media_id} within {wait_s:.0f}s",
                  file=sys.stderr, flush=True)
            return 2
        print(f"[spike] rpc={found['rpc']} workflow={found['wf']} size={found['size']}",
              file=sys.stderr, flush=True)

        resp = await page.request.get(found["url"], timeout=180_000, max_redirects=0)
        body = await resp.body()
        expected = found["size"]
        ok = resp.status == 200 and body[4:8] == b"ftyp" and (expected is None or len(body) == expected)
        print(
            f"[spike] GET -> HTTP {resp.status} {len(body)} B ftyp={body[4:8] == b'ftyp'} "
            f"size_match={len(body) == expected}",
            file=sys.stderr, flush=True,
        )
        if not ok:
            return 2
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(body)
        print(f"[spike] RECOVERED {media_id} -> {out}", file=sys.stderr, flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("profile")
    p.add_argument("project_id")
    p.add_argument("media_id")
    p.add_argument("--out", type=Path, default=Path("scripts/dev/_spike_out/route.mp4"))
    p.add_argument("--wait", type=float, default=45.0)
    a = p.parse_args()
    return asyncio.run(_main(a.profile, a.project_id, a.media_id, a.out, a.wait))


if __name__ == "__main__":
    raise SystemExit(main())
