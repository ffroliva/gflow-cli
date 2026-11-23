r"""How much of a `gflow image t2i` run is startup, and how much is Flow generating?

Cost: image quota only (no credits). Each arm generates one 1-image t2i per sample, so a run
spends 3 * N images of the profile's daily image cap. One project is created per
``cold_create`` sample; those projects are left empty in the account.

Question: would routing CLI commands to an already-warm browser (the MCP server's long-lived
worker) remove enough of a t2i run to be worth building?

Arms, run in blocks (not interleaved: the warm arm holds the profile lease, so a subprocess on
the same profile would be refused with ProfileLockedError):

- cold_create : `gflow image t2i` as a subprocess with no --project. Wall clock of the whole
                command: interpreter start, imports, lease, Chrome, Flow load, project create,
                generate, download, catalog write, teardown. What a user pays today.
- cold_reuse  : the same subprocess with --project <the project cold_create made first>.
                The difference to cold_create is the project-creation cost.
- warm        : one FlowApiClient entered once (timed apart), then N generate_image calls
                into the same project. The per-call time is the floor a warm worker could reach.
                It is an upper bound on the benefit: the worker's queued path adds a queue
                round trip this arm does not pay.

Pre-registered reading, on the medians (written before the first run):

* warm / cold_reuse <= 0.6: startup is a large share; a warm-worker route is worth a plan.
* warm / cold_reuse >= 0.85: generation dominates; a warm worker is not worth it for speed.
* in between: worth it only with another reason (fewer launches, fewer leases).
* cold_create - cold_reuse >= 5 s: project reuse (or a direct create) is worth doing alone.

N is small (image cap), so a result inside the 0.6-0.85 band settles nothing. Arm order is fixed
(warm, then cold), so a Flow-side slowdown during the run shows as a difference between arms:
the per-sample times are printed so a drift is visible. Prints no token, cookie, id or URL.

Usage: uv run python scripts/dev/spike_t2i_warm_vs_cold.py [profile] [N]   (default denon82 3)
"""

from __future__ import annotations

import asyncio
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from gflow_cli.api.client import FlowApiClient  # noqa: E402
from gflow_cli.api.image import GenerateImageRequest  # noqa: E402
from gflow_cli.paths import default_home, profile_subdir  # noqa: E402

PROMPT = "a plain white ceramic mug on a wooden table, soft daylight"


def median(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 1) if xs else None


def cold(profile: str, out: Path, project: str | None) -> tuple[float, str | None, int]:
    argv = ["uv", "run", "--quiet", "gflow", "image", "t2i", PROMPT, "--profile", profile]
    argv += ["--count", "1", "--out", str(out), "--json"]
    if project:
        argv += ["--project", project]
    started = time.monotonic()
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=ROOT, check=False)  # noqa: S603
    elapsed = time.monotonic() - started
    try:  # one indented JSON document (json_output.emit), not JSON lines
        doc = json.loads(proc.stdout)
    except ValueError:
        doc = {}
    pid = doc.get("project_id") if isinstance(doc, dict) else None
    return elapsed, pid, proc.returncode


async def warm(profile: str, project: str, n: int) -> tuple[float, list[float]]:
    started = time.monotonic()
    async with FlowApiClient(profile_dir=profile_subdir(default_home(), profile)) as client:
        entered = time.monotonic() - started
        calls: list[float] = []
        for _ in range(n):
            t0 = time.monotonic()
            await client.generate_image(project_id=project, req=GenerateImageRequest(prompt=PROMPT))
            calls.append(time.monotonic() - t0)
    return entered, calls


def main(profile: str, n: int) -> int:
    out = Path(tempfile.mkdtemp(prefix="spike_t2i_"))
    first, project, rc = cold(profile, out, None)  # also makes the project the other arms reuse
    if rc != 0 or not project:
        why = f"first cold run exited {rc}" if rc else "no project_id in the --json output"
        print(json.dumps({"verdict": "UNMEASURED", "why": why}))
        return 1
    entered, warm_calls = asyncio.run(warm(profile, project, n))
    cold_create = [first] + [cold(profile, out, None)[0] for _ in range(n - 1)]
    cold_reuse = [cold(profile, out, project)[0] for _ in range(n)]
    res = {
        "n": n,
        "warm_entry_s": round(entered, 1),
        "warm_call_s": [round(x, 1) for x in warm_calls],
        "cold_create_s": [round(x, 1) for x in cold_create],
        "cold_reuse_s": [round(x, 1) for x in cold_reuse],
    }
    w, cr, cc = median(warm_calls), median(cold_reuse), median(cold_create)
    if w and cr and cc:
        res["warm_over_cold_reuse"] = round(w / cr, 2)
        res["create_cost_s"] = round(cc - cr, 1)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    sys.exit(main(args[0] if args else "denon82", int(args[1]) if len(args) > 1 else 3))
