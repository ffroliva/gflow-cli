r"""Which lane is a profile on — labs, migrated, or signed out? ($0)

`skills/video-production/SKILL.md` Step 2 says "check the host, or the whole plan is
dead", and every migrated-host claim in the tree turns on the answer. Until now that
check was a manual browser visit, so it got skipped — and skipping it cost this session
an hour on 2026-09-07.

The trap it exists to catch: **a signed-out profile looks exactly like a migrated one.**
With no session, `labs.google/fx/...` renders its marketing shell and
`flow.google.com/project/<id>` redirects to `/about`. Read either in isolation and you
will conclude "this account was moved", which is a claim about Google's cohort
assignment inferred from a dead cookie. So this probe reports SIGNED_OUT as a distinct
verdict and refuses to name a lane when it sees one.

The discriminator is the app shell, never the URL alone:

  labs     React/Next   -> ``i.google-symbols`` carries the ligatures
  migrated Angular      -> ``mat-icon`` inside ``aisandbox-root`` carries them
  signed out            -> the landing page: a ``flow-landing-page`` /
                           ``next-route-announcer`` shell, a "Create with Google Flow"
                           call to action, and no project grid

Credit-free: two navigations and two DOM reads. Nothing is created, typed or submitted.

    python scripts/dev/spike_host_lane.py --profile promo-denon82
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

LABS_ROOT = "https://labs.google/fx/tools/flow"
MIGRATED_ROOT = "https://flow.google.com/"

_PROBE_JS = """
() => {
  const count = (sel) => document.querySelectorAll(sel).length;
  const txt = (document.body.innerText || '');
  return {
    url: location.href,
    title: document.title,
    // Which framework's icon carrier is on the page. A mismatch here looks
    // exactly like a missing feature, so it is the primary signal.
    carriers: {
      google_symbols: count('i.google-symbols'),
      mat_icon: count('mat-icon'),
    },
    // App-shell roots. Their presence says the APP mounted, not the landing page.
    shells: {
      aisandbox_root: count('aisandbox-root'),
      next_route_announcer: count('next-route-announcer'),
      flow_landing_page: count('flow-landing-page'),
      router_outlet: count('router-outlet'),
    },
    // A signed-in Flow always renders a project grid or an editor; the signed-out
    // landing page renders a marketing call to action and a cookie banner instead.
    signed_out_tells: {
      create_with_google_flow: /Create with Google Flow/i.test(txt),
      pricing_nav: /\\bPricing\\b/.test(txt),
      cookie_banner: /uses cookies to deliver/i.test(txt),
    },
    project_links: count('a[href*="/project/"]'),
    media_tiles: count('video') + count('img[src*="lh3.googleusercontent.com"]'),
    custom_element_sample: [...new Set(
      [...document.querySelectorAll('*')]
        .map(e => e.tagName.toLowerCase())
        .filter(t => t.includes('-'))
    )].slice(0, 20),
  };
}
"""


def verdict(labs: dict[str, Any], migrated: dict[str, Any]) -> str:
    """Name the lane, or refuse to. SIGNED_OUT outranks every other reading.

    Decided on ONE structural signal: **does a root render the account's project grid**
    (`a[href*="/project/"]`). A signed-in Flow always does; the marketing landing page
    never can, because it has no account to list.

    Deliberately not decided on text. An earlier draft keyed SIGNED_OUT on the English
    string "Create with Google Flow", which would read MIGRATED for a signed-out
    non-English profile — precisely the false positive this script exists to prevent, and
    a breach of the locale-invariance rule in AGENTS.md. The `signed_out_tells` are still
    collected, because they are useful to a human reading the JSON, but they no longer
    decide anything.

    `aisandbox_root` is likewise not a signed-in signal: the migrated marketing page
    mounts the same Angular shell.
    """
    grid = lambda r: int(r.get("project_links") or 0) > 0  # noqa: E731
    if not grid(labs) and not grid(migrated):
        return "SIGNED_OUT"
    if grid(migrated):
        return "MIGRATED"
    if grid(labs):
        return "LABS"
    return "INDETERMINATE"


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--settle", type=float, default=8.0)
    args = ap.parse_args()

    profile_dir = resolve_profile_dir(args.profile)
    results: dict[str, Any] = {"profile": args.profile, "roots": {}}

    async with build_client(profile_dir) as client:
        context = client._context  # noqa: SLF001 - spike reads the live context
        page = await context.new_page()
        for lane, url in (("labs", LABS_ROOT), ("migrated", MIGRATED_ROOT)):
            step(lane, f"probing {url}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(int(args.settle * 1000))
                results["roots"][lane] = await page.evaluate(_PROBE_JS)
            except Exception as exc:  # noqa: BLE001 - a nav failure is itself the finding
                results["roots"][lane] = {"nav_error": str(exc)[:300]}
            r = results["roots"][lane]
            step(
                lane,
                f"final={str(r.get('url', '?'))[:70]}  "
                f"symbols={r.get('carriers', {}).get('google_symbols')}  "
                f"mat_icon={r.get('carriers', {}).get('mat_icon')}  "
                f"projects={r.get('project_links')}",
            )

    results["verdict"] = verdict(
        results["roots"].get("labs", {}), results["roots"].get("migrated", {})
    )
    out = default_out_path("spike_host_lane")
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    step("VERDICT", results["verdict"])
    if results["verdict"] == "SIGNED_OUT":
        step("note", "signed out — this says NOTHING about which lane the account is on")
    step("done", f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
