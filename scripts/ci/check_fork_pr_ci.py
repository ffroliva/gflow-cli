#!/usr/bin/env python
# SPDX-License-Identifier: MIT
"""Fail when a fork PR's required CI never actually ran.

GitHub gates `pull_request`-triggered workflows on fork PRs behind a maintainer's
"Approve and run". Until that click the run sits at ``conclusion="action_required"`` —
and it is **absent from the PR's `statusCheckRollup`**, which is what branch protection
evaluates and what a reviewer reads. The PR therefore renders green while carrying no
test evidence whatsoever.

Measured here on 2026-09-15 across three open fork PRs with identical labels:

    #781  CI conclusion=success            → 16 checks shown, all green
    #793  CI conclusion=action_required    →  2 checks shown, both green
    #787  no `pull_request` run at all     →  2 checks shown, both green

#793 had already shipped broken tests and a ruff-format failure through exactly this
gap, with nothing red anywhere. The decision below is deliberately narrow: it reports
only the states the rollup hides. An outright ``failure`` is already visible, and an
``in_progress`` run shows as pending, so neither is this gate's business — flagging
them would train people to ignore it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Iterable, Mapping

#: Workflows whose absence makes a PR unreviewable. `CI` carries the test matrix,
#: ruff and pyright; a fork PR without it has been checked by nothing.
REQUIRED_WORKFLOWS = frozenset({"CI"})

#: The one conclusion that means "this run exists, proves nothing, and is invisible".
_AWAITING_APPROVAL = "action_required"


def untrustworthy_workflows(
    runs: Iterable[Mapping[str, object]],
    required: Iterable[str] = REQUIRED_WORKFLOWS,
) -> dict[str, str]:
    """Map workflow name -> why its result cannot be trusted. Empty means fine.

    Only ``pull_request`` runs count. A ``pull_request_target`` run (the triage
    workflow) executes for forks regardless of approval, so treating it as coverage
    would restate the very bug this guards against.
    """
    verdict: dict[str, str] = {}
    runs = list(runs)
    for name in sorted(required):
        candidates = [r for r in runs if r.get("name") == name and r.get("event") == "pull_request"]
        if not candidates:
            verdict[name] = "no run for this head SHA — CI never started"
        elif all(r.get("conclusion") == _AWAITING_APPROVAL for r in candidates):
            verdict[name] = (
                "awaiting maintainer approval (fork PR gate) — the run is hidden from "
                "the checks list, so this PR only looks green"
            )
    return verdict


def _fetch_runs(repo: str, head_sha: str) -> list[dict[str, object]]:
    out = subprocess.run(
        ["gh", "api", f"repos/{repo}/actions/runs?head_sha={head_sha}&per_page=100"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return list(json.loads(out).get("workflow_runs", []))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="ffroliva/gflow-cli")
    ap.add_argument("--pr", required=True, help="pull request number")
    args = ap.parse_args()

    head = subprocess.run(
        [
            "gh",
            "pr",
            "view",
            args.pr,
            "--repo",
            args.repo,
            "--json",
            "headRefOid",
            "--jq",
            ".headRefOid",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    verdict = untrustworthy_workflows(_fetch_runs(args.repo, head))
    if not verdict:
        print(f"PR #{args.pr}: every required workflow actually ran on {head[:8]}.")
        return 0
    print(f"PR #{args.pr}: required CI did not run on {head[:8]} —")
    for name, why in verdict.items():
        print(f"  {name}: {why}")
    print("\nApprove the workflow run, or gate the head locally, before trusting this PR.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
