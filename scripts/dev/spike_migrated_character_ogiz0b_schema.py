r"""Full wire capture for the migrated character portrait generation (image quota).

``spike_migrated_character_submit_wire.py`` established that the portrait submit
on flow.google.com answers over ``batchexecute`` rpcid **ogiZ0b**, whose response
echoes the prompt back alongside two UUIDs. gflow meanwhile waits 180 s for the
labs ``batchGenerateImages`` wire that never fires there.

To drive it we need the schema, not a 220-char head. This spike records, in full:

  * the REQUEST body of every batchexecute after the click (the submit shape)
  * the RESPONSE body of every batchexecute (the result shape)
  * whether the entity picks up workflow ids server-side regardless of what the
    client managed to read — i.e. did the generation actually happen?
  * the project's media before and after, so a produced portrait is visible even
    if the client never parsed it

Bodies go to a JSON file, never to stdout — some are 30 KB. Console output is a
one-line-per-call index.

Cost: one real portrait generation — image quota, zero credits.

    python scripts/dev/spike_migrated_character_ogiz0b_schema.py \
        --profile ci-probe --project 1e4efe0d-afcf-4e0d-ae4d-b4431f2d73de
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gflow_cli.api.character import CharacterImageRequest  # noqa: E402

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

_TARGETS = {"ogiZ0b", "WuwhI", "yBhWQ", "ngNC2", "Zzl0ze", "tRARke", "HTrJv"}


def _rpcids(url: str) -> list[str]:
    if "batchexecute" not in url or "rpcids=" not in url:
        return []
    raw = url.split("rpcids=", 1)[1].split("&", 1)[0]
    return [p for p in raw.split("%2C") if p]


async def _entity_snapshot(client: Any, project: str, entity_id: str) -> dict[str, Any]:
    """Ask the BACKEND what it thinks of the entity — independent of the driver."""
    try:
        chars = await client.list_characters(project)
    except Exception as exc:  # noqa: BLE001 - snapshot is diagnostic
        return {"error": f"{type(exc).__name__}: {exc}"}
    for c in chars:
        if getattr(c, "entity_id", None) == entity_id:
            return {
                "found": True,
                "display_name": getattr(c, "display_name", None),
                "workflow_ids": list(getattr(c, "workflow_ids", []) or []),
                "thumbnail_media_id": getattr(c, "thumbnail_media_id", None),
            }
    return {"found": False, "entity_count": len(chars)}


async def _main(profile: str, project: str) -> int:
    profile_dir = resolve_profile_dir(profile)
    step("profile", f"{profile} -> {profile_dir}")
    findings: dict[str, Any] = {"profile": profile, "project": project}

    async with build_client(profile_dir) as client:
        entity_id = await client.create_entity(project)
        step("entity", f"created {entity_id} (free tRPC)")
        findings["entity_id"] = entity_id
        findings["entity_before"] = await _entity_snapshot(client, project, entity_id)

        transport = client.transport
        page = transport._page  # noqa: SLF001 - spike watches the real page
        if page is None:
            step("abort", "transport exposed no page")
            return 1

        captured: list[dict[str, Any]] = []

        async def _on_response(resp: Any) -> None:
            if "batchexecute" not in resp.url:
                return
            ids = _rpcids(resp.url)
            if not (set(ids) & _TARGETS):
                return
            rec: dict[str, Any] = {"rpcids": ids, "status": resp.status}
            try:
                rec["response_body"] = await resp.text()
            except Exception as exc:  # noqa: BLE001 - body may be gone
                rec["response_body"] = f"<unavailable: {exc}>"
            try:
                req = resp.request
                rec["request_post_data"] = req.post_data
            except Exception as exc:  # noqa: BLE001 - post data may be gone
                rec["request_post_data"] = f"<unavailable: {exc}>"
            captured.append(rec)
            step(
                "captured",
                f"{ids} status={resp.status} "
                f"req={len(rec['request_post_data'] or '')}B "
                f"resp={len(rec['response_body'] or '')}B",
            )

        page.on("response", lambda r: asyncio.create_task(_on_response(r)))

        try:
            step("drive", "generate_character_images (expect the 180 s labs-wire timeout)")
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
                findings["production_path"] = "succeeded"
            except Exception as exc:  # noqa: BLE001 - the failure IS the measurement
                findings["production_path"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            step("drive", str(findings["production_path"])[:120])

            await page.wait_for_timeout(5000)
            findings["captured"] = captured
            findings["entity_after"] = await _entity_snapshot(client, project, entity_id)
            step("entity_after", json.dumps(findings["entity_after"])[:200])
        finally:
            try:
                await client.delete_characters(project, [entity_id])
                step("cleanup", f"deleted {entity_id}")
            except Exception as exc:  # noqa: BLE001 - cleanup is best-effort
                step("cleanup", f"FAILED to delete {entity_id}: {exc}")
            out = default_out_path("migrated_character_ogiz0b_schema")
            out.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
            step("wrote", str(out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="ci-probe")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.profile, args.project)))
