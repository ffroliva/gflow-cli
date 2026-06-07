#!/usr/bin/env python3
"""Dev utility: rename / set voice + personality on an existing CHARACTER entity.

Preserves the entity's current image references (workflow_ids). FREE (Bearer PATCH).

    ! .venv\\Scripts\\python.exe scripts\\dev\\patch_character.py --profile denon82 \\
        --project <pid> --entity <eid> --name "Stacky" --voice algieba --personality "..."
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, resolve_profile_dir  # noqa: E402


async def _run(a: argparse.Namespace) -> int:
    pdir = resolve_profile_dir(a.profile)
    async with build_client(pdir, headless=False) as client:
        chars = await client.list_characters(a.project)
        match = next((c for c in chars if c.entity_id == a.entity), None)
        if match is None:
            print(f"ERROR: entity {a.entity} not found in project {a.project}")
            return 1
        await client.patch_entity(
            project_id=a.project,
            entity_id=a.entity,
            display_name=a.name,
            workflow_ids=list(match.workflow_ids),
            voice=a.voice,
            personality=a.personality,
        )
        print(f"PATCHED {a.entity} -> name={a.name!r} voice={a.voice!r}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--profile", default="denon82")
    p.add_argument("--project", required=True)
    p.add_argument("--entity", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--voice", default=None)
    p.add_argument("--personality", default=None)
    return asyncio.run(_run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
