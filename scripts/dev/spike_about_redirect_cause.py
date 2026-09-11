r"""WHY does flow.google.com redirect a project to /about? A/B on the wire. ($0)

#756 measured the redirect and explicitly declined to measure its cause, and the
2026-09-10 stability spike could not investigate one because the redirect had stopped
reproducing. As of 2026-09-11 it reproduces **stably** on `denon82` -- 5/5, see
`docs/superpowers/spikes/2026-09-11-about-redirect-is-stable-for-an-account.md` -- and a
stable reproducer is the condition a cause becomes measurable in. That state is
perishable: `ci-probe` went from reproducing to not in two days with nobody watching.

One clue is already in hand and it is a sharp one: `gflow project list` on `denon82`
returns 50 projects INCLUDING the one that redirects. So the backend grants access to it
while the frontend declines to open it. That rules out "the account cannot see the
project" and points at what the app asks for between `goto` and the hop.

    A REDIRECT IS EVIDENCE ABOUT WHAT THE APP ASKED AND WAS TOLD.
    IT IS NOT, BY ITSELF, EVIDENCE ABOUT ACCESS.

## The design: an A/B, not an inspection

Reading only the failing account would show a page that redirects and a pile of requests,
with nothing to say which of them is abnormal. So the SAME capture runs on:

- `denon82`  -- redirects (the arm under test)
- `ci-probe` -- opens the editor (the control)

and the finding is the DIFFERENCE. A request present in both proves nothing; one that
appears, fails, or is answered differently only in the failing arm is the lead.

## What is recorded

- the document response itself: status, and any `Location` (server 302 vs client-side hop)
- every `batchexecute` rpcid, with status, for both arms
- any non-2xx on either arm, with the route stripped of its query
- the navigation timeline, so a client-side hop is visible as a second navigation
- how long after `goto` the hop lands

Deliberately NOT recorded: request bodies, response bodies, headers, cookies. The
question is *which call was answered how*, and bodies on this origin carry prompts and
bearer tokens.

## Pre-registered readings -- written before the run

| Outcome | Reading |
|---|---|
| document 3xx + `Location` | server-side; the app never boots |
| document 200, then a client hop | the app decides; the differing rpcid is the lead |
| rpcid in both, non-2xx only here | that call is the decision point -- never guess it |
| no request differs at all | not on the wire; record unmeasured, say what settles it |

A named call is a LEAD, not a cause. #756's warning stands: do not encode a remedy that
asserts a mechanism this cannot see.

## Cost

Zero. Two navigations per arm, metadata only, no generation, nothing written to Flow.

    python scripts/dev/spike_about_redirect_cause.py \
        --failing denon82 --failing-project 4ccb3222-... \
        --control ci-probe --control-project 1e4efe0d-...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

MIGRATED_PROJECT_URL = "https://flow.google.com/project/{project_id}"


def _route(url: str) -> str:
    """Query-stripped route. Flow's URLs carry rpcids in the query, so keep the ONE
    parameter that names the call and drop everything else (auth, tokens, prompts)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable>"
    rpcids = ""
    for chunk in (parts.query or "").split("&"):
        if chunk.startswith("rpcids="):
            rpcids = f"?{chunk}"
            break
    return f"{parts.scheme}://{parts.netloc}{parts.path}{rpcids}"


async def capture(profile: str, project_id: str, *, label: str, settle_s: float) -> dict[str, Any]:
    responses: list[dict[str, Any]] = []
    navigations: list[dict[str, Any]] = []

    async with build_client(resolve_profile_dir(profile)) as client:
        page = client.transport._page  # noqa: SLF001
        target = MIGRATED_PROJECT_URL.format(project_id=project_id)
        t0 = time.monotonic()

        def on_response(resp: Any) -> None:
            try:
                url = str(resp.url)
                if "google" not in urlsplit(url).netloc:
                    return
                responses.append(
                    {
                        "t_ms": round((time.monotonic() - t0) * 1000),
                        "status": int(resp.status),
                        "route": _route(url),
                    }
                )
            except Exception:  # noqa: BLE001 - a listener must never break the run
                return

        def on_nav(frame: Any) -> None:
            try:
                if frame == page.main_frame:
                    navigations.append(
                        {
                            "t_ms": round((time.monotonic() - t0) * 1000),
                            "url": _route(str(frame.url)),
                        }
                    )
            except Exception:  # noqa: BLE001
                return

        page.on("response", on_response)
        page.on("framenavigated", on_nav)
        try:
            step(label, f"goto {target}")
            nav = await page.goto(target, wait_until="domcontentloaded", timeout=45_000)
            doc = (
                {
                    "status": int(nav.status),
                    "url": _route(str(nav.url)),
                    "location": (await nav.header_value("location")) or None,
                }
                if nav is not None
                else None
            )
            await asyncio.sleep(settle_s)
        finally:
            page.remove_listener("response", on_response)
            page.remove_listener("framenavigated", on_nav)

        landed = _route(str(page.url))
        step(label, f"landed {landed}")
        return {
            "label": label,
            "profile": profile,
            "requested": _route(target),
            "landed": landed,
            "is_about": landed.endswith("/about"),
            "document": doc,
            "navigations": navigations,
            "responses": responses,
        }


def _rpcids(arm: dict[str, Any]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for r in arm["responses"]:
        if "rpcids=" in r["route"]:
            out.setdefault(r["route"].split("rpcids=")[1], []).append(r["status"])
    return out


#: Hosts that are Google furniture rather than Flow's API: fonts, tag manager, analytics,
#: the OneGoogle account bar, Play logging. Listed so EVERY response can be classified —
#: "zero batchexecute calls" is a statement about one route shape and would leave any
#: other Flow request unexamined, which is not the same claim at all.
_NON_FLOW_HOSTS = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "www.googletagmanager.com",
    "region1.google-analytics.com",
    "www.google-analytics.com",
    "ogads-pa.clients6.google.com",
    "play.google.com",
    "lh3.google.com",
    "www.gstatic.com",
)


def _classify(arm: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Every response, bucketed — so nothing is left unexamined by omission."""
    buckets: dict[str, list[dict[str, Any]]] = {
        "flow_batchexecute": [],
        "flow_other": [],
        "non_flow": [],
    }
    for r in arm["responses"]:
        host = urlsplit(r["route"]).netloc
        if "rpcids=" in r["route"]:
            buckets["flow_batchexecute"].append(r)
        elif any(host == h for h in _NON_FLOW_HOSTS):
            buckets["non_flow"].append(r)
        else:
            buckets["flow_other"].append(r)
    return buckets


def report(failing: dict[str, Any], control: dict[str, Any]) -> dict[str, Any]:
    f_rpc, c_rpc = _rpcids(failing), _rpcids(control)
    f_cls, c_cls = _classify(failing), _classify(control)
    return {
        "document": {
            # `page.goto` resolves to the FINAL main resource after following any server
            # redirect, so `status` alone cannot tell 200-then-client-hop from
            # 302-to-/about: both end 200 with no Location. The discriminators are the
            # resolved URL (a server redirect resolves to /about) and the 3xx list below.
            "failing_status": (failing.get("document") or {}).get("status"),
            "failing_resolved_url": (failing.get("document") or {}).get("url"),
            "failing_location": (failing.get("document") or {}).get("location"),
            "failing_requested": failing["requested"],
            "control_status": (control.get("document") or {}).get("status"),
        },
        "redirect_responses_3xx": {
            "failing": [r for r in failing["responses"] if 300 <= r["status"] < 400],
            "control": [r for r in control["responses"] if 300 <= r["status"] < 400],
        },
        "landed": {"failing": failing["landed"], "control": control["landed"]},
        "navigation_count": {
            "failing": len(failing["navigations"]),
            "control": len(control["navigations"]),
        },
        "response_classes": {
            arm: {k: len(v) for k, v in cls.items()}
            for arm, cls in (("failing", f_cls), ("control", c_cls))
        },
        # Named in full, because "0 batchexecute" is not "no Flow request".
        "failing_flow_requests_other_than_batchexecute": f_cls["flow_other"],
        "failing_all_routes": [r["route"] for r in failing["responses"]],
        "rpcids_only_in_control": sorted(set(c_rpc) - set(f_rpc)),
        "rpcids_only_in_failing": sorted(set(f_rpc) - set(c_rpc)),
        # Multiset, not set: [200] and [200, 200] are different answers, and collapsing
        # them would hide a call the failing arm made half as often.
        "rpcids_in_both_differing_status": {
            k: {"failing": sorted(f_rpc[k]), "control": sorted(c_rpc[k])}
            for k in sorted(set(f_rpc) & set(c_rpc))
            if sorted(f_rpc[k]) != sorted(c_rpc[k])
        },
        "non_2xx_failing": [r for r in failing["responses"] if r["status"] >= 400],
        "non_2xx_control": [r for r in control["responses"] if r["status"] >= 400],
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--failing", required=True)
    ap.add_argument("--failing-project", required=True)
    ap.add_argument("--control", required=True)
    ap.add_argument("--control-project", required=True)
    ap.add_argument("--settle-s", type=float, default=12.0)
    args = ap.parse_args()

    failing = await capture(
        args.failing, args.failing_project, label="failing", settle_s=args.settle_s
    )
    control = await capture(
        args.control, args.control_project, label="control", settle_s=args.settle_s
    )
    result = {
        "question": "why does flow.google.com redirect a project to /about? (#756)",
        "cost": "credit-free: two navigations, response metadata only, no bodies",
        "failing": failing,
        "control": control,
        "diff": report(failing, control),
    }
    out = default_out_path("spike_about_redirect_cause", ".json")
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    step("done", f"wrote {out}")
    print(json.dumps(result["diff"], indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
