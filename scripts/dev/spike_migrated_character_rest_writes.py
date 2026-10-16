r"""Do the aisandbox REST character writes still work for a flow.google.com account? ($0)

Character-portability plan, Task 5. Only the labs **tRPC** routes are measured retired
(``flow.createEntity``, ``flow.projectInitialData``). gflow's two other character writes
go to aisandbox-pa REST with a Bearer token:

* ``client.patch_entity``      -> ``PATCH /v1/flow/entities`` (name, images, voice, personality)
* ``client.delete_characters`` -> ``POST /v1/flow:batchDeleteAssets``

Nobody has run them against an account Flow serves from flow.google.com. If they work,
patch and delete need no editor driving; if not, the error says which way they fail.

Pre-registered reading:

* patch returns 2xx AND a fresh ``Zzl0ze`` read (after the read-lag settles) shows the
  new values -> REST patch works on this account.
* patch 2xx but the read never changes -> the write is accepted and dropped: unusable.
* 401 / 403 / 404 -> REST is closed for this account; the editor is the only write path.
* delete: the entity is gone from a settled read -> works.

Run on THROWAWAY spike entities only — this writes and deletes.

    uv run python scripts/dev/spike_migrated_character_rest_writes.py \
        --profile sykamel --project <id> --patch-entity <id> --delete-entity <id>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gflow_cli.api.transports import migrated_character  # noqa: E402

from _spike_common import build_client, resolve_profile_dir, step  # noqa: E402, isort: skip


async def _settled(client: object, project: str, entity: str, want: object, tries: int = 6):  # type: ignore[no-untyped-def]
    """Re-read until the entity matches ``want`` (a predicate) or tries run out."""
    for attempt in range(1, tries + 1):
        page = await client._checkout_page()  # type: ignore[attr-defined]  # noqa: SLF001
        try:
            read = await migrated_character.read_project(page, project)
        finally:
            client._checkin_page(page)  # type: ignore[attr-defined]  # noqa: SLF001
        row = next((c for c in read.characters if c.entity_id == entity), None)
        step("read", f"attempt {attempt}: {row}")
        if want(row):  # type: ignore[operator]
            return True
        await asyncio.sleep(10)
    return False


async def _main(profile: str, project: str, patch_entity: str, delete_entity: str) -> int:
    async with build_client(resolve_profile_dir(profile)) as client:
        before = await client.list_characters(project)
        target = next(c for c in before if c.entity_id == patch_entity)
        step("before", f"{target}")
        try:
            await client.patch_entity(
                project_id=project,
                entity_id=patch_entity,
                display_name=target.display_name + " REST",
                workflow_ids=list(target.workflow_ids),
                voice="Charon",
                personality="patched over REST",
            )
            step("patch", "returned without error")
            ok = await _settled(
                client,
                project,
                patch_entity,
                lambda r: (
                    r is not None and r.display_name.endswith(" REST") and r.voice == "Charon"
                ),
            )
            step("patch", f"visible on a settled read: {ok}")
        except Exception as exc:  # noqa: BLE001 - the failure IS the measurement
            step(
                "patch",
                f"FAILED: {type(exc).__name__} "
                f"status={getattr(exc, 'status', None)} {str(exc)[:200]}",
            )

        try:
            await client.delete_characters(project, [delete_entity])
            step("delete", "returned without error")
            gone = await _settled(client, project, delete_entity, lambda r: r is None)
            step("delete", f"gone on a settled read: {gone}")
        except Exception as exc:  # noqa: BLE001 - the failure IS the measurement
            step(
                "delete",
                f"FAILED: {type(exc).__name__} "
                f"status={getattr(exc, 'status', None)} {str(exc)[:200]}",
            )
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--patch-entity", required=True)
    ap.add_argument("--delete-entity", required=True)
    a = ap.parse_args()
    raise SystemExit(asyncio.run(_main(a.profile, a.project, a.patch_entity, a.delete_entity)))
