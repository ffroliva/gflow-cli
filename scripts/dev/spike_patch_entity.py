#!/usr/bin/env python3
r"""T-D spike — capture the real PATCH flow/entities response body (0 credits).

Purpose: PATCH ``flow/entities`` with a minimal ``entityInfo.displayName``
update and capture the raw server response.  This gives us a real-wire fixture
for ``tests/api/fixtures/patch_entity_response.json`` without spending credits.

Credit cost: 0  (Bearer REST PATCH, no generation).

Usage example:

    ! .venv\Scripts\python.exe scripts\dev\spike_patch_entity.py \
        --profile denon82 --project <existing-project-uuid>

    # With an existing entity (skip REST create):
    ! .venv\Scripts\python.exe scripts\dev\spike_patch_entity.py \
        --profile denon82 --project <pid> --entity <eid> --name "My Spike"

Outputs go to scripts/dev/_spike_out/ (gitignored) or OS temp dir.
The captured response is redacted (signed URLs / tokens removed) before write.
Intended fixture destination: tests/api/fixtures/patch_entity_response.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.client import FlowApiClient  # noqa: E402
from gflow_cli.data.redaction import redact_metadata  # noqa: E402

# ---------------------------------------------------------------------------
# tRPC helper (reused from character_create_spike_v2)
# ---------------------------------------------------------------------------


def _unwrap_trpc(data: Any) -> dict[str, Any]:
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        msg = f"unexpected tRPC reply shape: {type(data).__name__}"
        raise ValueError(msg)
    result = data.get("result", {})
    inner = result.get("data", {}) if isinstance(result, dict) else {}
    payload = inner.get("json", inner) if isinstance(inner, dict) else {}
    if not isinstance(payload, dict):
        msg = "tRPC reply missing result.data.json object"
        raise ValueError(msg)
    return payload


async def _create_entity(client: FlowApiClient, project_id: str) -> str:
    """Mint a fresh throwaway CHARACTER entity. FREE (no reCAPTCHA/credit)."""
    body = {"json": {"projectId": project_id}}
    data = await client._post_json(  # noqa: SLF001
        routes.CREATE_ENTITY_URL,
        body,
        content_type="application/json",
        route_name="createEntity",
    )
    payload = _unwrap_trpc(data)
    entity_id = payload.get("entityId")
    if not entity_id:
        msg = f"createEntity returned no entityId; keys={sorted(payload)}"
        raise ValueError(msg)
    step("0 OK", f"minted entityId={entity_id}", prefix="T-D")
    return str(entity_id)


async def _run(
    *,
    profile_dir: Path,
    headless: bool,
    project_id: str,
    entity_id: str | None,
    display_name: str,
    out_path: Path,
) -> int:
    async with build_client(profile_dir, headless=headless) as client:
        # Step 0 — ensure we have an entity.
        if not entity_id:
            step("0", "no --entity provided, creating throwaway entity…", prefix="T-D")
            entity_id = await _create_entity(client, project_id)
        else:
            step("0 SKIP", f"using provided entity={entity_id}", prefix="T-D")

        # Step 1 — build minimal PATCH body.
        # Wire shape mirrors docs/CHARACTER.md §6 Step 3 and character_create_spike.py
        # _save_character(), but restricted to displayName only (smallest valid PATCH).
        body: dict[str, Any] = {
            "entity": {
                "projectId": project_id,
                "entityId": entity_id,
                "entityInfo": {
                    "displayName": display_name,
                },
            },
            "updateMask": "entityInfo.displayName",
        }

        step("1", f"PATCHing flow/entities (displayName={display_name!r})…", prefix="T-D")
        response_data = await client._patch_json(  # noqa: SLF001
            routes.FLOW_ENTITIES_URL,
            body,
            route_name="spikePatchEntity",
        )

    step("1 OK", "PATCH flow/entities returned successfully", prefix="T-D")

    # Step 2 — redact and write fixture.
    redacted = redact_metadata(response_data)
    redacted = _deep_redact_signed_strings(redacted)

    # Wrap with spike metadata so the fixture is self-describing.
    output: dict[str, Any] = {
        "spike": "T-D",
        "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "projectId": project_id,
        "entityId": entity_id,
        "displayName": display_name,
        "response": redacted,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n[T-D] response written to:\n      {out_path}", flush=True)
    print(
        "[T-D] To use as a fixture (response field only):\n"
        f"      copy {out_path}\n"
        "            tests\\api\\fixtures\\patch_entity_response.json\n"
        "      (extract the 'response' key or use the whole envelope — your choice)",
        flush=True,
    )

    # Pretty-print the redacted response for quick review.
    print("\n[T-D] --- redacted response ---", flush=True)
    print(json.dumps(redacted, indent=2, ensure_ascii=False), flush=True)

    return 0


_SIGNED_URL_MARKERS = ("signature=", "x-goog-signature=", "expires=", "x-goog-credential=")


def _deep_redact_signed_strings(obj: Any) -> Any:
    """Extra pass: replace string values that look like signed CDN URLs."""
    if isinstance(obj, dict):
        return {k: _deep_redact_signed_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_redact_signed_strings(item) for item in obj]
    if isinstance(obj, str) and any(m in obj.lower() for m in _SIGNED_URL_MARKERS):
        return "<REDACTED_SIGNED_URL>"
    return obj


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="T-D spike: capture PATCH flow/entities response (0 credits)."
    )
    parser.add_argument(
        "--profile",
        default=os.environ.get("GFLOW_CLI_PROFILE", "denon82"),
        help="Chrome-strategy profile name. Default: denon82 / $GFLOW_CLI_PROFILE.",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Existing Flow project UUID.",
    )
    parser.add_argument(
        "--locale",
        default="pt",
        help="Flow UI locale (default: pt; unused by PATCH but kept for parity with other spikes).",
    )
    parser.add_argument(
        "--entity",
        default=None,
        help="Existing entity UUID. If omitted, a fresh throwaway entity is created.",
    )
    parser.add_argument(
        "--name",
        default="Spike Test",
        help="displayName to write via the PATCH (default: 'Spike Test').",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSON path. Default: scripts/dev/_spike_out/spike_patch_entity_<ts>.json",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headless (default: headed).",
    )
    args = parser.parse_args(argv)

    profile_dir = resolve_profile_dir(args.profile)
    out_path = Path(args.out) if args.out else default_out_path("spike_patch_entity", ".json")

    step(
        "--",
        f"profile={args.profile} project={args.project} entity={args.entity or '(create)'} "
        f"name={args.name!r} out={out_path}",
        prefix="T-D",
    )
    print("[T-D] NOTE: this run spends 0 credits.", flush=True)

    try:
        return asyncio.run(
            _run(
                profile_dir=profile_dir,
                headless=args.headless,
                project_id=args.project,
                entity_id=args.entity,
                display_name=args.name,
                out_path=out_path,
            )
        )
    except KeyboardInterrupt:
        print("[T-D] aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
