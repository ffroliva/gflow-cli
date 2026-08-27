"""Does Flow's changelog modal block the app behind it? (#587 follow-up). Zero credits.

Flow began showing a full announcement dialog ("360p option for Gemini Omni Flash",
observed 2026-08-27 on a pt account) over the project editor. A run that hit it
timed out AFTER `url_stable_after_goto` — i.e. the navigation was fine and
something later could not be clicked.

Two things need measuring, not assuming:

1. **Does the modal actually make the background unactionable?** A dialog can be
   purely visual, or it can trap pointer events / mark the rest `inert` or
   `aria-hidden`. Only the second kind explains a timeout on a background
   control. This reports which, per element.
2. **Do gflow's EXISTING detectors already see it?** `TOP_BANNER_SELECTORS` and
   `WELCOME_SCREEN_SELECTORS` anchor on `a[href*='changelog']`, and the modal
   carries a "Ver todos os registros de alterações" link — so it may already be
   matched and simply not dismissible by the close-button list.

    uv run python scripts/dev/spike_changelog_modal.py denon82 --project <id>

Read-only: it never clicks, so the account's dismissal state is unchanged and the
modal is still there for the next run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.client import FlowApiClient  # noqa: E402
from gflow_cli.api.transports.ui_automation import (  # noqa: E402
    MODE_SWITCH_TRIGGER_SELECTORS,
    OVERLAY_CLOSE_BUTTON_SELECTORS,
    TOP_BANNER_SELECTORS,
    WELCOME_SCREEN_SELECTORS,
)

from _spike_common import resolve_profile_dir  # noqa: E402, isort: skip

# Reports, for every dialog on the page: its accessible shape, whether it traps
# pointer events, and whether the app behind it was marked unactionable.
_PROBE = """
() => {
  const out = {dialogs: [], blockers: [], body: {}};
  const bs = getComputedStyle(document.body);
  out.body = {
    overflow: bs.overflow,
    pointerEvents: bs.pointerEvents,
    ariaHidden: document.body.getAttribute('aria-hidden'),
    inert: document.body.hasAttribute('inert'),
  };
  for (const d of document.querySelectorAll("[role='dialog'],[role='alertdialog'],dialog")) {
    const cs = getComputedStyle(d);
    out.dialogs.push({
      role: d.getAttribute('role') || d.tagName.toLowerCase(),
      modal: d.getAttribute('aria-modal'),
      label: (d.getAttribute('aria-label') || '').slice(0, 60),
      changelogLink: !!d.querySelector("a[href*='changelog']"),
      buttons: Array.from(d.querySelectorAll('button')).map(b => ({
        text: (b.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 30),
        icons: Array.from(b.querySelectorAll('i')).map(i => (i.innerText || '').trim()),
        aria: b.getAttribute('aria-label'),
      })).slice(0, 8),
      z: cs.zIndex,
      pointerEvents: cs.pointerEvents,
    });
  }
  // Anything that covers the viewport centre and is NOT the dialog itself.
  const cx = innerWidth / 2, cy = innerHeight / 2;
  const top = document.elementFromPoint(cx, cy);
  out.topAtCentre = top ? {
    tag: top.tagName.toLowerCase(),
    cls: (top.className || '').toString().slice(0, 80),
    inDialog: !!top.closest("[role='dialog'],[role='alertdialog'],dialog"),
  } : null;
  for (const el of document.querySelectorAll('body > *')) {
    const cs = getComputedStyle(el);
    if (cs.pointerEvents === 'none') continue;
    if (el.hasAttribute('inert') || el.getAttribute('aria-hidden') === 'true') {
      out.blockers.push({
        tag: el.tagName.toLowerCase(),
        inert: el.hasAttribute('inert'),
        ariaHidden: el.getAttribute('aria-hidden'),
      });
    }
  }
  return out;
}
"""


async def _run(profile: str, project_id: str | None) -> int:
    pdir = resolve_profile_dir(profile)
    async with FlowApiClient(profile_dir=pdir) as client:
        page = client._page  # noqa: SLF001 — dev instrument
        assert page is not None
        if project_id:
            url = routes.project_editor_url(client._account_locale, project_id)  # noqa: SLF001
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(6000)

        print("\n=== page URL ===")
        print(page.url)

        probe = await page.evaluate(_PROBE)
        print("\n=== dialogs on the page ===")
        print(json.dumps(probe["dialogs"], indent=2)[:3000])
        print("\n=== what is on top at the viewport centre ===")
        print(json.dumps(probe["topAtCentre"], indent=2))
        print("\n=== body / inert-or-aria-hidden siblings ===")
        print(json.dumps({"body": probe["body"], "blockers": probe["blockers"]}, indent=2))

        print("\n=== do gflow's EXISTING detectors see it? ===")
        for name, sels in (
            ("TOP_BANNER", TOP_BANNER_SELECTORS),
            ("WELCOME_SCREEN", WELCOME_SCREEN_SELECTORS),
            ("CLOSE_BUTTONS", OVERLAY_CLOSE_BUTTON_SELECTORS),
        ):
            for sel in sels:
                try:
                    n = await page.locator(sel).count()
                    vis = await page.locator(sel).first.is_visible() if n else False
                except Exception as exc:  # noqa: BLE001
                    n, vis = -1, f"ERR {type(exc).__name__}"
                print(f"  {name:<15} count={n!s:<4} visible={vis!s:<6} {sel}")

        print("\n=== is the app behind it ACTIONABLE? ===")
        # The mode-switch trigger is a real control gflow clicks on every run.
        for sel in MODE_SWITCH_TRIGGER_SELECTORS[:3]:
            loc = page.locator(sel).first
            try:
                count = await page.locator(sel).count()
                visible = await loc.is_visible() if count else False
                enabled = await loc.is_enabled() if count else False
            except Exception as exc:  # noqa: BLE001
                count, visible, enabled = -1, "ERR", type(exc).__name__
            blocked = "n/a"
            if count and visible:
                # Actionability is what Playwright waits on: a covered element is
                # visible and enabled yet never receives the click.
                blocked = await loc.evaluate(
                    "el => { const r = el.getBoundingClientRect();"
                    " const t = document.elementFromPoint(r.x + r.width/2, r.y + r.height/2);"
                    " return t === el || el.contains(t) ? 'reachable' : 'COVERED by <' +"
                    " (t ? t.tagName.toLowerCase() : 'null') + '>'; }"
                )
            print(f"  count={count!s:<4} visible={visible!s:<6} enabled={enabled!s:<6} {blocked}")
            print(f"    {sel}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("profile")
    ap.add_argument("--project", default=None, help="project id to open (else the gallery)")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_run(args.profile, args.project)))
