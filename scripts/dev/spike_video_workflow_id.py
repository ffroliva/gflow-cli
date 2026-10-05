r"""Is the id we store as a video's ``flow_operation_id`` Flow's own workflow id? ($0)

#898 item 2: ``assets.flow_workflow_id`` is written ``None`` for every video, while the
video path stores an id on ``operations.flow_operation_id``. On flow.google.com the
transport fills it from the submit reply's workflow id (``VideoStarted(flow_operation_id=
first.workflow_id)``). Before moving it onto the asset row, check it against what Flow
itself says, read-only: the project listing's ``workflows[].name`` /
``metadata.primaryMediaId`` and the media item's ``workflowId``.

Pre-registered reading:
  the stored operation id == the listing's workflow id for that media id
    -> it IS the workflow id; persist it on the asset (the catalog lookup by workflow id
       and the MCP task result's ``flow_workflow_id`` then work for videos).
  it differs -> it is something else; do not relabel it.

    python scripts/dev/spike_video_workflow_id.py --profile ci-probe \
        --project <uuid> --media <media uuid> --stored <operations.flow_operation_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402, isort: skip

from gflow_cli.api.client import _unwrap_trpc  # noqa: E402  # pyright: ignore[reportPrivateUsage]


async def _main(profile: str, project: str, media: str, stored: str) -> int:
    findings: dict[str, Any] = {"media": media, "stored_operation_id": stored}
    async with build_client(resolve_profile_dir(profile)) as client:
        try:
            listing = await client.fetch_project_listing(project)
        except Exception as exc:  # noqa: BLE001 - the failure IS a finding
            step("listing", f"FAILED {type(exc).__name__}: {str(exc)[:160]}")
            findings["listing_error"] = f"{type(exc).__name__}: {exc}"
            listing = None
        if listing is not None:
            contents = _unwrap_trpc(listing).get("projectContents") or {}
            items = [m for m in contents.get("media") or [] if m.get("name") == media]
            findings["media_workflowId"] = [m.get("workflowId") for m in items]
            findings["workflows_for_media"] = [
                w.get("name")
                for w in contents.get("workflows") or []
                if (w.get("metadata") or {}).get("primaryMediaId") == media
            ]
            findings["n_media"] = len(contents.get("media") or [])
            findings["n_workflows"] = len(contents.get("workflows") or [])
            wf = set(findings["media_workflowId"]) | set(findings["workflows_for_media"])
            findings["verdict"] = (
                "stored id IS the workflow id"
                if stored in wf
                else "stored id is NOT the workflow id"
                if wf
                else "media not found in listing"
            )
        step("result", json.dumps(findings))
    out = default_out_path("video_workflow_id")
    out.write_text(json.dumps(findings, indent=2), encoding="utf-8")
    step("wrote", str(out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="ci-probe")
    ap.add_argument("--project", required=True)
    ap.add_argument("--media", required=True)
    ap.add_argument("--stored", required=True)
    a = ap.parse_args()
    raise SystemExit(asyncio.run(_main(a.profile, a.project, a.media, a.stored)))
