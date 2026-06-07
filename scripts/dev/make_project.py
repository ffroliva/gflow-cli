#!/usr/bin/env python3
"""Dev utility: create a fresh Flow project and print its id.

! .venv\\Scripts\\python.exe scripts\\dev\\make_project.py --profile denon82 --title "Compiled Growth — Stacky"
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


async def _run(profile: str, title: str) -> int:
    pdir = resolve_profile_dir(profile)
    async with build_client(pdir, headless=False) as client:
        proj = await client.create_project(title=title)
        print(f"PROJECT_ID: {proj.project_id}", flush=True)
        print(f"TITLE: {title}", flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--profile", default="denon82")
    p.add_argument("--title", required=True)
    args = p.parse_args()
    return asyncio.run(_run(args.profile, args.title))


if __name__ == "__main__":
    raise SystemExit(main())
