"""List a migrated project's recent generations and their status — read-only, $0.

Opens the project editor and reads the page's own ``jwpduf`` poll in full (the submit
spike truncates bodies to 400 chars, which is enough to see a prompt and not enough to
see an outcome). Nothing is attached and nothing is submitted, so this cannot spend.

**Why it exists.** An entity-bound run was read as "failed" from a null ``MZZa6b`` reply,
and Flow's own gallery then showed a card reading **Queued** for the same submission. A
null submit payload and a failed generation are not the same thing, and guessing between
them from the submit reply alone is what produced a wrong conclusion. This reads the
outcome instead.

Usage:
    uv run python scripts/dev/spike_project_media_status.py <profile> <project_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from gflow_cli.api.transports.migrated_composer import MigratedComposer  # noqa: E402

from _spike_common import build_client, default_out_path, resolve_profile_dir  # noqa: E402, isort: skip

# A generation row carries: mediaId, projectId, workflowId, a "CAE"-ish enum, then a
# timestamp pair and the prompt. Status lives in the enum and in a trailing int.
ROW = re.compile(r'\\"([0-9a-f-]{36})\\",\\"([0-9a-f-]{36})\\",\\"([0-9a-f-]{36})\\",\\"(\w+)\\"')


def _rpcid(url: str) -> str | None:
    try:
        return urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("rpcids", [None])[0]
    except Exception:  # noqa: BLE001
        return None


async def _probe(page: Any, project_id: str, wait_s: float) -> dict[str, Any]:
    bodies: list[str] = []

    async def on_response(response: Any) -> None:
        url = str(getattr(response, "url", ""))
        if "batchexecute" not in url or _rpcid(url) != "jwpduf":
            return
        try:
            bodies.append(await response.text())
        except Exception:  # noqa: BLE001
            return

    page.on("response", lambda r: asyncio.create_task(on_response(r)))
    await MigratedComposer().ensure_editor(page, project_id)
    await page.wait_for_timeout(int(wait_s * 1000))

    rows: dict[str, dict[str, Any]] = {}
    for b in bodies:
        for m in ROW.finditer(b):
            media_id, proj, workflow, enum = m.groups()
            if proj != project_id:
                continue
            tail = b[m.end() : m.end() + 900]
            prompt = ""
            pm = re.search(r'\\"([^"\\]{20,200})', tail)
            if pm:
                prompt = pm.group(1)
            rows[media_id] = {
                "media_id": media_id,
                "workflow_id": workflow,
                "enum": enum,
                "prompt_head": prompt[:110],
            }
    # Keep one raw body: the first pass at this script keyed on the 4th field and got
    # "CAE" for every row, including one the gallery showed as Queued and one it showed
    # as Failed. A constant is not a status. Until the real status field is located in
    # the wire shape, the raw sample is the only honest artefact to reason from.
    sample = max(bodies, key=len) if bodies else ""
    return {
        "project_id": project_id,
        "poll_bodies": len(bodies),
        "rows": list(rows.values()),
        "raw_sample_len": len(sample),
        "raw_sample": sample[:20000],
    }


async def _main(profile: str, project_id: str, wait_s: float, out_path: str) -> int:
    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 — dev instrument
        assert page is not None
        report = await _probe(page, project_id, wait_s)
        Path(out_path).write_text(json.dumps(report, indent=2, ensure_ascii=False), "utf-8")
        print(
            f"[spike] {len(report['rows'])} generation rows from "
            f"{report['poll_bodies']} polls -> {out_path}"
        )
        for r in report["rows"][:25]:
            print(f"  {r['enum']:6} {r['media_id'][:8]}  {r['prompt_head']}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("profile")
    p.add_argument("project_id")
    p.add_argument("--wait", type=float, default=25.0)
    p.add_argument("--out", default=None)
    a = p.parse_args()
    out = a.out or str(default_out_path("project_media_status"))
    return asyncio.run(_main(a.profile, a.project_id, a.wait, out))


if __name__ == "__main__":
    raise SystemExit(main())
