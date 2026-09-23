r"""Does ``project.createProject`` 401 deterministically on accounts served flow.google.com? ($0)

Question (#864, #561, #863): generating without ``--project`` first calls
``labs.google/fx/api/trpc/project.createProject``. On accounts Flow serves from
flow.google.com that call has been seen returning 401 (surfaced as "run gflow auth
login"), and on one such account it later returned 200. Before any behaviour change
we need the outcome distribution, per profile, alongside the host each profile is
served — never inferring one from the other.

Per profile, through gflow's own ``FlowApiClient`` (the production path):

1. the labs session probe: HTTP status and whether it carries an ``access_token``
2. the host served: navigate to the labs Flow root and classify the landed URL
3. ``create_project`` N times: outcome, exception class, status, route

Cost: $0. Creating an empty project spends no credit and no quota. Each success
leaves one empty project titled ``gflow-spike-864`` behind (there is no delete route
in the client); remove them by hand in Flow.

Pre-registered reading (written before the run):

| createProject on a profile     | Reading                                                    |
|--------------------------------|------------------------------------------------------------|
| N/N 401                        | stable refusal for that account today                      |
| N/N 200                        | works for that account today                               |
| mixed                          | flaps — a pre-emptive block would break working calls      |
| profile fails before the call  | unmeasured for that profile (say why)                      |

A 401 on every flow.google.com profile and 200 on none still does NOT license
"flow.google.com accounts cannot create projects": host membership is uniform here
while capability has differed before (#561's 401 -> 200). Any fix must key on the
observed 401, not on the host.

    python scripts/dev/spike_create_project_401.py --profile ffroliva --profile ci-probe -n 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

from gflow_cli.api.transports._common import flow_host_kind  # noqa: E402
from gflow_cli.auth.verification import fetch_flow_session_httpx  # noqa: E402

LABS_ROOT = "https://labs.google/fx/tools/flow"


async def _session_probe(profile_dir: Path) -> dict[str, Any]:
    try:
        status, body, _ = await fetch_flow_session_httpx(profile_dir)
    except Exception as exc:  # noqa: BLE001 - a spike records, never raises
        return {"error": type(exc).__name__}
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = None
    has_token = isinstance(parsed, dict) and bool(parsed.get("access_token"))
    return {"status": status, "has_access_token": has_token}


async def _probe_profile(profile: str, attempts: int) -> dict[str, Any]:
    profile_dir = resolve_profile_dir(profile)
    result: dict[str, Any] = {"profile": profile, "session": await _session_probe(profile_dir)}
    step("session", f"{profile}: {result['session']}")
    try:
        async with build_client(profile_dir) as client:
            page = await client._checkout_page()  # noqa: SLF001 - spike reads the real page
            try:
                await page.goto(LABS_ROOT, wait_until="domcontentloaded", timeout=45_000)
                await page.wait_for_timeout(4_000)
                result["landed_host_kind"] = flow_host_kind(page.url)
                result["landed_path"] = page.url.split("?")[0].split("/", 3)[-1][:60]
            finally:
                client._checkin_page(page)  # noqa: SLF001
            step("host", f"{profile}: {result['landed_host_kind']} /{result['landed_path']}")

            outcomes: list[dict[str, Any]] = []
            for i in range(attempts):
                try:
                    info = await client.create_project(title="gflow-spike-864")
                    outcomes.append({"ok": True, "project_id_len": len(info.project_id)})
                except Exception as exc:  # noqa: BLE001
                    outcomes.append(
                        {
                            "ok": False,
                            "error": type(exc).__name__,
                            "status": getattr(exc, "status", None),
                            "route": getattr(exc, "route", None),
                        }
                    )
                step("create", f"{profile} #{i + 1}: {outcomes[-1]}")
                await asyncio.sleep(2)
            result["create_project"] = outcomes
    except Exception as exc:  # noqa: BLE001
        result["unmeasured"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        step("unmeasured", f"{profile}: {result['unmeasured']}")
    return result


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", action="append", required=True)
    ap.add_argument("-n", type=int, default=3)
    args = ap.parse_args()

    results = [await _probe_profile(p, args.n) for p in args.profile]
    out = default_out_path("spike_create_project_401")
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    step("done", f"wrote {out}")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
