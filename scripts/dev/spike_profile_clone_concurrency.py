"""Spike: can N clones of one signed-in Chrome profile drive Flow at the same time?

Question: e2e sharding needs one profile per worker (the profile lock is keyed by directory).
Does a copy of a profile directory (caches left out) stay signed in, and do concurrent clones
sign each other out when Google rotates session cookies?

Method: clone <profile> N times, run `gflow credits user` through the browser (direct wire
reads off) on the original and every clone at once, then run it again on each, one by one, to
see who is still signed in. Prints only exit codes, durations and a failure's problem title:
no balances, tokens or cookies. Spends no credits. Findings:
docs/superpowers/spikes/2026-11-12-profile-clone-concurrency.md

Usage: uv run python scripts/dev/spike_profile_clone_concurrency.py <profile> <clones> [--keep]
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(
    os.environ.get("GFLOW_CLI_HOME")
    or Path(os.environ.get("LOCALAPPDATA", "")) / "ffroliva" / "gflow-cli"
)
# Caches and lock files are not session state: leaving them out makes a clone ~6x smaller.
SKIP = {
    "Cache",
    "Code Cache",
    "GPUCache",
    "GrShaderCache",
    "ShaderCache",
    "DawnCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "CacheStorage",
    "ScriptCache",
    "Crashpad",
    "BrowserMetrics",
    "DeferredBrowserMetrics",
    "component_crx_cache",
    "optimization_guide_model_store",
    "SingletonLock",
    "SingletonCookie",
    "SingletonSocket",
    "lockfile",
    "RunningChromeVersion",
}


def clone(src: str, dst: str) -> float:
    source, target = HOME / f"profile_{src}", HOME / f"profile_{dst}"
    if target.exists():
        shutil.rmtree(target)
    start = time.monotonic()
    shutil.copytree(source, target, ignore=lambda _dir, names: [n for n in names if n in SKIP])
    return time.monotonic() - start


def size_mb(name: str) -> int:
    root = HOME / f"profile_{name}"
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) // 2**20


def credits(profile: str) -> dict[str, object]:
    env = {**os.environ, "GFLOW_CLI_WIRE_READS": "0", "PYTHONUTF8": "1"}
    argv = ["uv", "run", "gflow", "credits", "user", "--profile", profile, "--json"]
    start = time.monotonic()
    # Fixed argv; the profile names come from this script's own clone step.
    run = subprocess.run(  # noqa: S603
        argv, capture_output=True, text=True, env=env, timeout=600, check=False
    )
    out: dict[str, object] = {
        "profile": profile,
        "exit": run.returncode,
        "s": round(time.monotonic() - start),
        "ok": run.returncode == 0,
    }
    if run.returncode != 0:
        # The problem-details JSON goes to stderr; keep only its title, never values.
        text = run.stderr
        begin = text.find("{")
        try:
            data = json.loads(text[begin : text.rfind("}") + 1]) if begin >= 0 else {}
        except ValueError:
            data = {}
        problem = data.get("problem", {}) if isinstance(data, dict) else {}
        out["title"] = problem.get("title") if isinstance(problem, dict) else None
    return out


def main() -> None:
    src, count = sys.argv[1], int(sys.argv[2])
    names = [f"{src}-spike{i}" for i in range(1, count + 1)]
    for name in names:
        print(
            f"clone {name}: {clone(src, name):.1f}s, {size_mb(name)} MB (source {size_mb(src)} MB)"
        )
    everyone = [src, *names]
    start = time.monotonic()
    with cf.ThreadPoolExecutor(len(everyone)) as pool:
        concurrent = list(pool.map(credits, everyone))
    print(f"CONCURRENT ({time.monotonic() - start:.0f}s wall):")
    for row in concurrent:
        print("  ", row)
    print("SEQUENTIAL recheck (still signed in?):")
    for profile in everyone:
        print("  ", credits(profile))
    if "--keep" not in sys.argv:
        for name in names:
            shutil.rmtree(HOME / f"profile_{name}", ignore_errors=True)
        print("clones removed")


if __name__ == "__main__":
    main()
