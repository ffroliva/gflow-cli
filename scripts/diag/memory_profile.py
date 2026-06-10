"""Diagnostic — Chrome process-tree RSS at key milestones (issue #155).

Measures how much memory the Chrome/Chromium process tree consumes at four
points during a real browser launch against a gflow-cli profile:

  baseline        — before Playwright starts (current Python process only)
  post_launch     — immediately after launch_persistent_context returns
  post_navigation — after page.goto(FLOW_URL, wait_until="networkidle")
  post_close      — after context.close() and Playwright stop

The output table shows Own RSS (the profiler's Python process) and Tree RSS
(profiler + all Chrome child processes) so you can see exactly how much RAM
Chrome adds at each stage.

Run: `uv run python scripts/diag/memory_profile.py --profile NAME [--output-json PATH]`

Prerequisite: gflow auth login (plus psutil — install with: uv add --dev psutil)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

try:
    import psutil
except ImportError:
    print("psutil is required: uv add --dev psutil", file=sys.stderr)
    sys.exit(2)

try:
    from gflow_cli.api.transports.ui_automation import FLOW_URL
except ImportError:
    FLOW_URL = "https://labs.google/fx/tools/flow?hl=en"

from playwright.async_api import async_playwright

from gflow_cli.auth import profile_dir
from gflow_cli.browser_manager import channel_for_profile
from gflow_cli.config import get_settings

CHROME_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
]

_COL_MILESTONE = 16
_COL_OWN = 14
_COL_TREE = 15
_COL_DELTA = 12


def _tree_rss_mb(pid: int) -> float:
    """Return total RSS (MB) of a process and all its descendants."""
    try:
        proc = psutil.Process(pid)
        procs = [proc, *proc.children(recursive=True)]
        return sum(p.memory_info().rss for p in procs if p.is_running()) / (1024 * 1024)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0


def _own_rss_mb() -> float:
    """Return RSS (MB) of only the current Python process."""
    try:
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0


def _print_table_header() -> None:
    header = (
        f"{'Milestone':<{_COL_MILESTONE}} | "
        f"{'Own RSS (MB)':>{_COL_OWN}} | "
        f"{'Tree RSS (MB)':>{_COL_TREE}} | "
        f"{'Delta (MB)':>{_COL_DELTA}}"
    )
    sep = "-" * len(header)
    print(sep)
    print(header)
    print(sep)


def _print_table_row(name: str, own_mb: float, tree_mb: float, delta: float | None) -> None:
    delta_str = "—" if delta is None else f"{delta:+.1f}"
    print(
        f"{name:<{_COL_MILESTONE}} | "
        f"{own_mb:>{_COL_OWN}.1f} | "
        f"{tree_mb:>{_COL_TREE}.1f} | "
        f"{delta_str:>{_COL_DELTA}}"
    )


async def run(profile_name: str, output_json: Path | None) -> None:
    """Main diagnostic loop."""
    # Resolve profile directory using GFLOW_CLI_HOME
    settings = get_settings()
    _ = settings.home  # ensure settings loaded; profile_dir() uses get_settings() internally
    pdir = profile_dir(profile_name)
    if not pdir.exists():
        print(f"ERROR: Profile directory not found: {pdir}", file=sys.stderr)
        sys.exit(1)

    milestones: list[dict[str, object]] = []
    pid = os.getpid()

    # -------------------------------------------------------------------------
    # Milestone 1: baseline (before Playwright starts)
    # -------------------------------------------------------------------------
    own_baseline = _own_rss_mb()
    tree_baseline = _tree_rss_mb(pid)
    milestones.append(
        {
            "name": "baseline",
            "own_rss_mb": round(own_baseline, 2),
            "tree_rss_mb": round(tree_baseline, 2),
            "delta_mb": None,
        }
    )

    _print_table_header()
    _print_table_row("baseline", own_baseline, tree_baseline, None)

    channel = channel_for_profile(pdir)

    async with async_playwright() as pw:
        launcher = pw.chromium

        # -------------------------------------------------------------------------
        # Milestone 2: post_launch
        # -------------------------------------------------------------------------
        ctx = await launcher.launch_persistent_context(
            str(pdir),
            headless=False,
            args=CHROME_ARGS,
            locale="en-US",
            **({"channel": channel} if channel else {}),
        )

        own_post_launch = _own_rss_mb()
        tree_post_launch = _tree_rss_mb(pid)
        delta_launch = tree_post_launch - tree_baseline
        milestones.append(
            {
                "name": "post_launch",
                "own_rss_mb": round(own_post_launch, 2),
                "tree_rss_mb": round(tree_post_launch, 2),
                "delta_mb": round(delta_launch, 2),
            }
        )
        _print_table_row("post_launch", own_post_launch, tree_post_launch, delta_launch)

        # -------------------------------------------------------------------------
        # Milestone 3: post_navigation
        # -------------------------------------------------------------------------
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        print(f"Navigating to {FLOW_URL} ...")
        try:
            await page.goto(FLOW_URL, wait_until="networkidle", timeout=45_000)
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: navigation did not fully settle: {exc}", file=sys.stderr)

        own_post_nav = _own_rss_mb()
        tree_post_nav = _tree_rss_mb(pid)
        delta_nav = tree_post_nav - tree_baseline
        milestones.append(
            {
                "name": "post_navigation",
                "own_rss_mb": round(own_post_nav, 2),
                "tree_rss_mb": round(tree_post_nav, 2),
                "delta_mb": round(delta_nav, 2),
            }
        )
        _print_table_row("post_navigation", own_post_nav, tree_post_nav, delta_nav)

        # -------------------------------------------------------------------------
        # Milestone 4: post_close
        # -------------------------------------------------------------------------
        await ctx.close()

    own_post_close = _own_rss_mb()
    tree_post_close = _tree_rss_mb(pid)
    delta_close = tree_post_close - tree_baseline
    milestones.append(
        {
            "name": "post_close",
            "own_rss_mb": round(own_post_close, 2),
            "tree_rss_mb": round(tree_post_close, 2),
            "delta_mb": round(delta_close, 2),
        }
    )
    _print_table_row("post_close", own_post_close, tree_post_close, delta_close)
    print("-" * (_COL_MILESTONE + _COL_OWN + _COL_TREE + _COL_DELTA + 9))

    # -------------------------------------------------------------------------
    # JSON output (optional)
    # -------------------------------------------------------------------------
    if output_json is not None:
        result = {
            "platform": sys.platform,
            "chrome_args": CHROME_ARGS,
            "milestones": milestones,
        }
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"JSON written to: {output_json}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--profile",
        required=True,
        help="Name of the gflow-cli profile (directory under GFLOW_CLI_HOME)",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        metavar="PATH",
        help="Optional path to write machine-readable results as JSON",
    )
    args = parser.parse_args()
    asyncio.run(run(args.profile, args.output_json))


if __name__ == "__main__":
    main()
