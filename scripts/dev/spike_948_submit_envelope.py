r"""#948: what shape do the migrated host's video submit / status replies have NOW? (~10 credits)

Three reporters (2026-10-05) say a migrated-host video submit is accepted and billed but
gflow exits 7 with ``batchexecute <rpc>: no generation record ([uuid, uuid, uuid, 'CAE', …])``.
Their capture: the submit reply is ``[null, <balance>, [[<media>, null, null, [<title>, <ts>,
null, null, <workflow>, <UUID>, <ts>], <project>]]]`` and the ``as29s`` record carries
``null`` where ``"CAE"`` used to be.

This drives ONE veo-lite t2v through gflow's own client (the production path) and records
the SKELETON of every ``batchexecute`` frame whose rpcid is a submit or status rpc: UUIDs
become ``<uuid>``, URLs ``<url>``, other strings ``<str>``, ints kept (status / balance /
size are the question). No prompt text, token or URL leaves the process.

PRE-REGISTERED READING (written before the run):

  * gflow raises WireFormatError AND the submit skeleton has no slot-3 ``"CAE"``
    -> #948 reproduces on our account; the reporters' envelope shape is measured.
  * status (jwpduf / as29s) records show ``null`` at slot 3
    -> ``_is_record`` must accept it too (the recover / data download half).
  * the run SUCCEEDS and every record still says ``"CAE"``
    -> does not reproduce here; settles nothing (cohort / rollout). The skeleton is still
       the fixture of the old shape; ask a reporter for theirs.

    python scripts/dev/spike_948_submit_envelope.py --profile <name>
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

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402, isort: skip

from gflow_cli.api.transports.batchexecute import (  # noqa: E402
    _UUID_RE,  # pyright: ignore[reportPrivateUsage]
    _is_record,  # pyright: ignore[reportPrivateUsage]
    _walk_lists,  # pyright: ignore[reportPrivateUsage]
    generation_record,
    parse_frames,
)
from gflow_cli.api.transports.migrated_composer import STATUS_RPCS, SUBMIT_RPCS  # noqa: E402
from gflow_cli.api.video import GenerateVideoRequest, Mode, VideoModel  # noqa: E402
from gflow_cli.data.redaction import redact_error_detail  # noqa: E402

_UUID = _UUID_RE
_KEEP = {"CAE"}


def skeleton(node: Any) -> Any:
    if isinstance(node, list):
        return [skeleton(c) for c in node]  # type: ignore[misc]
    if isinstance(node, str):
        if node in _KEEP:
            return node
        if _UUID.match(node):
            return "<uuid>"
        return "<url>" if node.startswith("https://") else "<str>"
    return node  # int / float / bool / None: kept


async def _main(profile: str, project: str | None, out: Path) -> int:
    frames: list[dict[str, Any]] = []
    wanted = set(SUBMIT_RPCS) | set(STATUS_RPCS)
    timeline: list[str] = []
    wf_labels: dict[str, str] = {}
    t0 = time.monotonic()

    async def on_response(response: Any) -> None:
        if "batchexecute" not in str(getattr(response, "url", "")):
            return
        try:
            text = await response.text()
        except Exception:  # noqa: BLE001 - body may be gone
            return
        for rid, payload in parse_frames(text):
            if rid in wanted:
                frames.append({"rpcid": rid, "skeleton": skeleton(payload)})
            # Every record on ANY rpc: which rpc carries the terminal status? (#948 run 2)
            for node in _walk_lists(payload):
                if not _is_record(node):
                    continue
                rec = generation_record(rid, node)
                wf = wf_labels.setdefault(rec.workflow_id, f"W{len(wf_labels)}")
                timeline.append(
                    f"{time.monotonic() - t0:6.1f}s {rid:7} {wf} status={rec.status} "
                    f"url={bool(rec.video_url)}"
                )

    outcome: dict[str, Any] = {}
    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 - dev instrument
        assert page is not None
        page.context.on("response", on_response)
        req = GenerateVideoRequest(
            prompt="A paper boat drifting on a calm pond, morning light",
            mode=Mode.T2V,
            model=VideoModel.VEO_3_1_LITE,
        )
        step("submit", f"model={req.model} (spends credits)")
        try:
            res = await client.generate_video(
                req=req, project_id=project, out_dir=out.parent, poll_timeout_s=600
            )
            outcome = {"ok": True, "media_id_present": bool(getattr(res, "media_id", None))}
        except Exception as exc:  # noqa: BLE001 - the error IS the observation
            detail = redact_error_detail(str(exc)[:300])
            outcome = {"ok": False, "error": type(exc).__name__, "detail": detail}
        await asyncio.sleep(3)

    out.write_text(
        json.dumps({"outcome": outcome, "frames": frames, "timeline": timeline}, indent=1),
        encoding="utf-8",
    )
    step("done", f"{outcome} frames={len(frames)} -> {out}")
    for f in frames[:3]:
        step(f["rpcid"], json.dumps(f["skeleton"])[:400])
    for line in timeline:
        step("record", line)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", default=None)
    args = ap.parse_args()
    out = default_out_path("spike_948_submit_envelope")
    sys.exit(asyncio.run(_main(args.profile, args.project, out)))
