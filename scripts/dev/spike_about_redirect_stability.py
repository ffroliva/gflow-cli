"""Spike: is `flow.google.com/about` a TRANSIENT landing or a STABLE one? (#756)

**The question, and why it needs measuring.** Routing the `/about` landing to
``FlowAppError`` gives it exit 31's retry semantics, because ``is_retryable`` is
class-level (``errors.py``). Whether a retry helps is therefore an assertion the
code makes on every occurrence — and nobody has measured it. #756 measured the
redirect and explicitly declined to measure its cause; this measures only whether
it *repeats*, which is a different and answerable question.

**Method.** N sequential ``MigratedComposer.ensure_editor`` calls against one
project on one profile, recording where each landed. No generation, no upload,
no prompt submitted: **zero credits.** Same shape as the 2026-09-04 migrated-host
mechanism spike that settled ``FlowHostMigratedError``'s retryability (5/5, 7/7).

**How to read the result.**

| Outcome | Reading |
|---|---|
| N/N `/about` | stable for this account — a retry is doomed; must NOT be retryable |
| mixed | it flaps — a retry can win; retryable is defensible |
| 0/N `/about` | **does not reproduce today.** Settles NOTHING about
  retryability — absence of a reproduction is not evidence of transience |

**Scope, stated up front.** One profile is one account, and per
``flow-capabilities-are-cohort-dependent`` an account is not a cohort. This
answers "does it repeat for THIS account, now" — nothing wider.

    python scripts/dev/spike_about_redirect_stability.py --profile ci-probe --attempts 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)


async def _one_attempt(client: Any, project_id: str, timeout_s: float) -> dict[str, Any]:
    """One ensure_editor call. Records where it landed and what it raised."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = client._page  # noqa: SLF001 — dev instrument
    started = time.monotonic()
    outcome: dict[str, Any] = {}
    try:
        await MigratedComposer().ensure_editor(page, project_id, timeout_s=timeout_s)
        outcome["result"] = "editor_ready"
        outcome["error"] = None
    except BaseException as exc:  # noqa: BLE001 — the failure IS the measurement
        outcome["result"] = "raised"
        outcome["error"] = f"{type(exc).__name__}: {exc}"
    outcome["landed_url"] = str(getattr(page, "url", ""))
    outcome["is_about"] = outcome["landed_url"].rstrip("/").endswith("/about")
    outcome["elapsed_s"] = round(time.monotonic() - started, 2)
    return outcome


async def _run(profile: str, project_id: str, attempts: int, timeout_s: float) -> int:
    profile_dir = resolve_profile_dir(profile)
    step("profile", f"{profile} -> {profile_dir}")

    async with build_client(profile_dir) as client:
        step("project", str(project_id))

        results: list[dict[str, Any]] = []
        for i in range(1, attempts + 1):
            outcome = await _one_attempt(client, str(project_id), timeout_s)
            results.append(outcome)
            step(
                f"attempt {i}/{attempts}",
                f"{outcome['result']} is_about={outcome['is_about']} "
                f"{outcome['elapsed_s']}s url={outcome['landed_url']}",
            )

    about = sum(1 for r in results if r["is_about"])
    ready = sum(1 for r in results if r["result"] == "editor_ready")
    if about == attempts:
        verdict = "STABLE — every attempt landed on /about; a retry is doomed here"
    elif about == 0:
        verdict = (
            "DOES NOT REPRODUCE on this profile today — settles NOTHING about "
            "retryability. Do not read this as 'transient'."
        )
    else:
        verdict = f"FLAPS — {about}/{attempts} landed on /about; a retry can win"

    step("verdict", verdict)
    out = default_out_path("about_redirect_stability")
    out.write_text(
        json.dumps(
            {
                "profile": profile,
                "project_id": project_id,
                "attempts": attempts,
                "timeout_s": timeout_s,
                "about_landings": about,
                "editor_ready": ready,
                "verdict": verdict,
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    step("out", str(out))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True, help="profile name, e.g. ci-probe")
    ap.add_argument("--project-id", required=True, help="project to open on this account")
    ap.add_argument("--attempts", type=int, default=5, help="sequential attempts (default 5)")
    ap.add_argument("--timeout-s", type=float, default=30.0, help="ensure_editor readiness wait")
    args = ap.parse_args()
    return asyncio.run(_run(args.profile, args.project_id, args.attempts, args.timeout_s))


if __name__ == "__main__":
    raise SystemExit(main())
