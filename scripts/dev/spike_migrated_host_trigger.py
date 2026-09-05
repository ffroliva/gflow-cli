r"""What actually decides labs.google vs flow.google.com? ($0, no generation)

The transport's own remediation says the migration "flaps per page load, so
retrying often lands the old frontend" (`api/transports/_common.py:120`), while
recon on 2026-09-03 recorded `ffroliva` on the migrated host 7 loads out of 7.
Both cannot be current, and neither explains the MECHANISM.

They are not two skins on one backend. `flow.google.com` serves
`AiSandboxAngularFrontend` over `batchexecute` with **zero** calls to
`aisandbox-pa.googleapis.com`, authenticated by `.google.com` SSO cookies alone.
`labs.google` is the Next.js app on `aisandbox-pa`, authenticated by
`__Secure-next-auth.session-token`. A per-load flap between two different
applications with two different backends and two different auth systems is not
a plausible rollout behaviour.

HYPOTHESIS UNDER TEST
---------------------
The redirect is driven by the **labs.google NextAuth session cookie**, not by an
account-level rollout flag. The 2026-09-03 capture recorded
`has_labs_next_auth: false` on the migrated profile. If labs.google cannot
establish a session it may bounce the user to the app that needs only SSO --
which would also explain "flapping", since a cookie expiring mid-session flips
the answer without anything being rolled out.

If TRUE, "migrated" is partly an AUTH state, `ffroliva` may be recoverable by
re-authenticating, and #639's framing changes.
If FALSE, the trigger is server-side and we stop guessing at it.

WHAT THIS RECORDS
-----------------
* whether `__Secure-next-auth.session-token` is present for labs.google
* what `GET /fx/api/auth/session` returns (`{}` = unauthenticated, fail-closed)
* the FULL navigation chain -- every status + Location -- so an HTTP 3xx is
  distinguishable from a client-side JS navigation
* which host we ended on, sampled N times to measure flapping directly

Credit-free: navigation and cookie reads only. Nothing is submitted.

    python scripts/dev/spike_migrated_host_trigger.py --profile denon82 --samples 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gflow_cli.api.routes import EDITOR_BOOTSTRAP_URL  # noqa: E402

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

_SESSION_COOKIE = "__Secure-next-auth.session-token"  # noqa: S105 - cookie NAME, not a secret


async def _cookie_state(context: Any) -> dict[str, Any]:
    """Which auth cookies exist, by name only -- values are never recorded."""
    jar = await context.cookies()
    labs = [c for c in jar if "labs.google" in (c.get("domain") or "")]
    google = [c for c in jar if (c.get("domain") or "").endswith(".google.com")]
    return {
        "labs_cookie_count": len(labs),
        "google_sso_cookie_count": len(google),
        "has_labs_next_auth": any(c.get("name") == _SESSION_COOKIE for c in labs),
        "labs_cookie_names": sorted({str(c.get("name")) for c in labs})[:20],
    }


async def _auth_session_probe(context: Any) -> dict[str, Any]:
    """`{}` means unauthenticated. Fail-closed: only a real user.email counts."""
    try:
        resp = await context.request.get("https://labs.google/fx/api/auth/session")
        body = await resp.json()
        return {
            "status": resp.status,
            # Never record the email itself -- only whether one is present.
            "authenticated": bool(isinstance(body, dict) and body.get("user", {}).get("email")),
            "keys": sorted(body.keys()) if isinstance(body, dict) else None,
        }
    except Exception as exc:  # noqa: BLE001 - a spike must record, never abort
        return {
            "status": None,
            "authenticated": None,
            "error": type(exc).__name__,
            "detail": str(exc)[:200],
        }


async def _one_navigation(context: Any, sample: int) -> dict[str, Any]:
    """Navigate once and record the whole redirect chain."""
    page = await context.new_page()
    chain: list[dict[str, Any]] = []

    def _on_response(resp: Any) -> None:
        if 300 <= resp.status < 400 or resp.url.rstrip("/") in (
            EDITOR_BOOTSTRAP_URL.rstrip("/"),
            "https://flow.google.com",
        ):
            chain.append(
                {
                    "url": resp.url[:160],
                    "status": resp.status,
                    "location": (resp.headers or {}).get("location", "")[:160] or None,
                }
            )

    page.on("response", _on_response)
    try:
        await page.goto(EDITOR_BOOTSTRAP_URL, wait_until="domcontentloaded")
        url_at_goto = page.url
        # The migrated host is reached by a client-side hop on some loads, so
        # `page.url` right after goto can still read labs.google -- see memory
        # goto-returns-before-client-side-redirect.
        await page.wait_for_timeout(4000)
        settled = page.url
        return {
            "sample": sample,
            "url_at_goto": url_at_goto[:160],
            "url_settled": settled[:160],
            "host_settled": settled.split("/")[2] if "//" in settled else None,
            "moved_after_goto": url_at_goto != settled,
            "redirect_chain": chain[:12],
        }
    finally:
        await page.close()


async def _main(profile: str, samples: int) -> int:
    profile_dir = resolve_profile_dir(profile)
    step("profile", f"{profile} -> {profile_dir}")

    async with build_client(profile_dir) as client:
        context = client._context  # noqa: SLF001 - spike reads the live context
        cookies = await _cookie_state(context)
        step("cookies", json.dumps(cookies, ensure_ascii=False))
        session = await _auth_session_probe(context)
        step("auth-session", json.dumps(session, ensure_ascii=False))

        runs = []
        for i in range(1, samples + 1):
            run = await _one_navigation(context, i)
            runs.append(run)
            step(f"nav-{i}", f"{run['host_settled']}  moved={run['moved_after_goto']}")

    hosts = [r["host_settled"] for r in runs]
    payload = {
        "profile": profile,
        "note": "credit-free: navigation + cookie reads only, nothing submitted",
        "hypothesis": "the labs->flow redirect is driven by the labs.google NextAuth session",
        "cookies": cookies,
        "auth_session": session,
        "samples": runs,
        "hosts_seen": sorted(set(h for h in hosts if h)),
        "flapped": len(set(h for h in hosts if h)) > 1,
    }
    out = default_out_path("migrated_host_trigger")
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    step("VERDICT", f"hosts={payload['hosts_seen']}  flapped={payload['flapped']}")
    step(
        "READ",
        "has_labs_next_auth="
        f"{cookies['has_labs_next_auth']}  authenticated={session.get('authenticated')}",
    )
    step("out", str(out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="denon82")
    ap.add_argument("--samples", type=int, default=5)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.profile, args.samples)))
