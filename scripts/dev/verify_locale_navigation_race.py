"""E2E GATE for #580 — is editor navigation actually race-free on a pt-BR account?

The defect is a live-timing property. Unit tests pin the contract at the seam;
only a real run against real Flow can prove the race is gone.

**The trap this avoids:** creating a NEW project takes the "+ New project" click
branch and never calls ``page.goto`` at all — exercising it proves nothing. This
harness navigates to an EXISTING project, which is the racing path.

Runs both arms:

  treatment — the fix as shipped: locale resolved, URL built from it
  control   — locale forced to None, reproducing the pre-fix bare/guessed URL

A pass that would also pass in the control arm is not evidence, so the control
must show the redirect the treatment does not.

Costs no credits: navigation only, aborted before any generation.

    uv run python scripts/dev/verify_locale_navigation_race.py --profile denon82 --project <pid>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spike_common import build_client, resolve_profile_dir  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.transports._common import await_url_settled  # noqa: E402


async def arm(client: Any, page: Any, project_id: str, locale: str | None, label: str) -> dict:
    """Navigate exactly as _enter_editor does, and measure the race window."""
    url = routes.project_editor_url(locale, project_id)
    await page.goto("about:blank")
    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    at_return = page.url
    settled = await await_url_settled(page)
    raced = bool(settled and settled != at_return)
    return {
        "arm": label,
        "locale_used": locale,
        "requested": url,
        "url_at_goto_return": at_return,
        "url_settled": settled,
        "REDIRECT_AFTER_GOTO": raced,
        "project_preserved": project_id in (settled or ""),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True, help="an EXISTING project id (the racing path)")
    args = ap.parse_args()

    async with build_client(resolve_profile_dir(args.profile)) as client:
        resolved = client._account_locale
        print(f"\naccount locale resolved by the client: {resolved!r}")
        if resolved is None:
            print("  WARNING: unresolved — treatment arm degrades to the bare URL")

        page = await client._checkout_page()
        try:
            treatment = await arm(client, page, args.project, resolved, "treatment (fix)")
            control = await arm(client, page, args.project, None, "control (no locale)")
        finally:
            client._checkin_page(page)

    for r in (treatment, control):
        print(f"\n--- {r['arm']} ---")
        print(f"  locale used        : {r['locale_used']!r}")
        print(f"  url at goto return : {r['url_at_goto_return']}")
        print(f"  url settled        : {r['url_settled']}")
        print(f"  REDIRECT AFTER GOTO: {r['REDIRECT_AFTER_GOTO']}")

    out = Path("scripts/dev/_spike_out/verify_locale_navigation_race.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([treatment, control], indent=2), encoding="utf-8")

    gate_pass = (
        resolved is not None
        and not treatment["REDIRECT_AFTER_GOTO"]
        and treatment["project_preserved"]
    )
    control_proves_load_bearing = control["REDIRECT_AFTER_GOTO"]

    print("\n=== GATE ===")
    print(f"  treatment race-free      : {not treatment['REDIRECT_AFTER_GOTO']}")
    print(f"  control still races      : {control_proves_load_bearing}")
    if gate_pass and control_proves_load_bearing:
        print("  PASS — fix is effective AND demonstrably load-bearing")
    elif gate_pass:
        print("  WEAK PASS — treatment is clean but the control did not race;")
        print("              this run cannot prove the fix caused the improvement")
    else:
        print("  FAIL — the race survives the fix")
    print(f"\nevidence: {out}")
    return 0 if gate_pass else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
