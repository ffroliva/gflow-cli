#!/usr/bin/env python3
"""#836: what does ``wait_until="networkidle"`` actually cost on Flow's bootstrap?

`ui_automation.py:1118` navigates with ``wait_until="networkidle"``, 474 lines above a
comment in the same file forbidding it. The issue's stated mechanism is that Flow holds a
long-poll or WebSocket open, so networkidle never fires and the bootstrap burns its full
45 s ceiling inside a `try/except` that only logs.

**Rung 1 of the spike ladder already weakens that mechanism**, which is why this measures
rather than assumes. `2026-09-14-two-domain-protocol-survey.md` recorded *0 WebSocket
events in 8/8 observations where the app loaded*, and
`2026-09-14-video-poll-is-a-fixed-client-timer.md` § 4 found nothing held open on the
video path. The one long-held response, `ogiZ0b`, spans an *image generation* — not the
bootstrap navigation this probe measures.

So the open question is not "does it hang" but "what does it cost, and does the
prescribed pattern cost less". This A/Bs the two, credit-free.

Arms, each in its own freshly launched persistent context so neither warms the other:

  A  goto(wait_until="domcontentloaded")  then wait for a structural app anchor
  B  goto(wait_until="networkidle")        — the production call, verbatim

Order alternates across rounds so a warm HTTP cache cannot favour one arm.

$0 — navigates only. No prompt is submitted and no generation is started.

Usage:
    uv run python scripts/dev/spike_networkidle_bootstrap_cost.py --profile denon82
    uv run python scripts/dev/spike_networkidle_bootstrap_cost.py --profile denon82 --rounds 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, cast

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _spike_common import default_out_path, resolve_profile_dir, step  # noqa: E402

_ROOT = _HERE.parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

FLOW_URL = "https://labs.google/fx/tools/flow?hl=en"
CEILING_MS = 45_000  # the production timeout at ui_automation.py:1118
_VIEWPORT = {"width": 1280, "height": 800}

# Structural only, per the locale-invariance rule: the Angular root of the graduated
# app (`aisandbox-root`, recorded in the 2026-09-14 two-domain survey) or the labs
# shell's own root. Never a display label.
APP_ANCHOR = "aisandbox-root, app-root, flow-root, [data-test-id], main"


async def _launch(pw: Any, profile_dir: Path) -> Any:
    """Mirror ui_automation.setup_own_context's persistent-context launch."""
    from gflow_cli.browser_manager import channel_for_profile

    return await pw.chromium.launch_persistent_context(
        str(profile_dir),
        headless=False,
        viewport=cast("dict[str, int]", _VIEWPORT),
        locale=os.getenv("GFLOW_CLI_LOCALE", "en-US"),
        channel=channel_for_profile(profile_dir),
        args=[
            "--disable-blink-features=AutomationControlled",
            "--password-store=basic",
            "--disable-dev-shm-usage",
        ],
    )


async def _one_arm(pw: Any, profile_dir: Path, arm: str) -> dict[str, Any]:
    """Launch fresh, navigate once under *arm*'s wait_until, and time it."""
    requests: list[str] = []
    ctx = await _launch(pw, profile_dir)
    try:
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})",
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        page.on("request", lambda r: requests.append(r.url[:120]))

        wait_until = "networkidle" if arm == "B_networkidle" else "domcontentloaded"
        record: dict[str, Any] = {"arm": arm, "wait_until": wait_until}

        t0 = time.monotonic()
        try:
            await page.goto(FLOW_URL, wait_until=wait_until, timeout=CEILING_MS)
            record["goto_ms"] = round((time.monotonic() - t0) * 1000)
            record["goto_timed_out"] = False
        except Exception as exc:  # noqa: BLE001 - the timeout IS the measurement
            record["goto_ms"] = round((time.monotonic() - t0) * 1000)
            record["goto_timed_out"] = True
            record["goto_error"] = type(exc).__name__

        # Arm A pays for the anchor separately; arm B is the production call as-is.
        if arm == "A_domcontentloaded":
            t1 = time.monotonic()
            try:
                await page.wait_for_selector(APP_ANCHOR, timeout=CEILING_MS, state="attached")
                record["anchor_ms"] = round((time.monotonic() - t1) * 1000)
                record["anchor_found"] = True
            except Exception as exc:  # noqa: BLE001
                record["anchor_ms"] = round((time.monotonic() - t1) * 1000)
                record["anchor_found"] = False
                record["anchor_error"] = type(exc).__name__
            record["total_ms"] = record["goto_ms"] + record["anchor_ms"]
        else:
            record["total_ms"] = record["goto_ms"]

        record["final_url"] = page.url
        record["request_count"] = len(requests)
        return record
    finally:
        await ctx.close()


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--rounds", type=int, default=2)
    args = ap.parse_args()

    profile_dir = resolve_profile_dir(args.profile)
    from playwright.async_api import async_playwright

    from gflow_cli.profile_lease import ProfileLease

    results: list[dict[str, Any]] = []
    # Own the profile before Chrome launches, exactly as setup_own_context does.
    # Without it a concurrent gflow run opens a SECOND browser on the same profile
    # instead of getting ProfileLockedError — and this probe's whole point is to
    # measure the production launch, so it has to take the production lock too.
    async with ProfileLease(profile_dir), async_playwright() as pw:
        for rnd in range(1, args.rounds + 1):
            # Alternate so a warm cache cannot systematically favour one arm.
            arms = ["A_domcontentloaded", "B_networkidle"]
            if rnd % 2 == 0:
                arms.reverse()
            for arm in arms:
                step(f"round {rnd}", f"{arm} …")
                rec = await _one_arm(pw, profile_dir, arm)
                rec["round"] = rnd
                results.append(rec)
                step(
                    f"round {rnd}",
                    f"{arm}: total={rec['total_ms']}ms "
                    f"goto={rec['goto_ms']}ms timed_out={rec['goto_timed_out']} "
                    f"requests={rec['request_count']}",
                )

    def _avg(arm: str, key: str) -> float | None:
        vals = [r[key] for r in results if r["arm"] == arm and key in r]
        return round(sum(vals) / len(vals), 1) if vals else None

    summary = {
        "profile": args.profile,
        "flow_url": FLOW_URL,
        "ceiling_ms": CEILING_MS,
        "rounds": args.rounds,
        "A_domcontentloaded_avg_total_ms": _avg("A_domcontentloaded", "total_ms"),
        "B_networkidle_avg_total_ms": _avg("B_networkidle", "total_ms"),
        "B_hit_the_ceiling": [
            r["total_ms"] for r in results if r["arm"] == "B_networkidle" and r["goto_timed_out"]
        ],
        "runs": results,
    }
    out = default_out_path("networkidle_bootstrap_cost")
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    step("done", f"wrote {out}")
    print(json.dumps({k: v for k, v in summary.items() if k != "runs"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
