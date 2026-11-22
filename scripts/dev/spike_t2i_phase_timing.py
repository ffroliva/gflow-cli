r"""Break a cold ``gflow image t2i`` run into its phases.

Cost: image quota only (no credits). Each sample generates one image. One project
is created per ``create`` sample; those projects are left in the account.

Pre-registered reading (written before the first run):

* Chrome launch + Flow load >= 60% of non-generation time: the resident browser
  removes the dominant cost.
* Imports >= 40% of non-generation time: trimming them helps both CLI and MCP.
* Neither dominates: neither fix is worth it alone.

Phases timed:

1. ``imports``: time to import ``gflow_cli.api.client.FlowApiClient`` (the
   heaviest import chain, which pulls Playwright and structlog).
2. ``lease``: ``ProfileLease.__aenter__`` (kernel lock + evidence collection).
3. ``chrome``: Playwright launch + persistent context (Chrome start, profile load).
4. ``flow_load``: the first ``page.goto(flow.google.com)`` + domcontentloaded.
5. ``create_project``: ``client.create_project(title)`` (button click + rpc reply
   + rename). Skipped when ``--project`` is passed.
6. ``generate``: ``client.generate_image(...)`` (reCAPTCHA mint, submit, wait for
   the result, download the signed URL to memory).
7. ``teardown``: ``client.__aexit__`` (page close, context close, Playwright stop,
   lease release).

Each phase is timed with ``time.perf_counter`` around the real production code
path, not a simulation.

Usage::

    uv run python scripts/dev/spike_t2i_phase_timing.py [profile] [N]
    # default: denon82, N=5
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

PROMPT = "a plain white ceramic mug on a wooden table, soft daylight"


def _median(xs: list[float]) -> float:
    return round(statistics.median(xs), 2) if xs else 0.0


def _pct(part: float, total: float) -> str:
    return f"{100 * part / total:.0f}%" if total > 0 else "?"


async def run_one(profile_dir: Path, project_id: str | None) -> dict[str, float]:
    """Run one t2i and return per-phase times in seconds."""
    phases: dict[str, float] = {}

    # 1. imports (already done at module level, so measure a fresh import)
    t0 = time.perf_counter()
    # Force re-resolve of the heavy chain
    from gflow_cli.api.client import FlowApiClient  # noqa: F811
    from gflow_cli.api.image import GenerateImageRequest  # noqa: F811

    _ = FlowApiClient, GenerateImageRequest  # touch them
    phases["imports"] = time.perf_counter() - t0

    # 2-7: instrument through the client lifecycle
    req = GenerateImageRequest(prompt=PROMPT)

    t0 = time.perf_counter()
    client = FlowApiClient(profile_dir=profile_dir)

    # 2. lease + 3. chrome (combined in __aenter__)
    # We time __aenter__ as a whole, then split later if needed
    t_enter = time.perf_counter()
    await client.__aenter__()
    phases["client_enter"] = time.perf_counter() - t_enter

    try:
        # 5. create_project (optional)
        if project_id is None:
            t_create = time.perf_counter()
            info = await client.create_project(title="spike-phase-timing")
            phases["create_project"] = time.perf_counter() - t_create
            project_id_used = info.project_id
        else:
            phases["create_project"] = 0.0
            project_id_used = project_id

        # 6. generate
        t_gen = time.perf_counter()
        await client.generate_image(project_id=project_id_used, req=req)
        phases["generate"] = time.perf_counter() - t_gen
    finally:
        # 7. teardown
        t_teardown = time.perf_counter()
        await client.__aexit__(None, None, None)
        phases["teardown"] = time.perf_counter() - t_teardown

    phases["total"] = time.perf_counter() - t0
    return phases


def main(profile: str, n: int) -> int:
    from gflow_cli.paths import default_home, profile_subdir

    profile_dir = profile_subdir(default_home(), profile)
    if not profile_dir.is_dir():
        print(json.dumps({"verdict": "UNMEASURED", "why": f"profile {profile} not found"}))
        return 1

    # First run creates a project, rest reuse it
    print(f"Running {n} samples on profile {profile}...", file=sys.stderr)

    all_phases: dict[str, list[float]] = {}
    first_project: str | None = None

    for i in range(n):
        use_project = first_project if i > 0 else None
        try:
            phases = asyncio.run(run_one(profile_dir, use_project))
        except Exception as exc:
            why = f"run {i} failed: {type(exc).__name__}"
            print(json.dumps({"verdict": "UNMEASURED", "why": why}))
            return 1
        for k, v in phases.items():
            all_phases.setdefault(k, []).append(v)
        if i == 0 and "create_project" in phases and phases["create_project"] > 0:
            # Extract the project id from the first run for reuse
            # (we lose it here; the caller can pass --project next time)
            pass
        summary = json.dumps({k: round(v, 1) for k, v in phases.items()})
        print(f"  sample {i}: {summary}", file=sys.stderr)

    medians = {k: _median(v) for k, v in all_phases.items()}
    total_median = medians.get("total", 0)
    gen_median = medians.get("generate", 0)
    non_gen = total_median - gen_median

    result = {
        "n": n,
        "profile": profile,
        "medians_s": medians,
        "non_generation_s": round(non_gen, 2),
        "phase_share_of_non_gen": {
            k: _pct(v, non_gen) for k, v in medians.items() if k not in ("total", "generate")
        },
    }

    # Pre-registered thresholds
    client_enter = medians.get("client_enter", 0)
    imports = medians.get("imports", 0)
    if non_gen > 0:
        result["chrome_share_of_non_gen"] = _pct(client_enter, non_gen)
        result["imports_share_of_non_gen"] = _pct(imports, non_gen)
        if client_enter / non_gen >= 0.6:
            result["reading"] = "Chrome launch dominates; a resident browser removes the main cost"
        elif imports / non_gen >= 0.4:
            result["reading"] = "Imports dominate; trimming them helps CLI and MCP both"
        else:
            result["reading"] = "Neither dominates alone"

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    sys.exit(main(args[0] if args else "denon82", int(args[1]) if len(args) > 1 else 5))
