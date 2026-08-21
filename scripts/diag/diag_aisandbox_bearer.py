#!/usr/bin/env python3
"""Diagnose issue #561: is the aisandbox Bearer path broken, or just uploadImage?

The nightly canary (#502) caught ``uploadImage`` returning HTTP 401 while
``gflow auth status`` verified the Flow session clean. The failing call is the
ONLY aisandbox Bearer call in the ``e2e_auth`` tier, so that run cannot tell
these apart:

    H-A  uploadImage specifically is being rejected
    H-B  the whole aisandbox Bearer path is failing, and uploadImage is merely
         the one place we happen to touch it

This script issues a **read-only** aisandbox call and the uploadImage POST with
the *same* token, in one session, and prints both statuses. Different statuses
=> H-A. Both 401 => H-B, which is far more serious.

Cost: $0 — a project create (labs.google tRPC, cookie auth), one entity read,
and one image upload. None spends a generation credit; none touches reCAPTCHA.

Credentials are never printed: the token is reported by length and prefix only.
Output DOES include a project id and a truncated authenticated response body —
review it before pasting into a public issue.

Usage:
    python scripts/diag/diag_aisandbox_bearer.py --profile denon82
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from typing import Any

from gflow_cli.api import routes
from gflow_cli.api.client import _AISANDBOX_CONTENT_TYPE, FlowApiClient
from gflow_cli.auth import profile_dir as resolve_profile_dir

# Same 1x1 transparent PNG the e2e test uploads — passes the magic-byte check.
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4"
    "2mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _report(label: str, status: int, body: str) -> None:
    verdict = "OK" if status < 400 else "REJECTED"
    print(f"\n[{verdict}] {label}: HTTP {status}")
    print(f"        body: {body[:240].strip()}")


async def run(profile_name: str, transport: str | None) -> int:
    profile = resolve_profile_dir(profile_name)
    if not profile.exists():
        print(f"profile not found: {profile}", file=sys.stderr)
        return 2

    kwargs: dict[str, Any] = {"profile_dir": profile}
    if transport:
        kwargs["transport"] = transport
    print(f"transport: {transport or '(default)'}")
    async with FlowApiClient(**kwargs) as client:
        # 1. labs.google tRPC, cookie auth — the known-working control. If this
        #    fails the session really is dead and nothing below means anything.
        project = await client.create_project(title="diag #561 bearer probe")
        print(f"[OK] create_project (labs.google tRPC, cookie auth): {project.project_id}")

        # 2. The Bearer token itself, from the BFF session endpoint.
        token = await client._ensure_access_token()  # noqa: SLF001 - diagnostic
        looks_right = token.startswith("ya29.")
        print(
            f"[OK] access_token from /fx/api/auth/session: "
            f"len={len(token)} prefix={token[:5]}… ya29={looks_right}"
        )

        headers: dict[str, str] = await client._aisandbox_auth_headers()  # noqa: SLF001
        print(f"     header keys sent to aisandbox: {sorted(headers)}")

        ctx = client._context  # noqa: SLF001
        if ctx is None:
            print("no browser context", file=sys.stderr)
            return 2

        # 3. READ-ONLY aisandbox Bearer call. Same token, different endpoint.
        entities_url = f"{routes.FLOW_ENTITIES_URL}?projectId={project.project_id}"
        r_read = await ctx.request.get(entities_url, headers=headers)
        _report("aisandbox GET flow/entities (read-only)", r_read.status, await r_read.text())

        # 4. The failing call, same session, same token.
        body: dict[str, Any] = {
            "clientContext": {"projectId": project.project_id, "tool": "PINHOLE"},
            "imageBytes": base64.b64encode(_PNG_1X1).decode(),
        }
        r_up = await ctx.request.post(
            routes.UPLOAD_IMAGE,
            headers={**headers, "content-type": _AISANDBOX_CONTENT_TYPE},
            data=json.dumps(body),
        )
        _report("aisandbox POST flow/uploadImage", r_up.status, await r_up.text())

        print("\n--- verdict ---")
        if r_read.status < 400 and r_up.status >= 400:
            print("H-A: Bearer auth works; uploadImage specifically is rejected.")
        elif r_read.status >= 400 and r_up.status >= 400:
            print("H-B: the whole aisandbox Bearer path is rejected. Escalate.")
        else:
            # r_up < 400 is the only remaining case: the three arms are a total
            # cover, so a trailing `else` here would be unreachable.
            print("Neither reproduces — uploadImage succeeded. Suspect intermittency.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", required=True)
    p.add_argument("--transport", default=None, help="e.g. evaluate_fetch; omit for default")
    a = p.parse_args()
    return asyncio.run(run(a.profile, a.transport))


if __name__ == "__main__":
    raise SystemExit(main())
