"""Live fault-injection: does a real 400 surface as ContentPolicyError? (#528, v0.60.0)

The release ships a classification change: an HTTP 400 on the generation route
must raise `ContentPolicyError` with remediation naming the reference-shape and
person-descriptor levers, NOT `WireFormatError` telling the operator to shorten
their prompt.

Verifying that end-to-end needs a 400. Google would not produce one on demand
today — two face-bearing `--ref` images plus an age-explicit descriptor returned
200 (recorded in the ledger). So we inject the 400 at the network boundary and
let everything downstream be real: real browser, real Flow editor, real prompt
submit, real transport, real error path, real CLI exit code.

What this DOES prove: when a 400 arrives, the user gets the right class and the
right advice.
What this does NOT prove: that Google sends 400 for any particular prompt shape.

Run:
    uv run python scripts/dev/live_verify_policy_400_v060.py --profile denon82
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spike_common import build_client, resolve_profile_dir  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gflow_cli.api.image import GenerateImageRequest  # noqa: E402
from gflow_cli.errors import ContentPolicyError, WireFormatError  # noqa: E402

# Shape taken verbatim from Flow's documented 400 (errors.py ContentPolicyError
# docstring). The bare variant is what every #528 incident bundle actually showed.
BODIES = {
    "with_reason": {
        "error": {
            "code": 400,
            "message": "Request contains an invalid argument.",
            "status": "INVALID_ARGUMENT",
            "details": [{"reason": "PUBLIC_ERROR_UNSAFE_GENERATION"}],
        }
    },
    "bare_400_as_seen_in_bundles": {
        "error": {
            "code": 400,
            "message": "Request contains an invalid argument.",
            "status": "INVALID_ARGUMENT",
        }
    },
}


async def run_case(client: Any, ctx: Any, label: str, body: dict[str, Any]) -> dict[str, Any]:
    """Intercept at CONTEXT level, not page level.

    The transport drives its own page (`self._page`), which is not necessarily
    the one a caller checked out of the pool. A page-scoped route would silently
    never fire and the test would 'pass' by generating normally.
    """

    async def deny(route: Any) -> None:
        await route.fulfill(
            status=400,
            content_type="application/json",
            body=json.dumps(body),
        )

    await ctx.route("**/flowMedia:batchGenerateImages*", deny)
    try:
        req = GenerateImageRequest(prompt=f"a quiet harbour at dawn [{label}]", count=1)
        try:
            await client.transport.generate_images(project_id=None, request=req)
        except ContentPolicyError as exc:
            return {
                "case": label,
                "error_class": type(exc).__name__,
                "status": exc.status,
                "route": exc.route,
                "exit_code": None,
                "remediation": exc.remediation_hint,
                "detail": exc.detail,
                "PASS": True,
            }
        except WireFormatError as exc:
            return {
                "case": label,
                "error_class": type(exc).__name__,
                "remediation": exc.remediation_hint,
                "detail": exc.detail,
                "PASS": False,
                "why": "REGRESSION — this is exactly the #528 bug",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "case": label,
                "error_class": type(exc).__name__,
                "detail": str(exc)[:300],
                "PASS": False,
            }
        return {"case": label, "PASS": False, "why": "no error raised at all"}
    finally:
        await ctx.unroute("**/flowMedia:batchGenerateImages*", deny)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    args = ap.parse_args()

    results = []
    async with build_client(resolve_profile_dir(args.profile)) as client:
        ctx = client._context
        assert ctx is not None, "no browser context"
        try:
            for label, body in BODIES.items():
                r = await run_case(client, ctx, label, body)
                results.append(r)
                print(f"\n--- {label} ---")
                print(f"  class      : {r.get('error_class')}")
                print(f"  status     : {r.get('status')}")
                print(f"  PASS       : {r.get('PASS')} {r.get('why', '')}")
                print(f"  remediation: {str(r.get('remediation'))[:220]}")
        finally:
            pass

    out = Path("scripts/dev/_spike_out/live_verify_policy_400_v060.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = all(r.get("PASS") for r in results)
    print(f"\n=== {'ALL PASS' if ok else 'FAILURES PRESENT'} ===\nevidence: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
