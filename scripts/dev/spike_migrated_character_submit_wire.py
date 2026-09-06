r"""What wire carries the migrated character portrait generation? (image quota, 0 credits)

Live 2026-09-06, `character create` on flow.google.com now reaches:

    character_editor_ready -> character_model_selected -> prompt_input_found
    -> prompt_submitted -> "No batchGenerateImages response within 180.0s"

Everything up to and including the submit works. Only the *listener* is wrong:
gflow waits for ``aisandbox-pa.googleapis.com/.../flowMedia:batchGenerateImages``,
the labs wire. The provenance spike showed the migrated frontend adds a
``flow.google.com/.../batchexecute`` wire on top of the shared backend, so the
portrait submit almost certainly lands there instead.

This spike drives the REAL production path — the transport's own
``generate_character_images`` — with a recorder attached to every request and
response, so what we learn is what gflow itself would see. It records:

  * every batchexecute rpcid fired after the click, in order
  * every aisandbox call, to confirm whether the labs wire is truly silent
  * response body heads, scanned for a workflow id / media id shape

The entity is deleted afterwards. Cost: one real portrait generation — image
quota, zero credits (see AGENTS.md "two cost currencies").

    python scripts/dev/spike_migrated_character_submit_wire.py \
        --profile ci-probe --project 1e4efe0d-afcf-4e0d-ae4d-b4431f2d73de
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gflow_cli.api.character import CharacterImageRequest  # noqa: E402

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

# A Flow workflow name looks like `projects/<uuid>/workflows/<uuid>`; media ids
# are bare uuids. Both are what the entity PATCH needs, so both are worth
# spotting in a response body we do not yet know the schema of.
_WORKFLOW_RE = re.compile(r"projects/[0-9a-f-]{36}/workflows/[0-9a-zA-Z_-]{6,}")
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")


def _rpcids(url: str) -> list[str]:
    if "batchexecute" not in url or "rpcids=" not in url:
        return []
    raw = url.split("rpcids=", 1)[1].split("&", 1)[0]
    return [p for p in raw.split("%2C") if p]


async def _main(profile: str, project: str) -> int:
    profile_dir = resolve_profile_dir(profile)
    step("profile", f"{profile} -> {profile_dir}")
    findings: dict[str, Any] = {"profile": profile, "project": project}

    async with build_client(profile_dir) as client:
        entity_id = await client.create_entity(project)
        step("entity", f"created {entity_id} (free tRPC)")
        findings["entity_id"] = entity_id

        transport = client.transport
        page = transport._page  # noqa: SLF001 - spike watches the real page
        if page is None:
            step("abort", "transport exposed no page")
            return 1

        calls: list[dict[str, Any]] = []

        async def _on_response(resp: Any) -> None:
            url = resp.url
            host = urlsplit(url).netloc
            is_batch = "batchexecute" in url
            is_sandbox = "aisandbox" in host
            if not (is_batch or is_sandbox):
                return
            rec: dict[str, Any] = {
                "kind": "batchexecute" if is_batch else "aisandbox",
                "status": resp.status,
                "rpcids": _rpcids(url),
                "path": urlsplit(url).path[:80],
            }
            try:
                body = await resp.text()
            except Exception:  # noqa: BLE001 - body may be consumed/streamed
                body = ""
            rec["len"] = len(body)
            rec["workflows"] = sorted(set(_WORKFLOW_RE.findall(body)))[:5]
            # uuids are noisy; only record them when a workflow-ish key is near
            if '"workflow' in body.lower() or "mediaid" in body.lower().replace("_", ""):
                rec["uuid_sample"] = sorted(set(_UUID_RE.findall(body)))[:5]
            rec["head"] = body[:220]
            calls.append(rec)

        page.on("response", lambda r: asyncio.create_task(_on_response(r)))

        try:
            step("drive", "invoking the transport's own generate_character_images")
            req = CharacterImageRequest(
                prompt="a calm portrait of a woman, neutral background",
                model="nano2",
            )
            try:
                await transport.generate_character_images(  # type: ignore[attr-defined]
                    project_id=project,
                    entity_id=entity_id,
                    request=req,
                    image_reference_index=0,
                    locale=None,
                )
                step("drive", "returned WITHOUT error — the labs wire answered after all")
                findings["production_path"] = "succeeded"
            except Exception as exc:  # noqa: BLE001 - the failure IS the measurement
                step("drive", f"failed as expected: {type(exc).__name__}: {str(exc)[:120]}")
                findings["production_path"] = f"{type(exc).__name__}: {str(exc)[:200]}"

            await page.wait_for_timeout(3000)
            findings["calls"] = calls
            batch = [c for c in calls if c["kind"] == "batchexecute"]
            sandbox = [c for c in calls if c["kind"] == "aisandbox"]
            step("wire", f"batchexecute responses={len(batch)}  aisandbox responses={len(sandbox)}")
            for c in batch:
                if c["rpcids"] or c["workflows"]:
                    step(
                        "batchexecute",
                        f"rpcids={c['rpcids']} status={c['status']} len={c['len']} "
                        f"workflows={c['workflows']}",
                    )
            for c in sandbox:
                step("aisandbox", f"{c['path']} status={c['status']} len={c['len']}")
            hits = [c for c in calls if c["workflows"]]
            step(
                "verdict",
                f"{len(hits)} response(s) carried a workflow name -> "
                + (str(hits[0]["rpcids"] or hits[0]["path"]) if hits else "NONE FOUND"),
            )
        finally:
            try:
                await client.delete_characters(project, [entity_id])
                step("cleanup", f"deleted {entity_id}")
            except Exception as exc:  # noqa: BLE001 - cleanup is best-effort
                step("cleanup", f"FAILED to delete {entity_id}: {exc}")
            out = default_out_path("migrated_character_submit_wire")
            out.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
            step("wrote", str(out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="ci-probe")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.profile, args.project)))
