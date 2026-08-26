"""Did Flow actually use the model we asked for? Ask the server, both arms.

The catalog records attribution from the classic RESPONSE (observed) or, on the
agentic arm, from the REQUEST we sent (assumed). Same column, no distinction.

Observed live 2026-08-26: an agentic run requested GEM_PIX_2 (Nano Banana Pro),
Flow generated with NARWHAL, the CLI exited 0 and printed GEM_PIX_2. Nothing in
the product noticed.

This compares INTENT against SERVER TRUTH via `flow.projectInitialData`, which
carries `modelNameType` / `seed` / `aspectRatio` for BOTH arms keyed by the media
UUID — through a fetcher gflow already has (~0.5s, cookie auth, no credits).

    uv run python scripts/dev/verify_model_attribution.py \
        --profile denon82 --project <pid> --media <uuid> --expected GEM_PIX_2

Omit --media to audit every media in the project against the catalog.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spike_common import build_client, resolve_profile_dir  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from gflow_cli.services.catalog_sync import parse_media_attribution  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--media", help="a single media UUID to check")
    ap.add_argument("--expected", help="the model that was REQUESTED")
    args = ap.parse_args()

    async with build_client(resolve_profile_dir(args.profile)) as client:
        listing = await client.fetch_project_listing(args.project)

    attrs = parse_media_attribution(listing)
    if not attrs:
        # Never report this as "the server attributes nothing" — an empty read is
        # indistinguishable from a listing we failed to parse, and #539 records
        # the cost of treating emptiness as absence.
        print("no attributed media found — INCONCLUSIVE, not proof of absence")
        return 2

    rows = {args.media: attrs[args.media]} if args.media and args.media in attrs else attrs
    if args.media and args.media not in attrs:
        print(f"media {args.media} not in listing (freshness? try again shortly)")
        return 2

    print(f"{'media':<38}{'server model':<14}{'seed':<10}aspect")
    print("-" * 88)
    for uuid, a in rows.items():
        print(f"{uuid:<38}{str(a.model_name_type):<14}{str(a.seed):<10}{a.aspect_ratio}")

    if args.expected:
        mismatched = [
            (u, a.model_name_type)
            for u, a in rows.items()
            if a.model_name_type and a.model_name_type != args.expected
        ]
        print("\n=== ATTRIBUTION CHECK ===")
        print(f"  requested : {args.expected}")
        if mismatched:
            for u, got in mismatched:
                print(f"  MISMATCH  : {u} generated with {got!r}, not {args.expected!r}")
            print("\n  The catalog records the REQUEST on the agentic arm, so this")
            print("  mismatch is invisible in `gflow data list images`.")
            return 1
        print("  all media match the requested model")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
