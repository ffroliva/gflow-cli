"""Spike: does the labs.google tRPC lane 401 without origin/referer? (#578)

We do not guess, we verify. PR #578 claims a tRPC MUTATION rejected for lacking
`origin`/`referer` answers 401. That claim is inference from a commit message
plus two third-party clients — nothing in our tree had ever observed it.

Experiment (A/B, interleaved, one session):

    A = control  — headers exactly as `develop` sends today: content-type only
    B = treatment — content-type + origin + referer (what PR #578 sends)

Both run against the SAME live `project.createProject`, on the same page, the
same cookie jar, within seconds of each other, alternating A/B/A/B so ordering,
rate limiting or session decay cannot masquerade as the effect.

Costs no Veo/Imagen credits — createProject is a tRPC mutation, not a
generation. Successful calls DO create real projects, named `spike401-*` so
they are obvious to delete afterwards.

Run:
    uv run python scripts/dev/spike_trpc_origin_referer_401.py --profile ffroliva
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, default_out_path, resolve_profile_dir  # noqa: E402

TRPC_CREATE = "https://labs.google/fx/api/trpc/project.createProject"
LABS_ORIGIN = "https://labs.google"
LABS_REFERER = "https://labs.google/fx/tools/flow"

ARMS = {
    # exactly what develop sends today
    "A_control_no_origin": {"content-type": "application/json"},
    # exactly what PR #578 sends
    "B_with_origin_referer": {
        "content-type": "application/json",
        "origin": LABS_ORIGIN,
        "referer": LABS_REFERER,
    },
}


async def one_call(page: Any, arm: str, headers: dict[str, str], n: int) -> dict[str, Any]:
    """Fire one createProject and record what ACTUALLY went on the wire.

    Critical control: `page.request` is bound to the page's context, and
    Playwright/Chromium may add `origin`/`referer` of its own accord. If it
    does, the "control" arm is not origin-less at all and the whole A/B is
    meaningless. So we capture the real outgoing headers rather than trusting
    the dict we passed in.
    """
    seen: list[dict[str, str]] = []

    def on_request(req: Any) -> None:
        if "createProject" in req.url:
            try:
                seen.append(dict(req.headers))
            except Exception:  # noqa: BLE001
                pass

    page.on("request", on_request)
    body = {"json": {"projectTitle": f"spike401-{arm}-{n}", "toolName": "PINHOLE"}}
    try:
        resp = await page.request.post(TRPC_CREATE, data=json.dumps(body), headers=headers)
    finally:
        page.remove_listener("request", on_request)
    actual = seen[-1] if seen else {}
    text = ""
    try:
        text = (await resp.text())[:300]
    except Exception as exc:  # noqa: BLE001
        text = f"<unreadable: {exc}>"
    return {
        "arm": arm,
        "iteration": n,
        "status": resp.status,
        "headers_we_passed": sorted(headers),
        "headers_actually_sent": sorted(actual),
        "actual_origin": actual.get("origin"),
        "actual_referer": actual.get("referer"),
        "body_prefix": text,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--rounds", type=int, default=2)
    args = ap.parse_args()

    results: list[dict[str, Any]] = []
    async with build_client(resolve_profile_dir(args.profile)) as client:
        page = await client._checkout_page()
        try:
            # Control the control: a dead session 401s BOTH arms, which would
            # read as "no difference" and falsely refute the hypothesis. The
            # session endpoint is a GET (not a mutation), so it does not depend
            # on the header under test.
            probe = await page.request.get("https://labs.google/fx/api/auth/session")
            probe_text = (await probe.text())[:200]
            alive = probe.status == 200 and '"user"' in probe_text
            print(f"  session probe -> HTTP {probe.status} alive={alive}")
            if not alive:
                print("\n  ABORT: session is not live. Result would be uninterpretable.")
                print(f"  probe body: {probe_text[:160]}")
                return 2
            for n in range(1, args.rounds + 1):
                for arm, headers in ARMS.items():
                    r = await one_call(page, arm, headers, n)
                    results.append(r)
                    print(
                        f"  {arm:<24} round {n} -> HTTP {r['status']} "
                        f"| wire origin={r['actual_origin']!r} referer={r['actual_referer']!r}"
                    )
                    await asyncio.sleep(1.0)
        finally:
            client._checkin_page(page)

    out = default_out_path("spike_trpc_origin_referer_401", ".json")
    Path(out).write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("\n--- VERDICT ---")
    for arm in ARMS:
        st = [r["status"] for r in results if r["arm"] == arm]
        print(f"  {arm:<24} statuses={st}")
    a = {r["status"] for r in results if r["arm"] == "A_control_no_origin"}
    b = {r["status"] for r in results if r["arm"] == "B_with_origin_referer"}
    if a == b:
        print("\n  NO DIFFERENCE — origin/referer does not change the outcome.")
        print("  PR #578's premise is NOT supported by this run.")
    elif a == {401} and b == {200}:
        print("\n  CONFIRMED — origin-less mutation 401s; with origin/referer it succeeds.")
    else:
        print("\n  MIXED — read the JSON before concluding anything.")
    print(f"\nevidence: {out}")
    print("cleanup: delete any projects named spike401-*")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
