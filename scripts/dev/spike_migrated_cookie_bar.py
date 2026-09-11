r"""Does Google's glue cookie bar render on flow.google.com, and does it block? ($0)

Settles the premise of #780 / PR #781.

#780 asserts, as its first of three "measured" failure modes:

    Google's glue cookie banner (`glue-cookie-notification-bar`) sits fixed at the
    viewport bottom and intercepts every pointer event over the composer's settings
    trigger. Click retries run into Playwright's timeout and the run dies as an
    unhandled TimeoutError.

PR #781 builds three gates on that premise. Nothing in this repo has ever observed the
bar on `flow.google.com`. What it HAS observed:

- `test_assets/debug_editor/buttons.json` -- the bar, with `__accept` ("Agree") first and
  `__reject` ("No thanks") second. Captured on a **labs.google** editor: the sibling
  buttons in that same file carry styled-components classes (`sc-e8425ea6-0 gLXNUV`),
  which is labs' React build, not the migrated host's Angular one.
- `scripts/smoke_video_editor.py:385` -- dismisses `#glue-cookie-notification-bar-1
  button`, again on the labs editor.
- `docs/superpowers/spikes/2026-09-10-migrated-click-blocked.md` -- 159 DOM samples on
  `flow.google.com`, **zero** `.cdk-overlay-pane` and **zero** `[role='dialog']` before
  the click, click landed 3/3 in 65-130 ms. That probe never looked for a cookie bar,
  so it neither found nor excluded one.

    A SELECTOR NOBODY LOOKED FOR IS NOT AN ABSENCE.
    A BAR THAT EXISTS IS NOT YET A BLOCKER.

So this probe asks two separate questions, because #780 conflates them:

1. **Does the bar render on the migrated host at all?** Inventory the selectors, and --
   because a warm profile has usually already answered consent -- also record whether the
   *machinery* is even shipped: any script/link/global that Google's glue consent bundle
   would bring with it. A page that never loads the bundle cannot grow the bar later.
2. **If it renders, does it block?** `elementFromPoint` over the settings trigger and
   over the image submit button (`arrow_forward`). Fixed furniture at the bottom of a
   viewport is only a blocker if it is on top of the control being clicked.

Contrast arm: the same inventory on `labs.google`, so "the bar is real" and "the bar is
on THIS host" cannot be confused for each other again.

## Pre-registered readings -- written before the run, so the result cannot be respun

| Outcome | Reading |
|---|---|
| bar visible on migrated, and it wins `elementFromPoint` over a control | #780's premise holds; PR #781's cookie gate has a real target |
| bar visible on migrated, controls still hit-test to themselves | the bar EXISTS and does NOT block -- #780's stated mechanism is falsified and the gate guards a decoration |
| bar absent on migrated, present on labs | the bar is a labs surface; PR #781 gates the Angular composer against React-app furniture |
| bar absent on both, glue bundle absent too | strongest available negative: the machinery is not shipped to this page |
| bar absent on both, glue bundle PRESENT | **does not reproduce here; settles nothing** -- consent is already stored on this profile. Report as unmeasured. |

What would settle that last row: a profile that has never answered consent, or an EU
egress. Neither is summonable on demand, which is why the bundle reading is captured --
it is the one signal a dismissed-consent profile cannot suppress.

The last row is the one worth pre-writing. This account has driven Flow for weeks, so a
stored consent cookie is the *expected* reason to see nothing, and a 0/N here is
consistent with both "never renders" and "already dismissed". Only the bundle reading
separates them.

## The control arm (`--dismiss`)

Off by default, because dismissing the bar is what destroys the evidence. Passed, the
probe re-reads the same inventory AFTER running the driver's own `_dismiss_cookie_bar`,
so "the bar blocks" and "dismissing it unblocks" are two separate measurements rather
than one assumption. A result with no control is a coincidence with formatting.

## Cost

Zero. Navigation and DOM reads; with `--dismiss`, one click on a consent button. Nothing
typed, nothing submitted, nothing created or deleted. No credits, no daily quota.

    python scripts/dev/spike_migrated_cookie_bar.py \
        --profile ci-probe --project <project-id> --samples 6
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

# Copied from migrated_composer.py so the probe and the driver cannot disagree about
# which controls are under discussion. COOKIE_BAR/COOKIE_BAR_BUTTONS are PR #781's own
# constants, reproduced verbatim: the probe must test the selector the PR ships, not a
# better one, or a miss here would be evidence about my selector instead of theirs.
READY_ANCHOR = ".settings-trigger-button"
COOKIE_BAR = "#glue-cookie-notification-bar-1, .glue-cookie-notification-bar"
COOKIE_BAR_BUTTONS = "#glue-cookie-notification-bar-1 button, .glue-cookie-notification-bar button"

MIGRATED_URL = "https://flow.google.com/project/{project_id}"
LABS_URL = "https://labs.google/fx/tools/flow/project/{project_id}"

_INVENTORY_JS = r"""
() => {
  const BAR = "#glue-cookie-notification-bar-1, .glue-cookie-notification-bar";
  const rectOf = (el) => {
    const b = el.getBoundingClientRect();
    return {x: Math.round(b.x), y: Math.round(b.y),
            w: Math.round(b.width), h: Math.round(b.height)};
  };
  const describe = (el) => el ? {
    tag: el.tagName.toLowerCase(),
    id: el.id || null,
    cls: [...el.classList].slice(0, 4),
    role: el.getAttribute('role'),
  } : null;
  const visible = (el) => {
    const cs = getComputedStyle(el), b = el.getBoundingClientRect();
    return b.width > 0 && b.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden';
  };

  // --- 1. the bar itself, by PR #781's own selector -------------------------
  const bars = [...document.querySelectorAll(BAR)].map((el) => ({
    tag: el.tagName.toLowerCase(),
    id: el.id || null,
    cls: [...el.classList].slice(0, 4),
    visible: visible(el),
    rect: rectOf(el),
    position: getComputedStyle(el).position,
    zIndex: getComputedStyle(el).zIndex,
    pointerEvents: getComputedStyle(el).pointerEvents,
    buttons: [...el.querySelectorAll('button')].map((b) => ({
      cls: [...b.classList].slice(0, 4),
      ariaLabel: b.getAttribute('aria-label'),
      disabled: b.hasAttribute('disabled'),
    })),
  }));

  // --- 2. is the glue consent MACHINERY even shipped to this page? ----------
  // A warm profile hides a bar it already dismissed; it cannot hide the bundle.
  const srcs = [...document.querySelectorAll('script[src], link[href]')]
    .map((el) => el.getAttribute('src') || el.getAttribute('href') || '')
    .filter((u) => /glue|cookie|consent|funding/i.test(u))
    .slice(0, 20);
  const globals = ['glue', 'gluecookie', 'googlefc', 'cookieChoices']
    .filter((k) => k in window);

  // --- 3. positive observation of WHAT IS THERE at the viewport bottom ------
  // The claim is "sits fixed at the viewport bottom". Enumerate everything that
  // actually does, so a negative on the selector is not a negative on the idea.
  const bottomFixed = [...document.querySelectorAll('body *')]
    .filter((el) => {
      const cs = getComputedStyle(el);
      if (cs.position !== 'fixed' && cs.position !== 'sticky') return false;
      if (!visible(el)) return false;
      const b = el.getBoundingClientRect();
      return b.bottom > window.innerHeight - 160 && b.width > 200;
    })
    .slice(0, 15)
    .map((el) => ({tag: el.tagName.toLowerCase(), id: el.id || null,
                   cls: [...el.classList].slice(0, 4), rect: rectOf(el),
                   zIndex: getComputedStyle(el).zIndex}));

  // --- 4. do the controls hit-test to themselves? --------------------------
  // Existing is not blocking. This is the half of #780 that its own evidence skips.
  const hit = (el) => {
    if (!el) return null;
    const b = el.getBoundingClientRect();
    if (!b.width || !b.height) return {rendered: false};
    const top = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
    const own = !!top && (top === el || el.contains(top) || top.contains(el));
    return {
      rendered: true, rect: rectOf(el), hit_testable: own,
      top: describe(top),
      top_is_cookie_bar: !!top && !!top.closest(BAR),
    };
  };
  const submit = [...document.querySelectorAll('button')].find((b) =>
    [...b.querySelectorAll('mat-icon, i.google-symbols')].some((i) =>
      (i.textContent || '').trim() === 'arrow_forward'));

  return {
    url_origin: location.origin,
    url_path: location.pathname,
    ready_state: document.readyState,
    body_pointer_events: getComputedStyle(document.body).pointerEvents,
    bars: bars,
    bar_button_count: document.querySelectorAll(
      "#glue-cookie-notification-bar-1 button, .glue-cookie-notification-bar button").length,
    glue_resources: srcs,
    glue_globals: globals,
    bottom_fixed: bottomFixed,
    settings_trigger: hit(document.querySelector('.settings-trigger-button')),
    image_submit: hit(submit || null),
  };
}
"""


async def _sample(page: Any, *, samples: int, interval_s: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    t0 = time.monotonic()
    for _ in range(samples):
        try:
            reading = await page.evaluate(_INVENTORY_JS)
            reading["t_ms"] = round((time.monotonic() - t0) * 1000)
        except Exception as exc:  # noqa: BLE001 - an unreadable sample is a datum
            reading = {"t_ms": round((time.monotonic() - t0) * 1000), "error": str(exc)[:200]}
        out.append(reading)
        await asyncio.sleep(interval_s)
    return out


async def _visit(
    client: Any, url: str, *, label: str, samples: int, dismiss: bool = False
) -> dict[str, Any]:
    page = client.transport._page  # noqa: SLF001 - the spike drives gflow's own page
    step(label, f"goto {url}")
    nav_error: str | None = None
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    except Exception as exc:  # noqa: BLE001
        nav_error = str(exc)[:200]
    # The composer mounts 1479-3143 ms after domcontentloaded (2026-09-10 spike), and a
    # cookie bar is furniture that can arrive later still. Sample across the window
    # rather than reading once, or a "0" would only mean "too early".
    readings = await _sample(page, samples=samples, interval_s=1.5)
    landed = str(getattr(page, "url", ""))
    step(label, f"landed on {landed}")
    visit: dict[str, Any] = {
        "label": label,
        "requested_url": url,
        "landed_url": landed,
        "nav_error": nav_error,
        "readings": readings,
    }
    if dismiss:
        # The driver's OWN dismissal, imported rather than reimplemented: a probe that
        # clicked the bar itself would measure my click, not the one that ships.
        from gflow_cli.api.transports.migrated_composer import (  # noqa: PLC0415
            MigratedComposer,
        )

        step(label, "running the driver's _dismiss_cookie_bar")
        try:
            await MigratedComposer()._dismiss_cookie_bar(page)  # noqa: SLF001
            visit["dismiss_error"] = None
        except Exception as exc:  # noqa: BLE001 - a refusal is a datum
            visit["dismiss_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        visit["readings_after_dismiss"] = await _sample(page, samples=samples, interval_s=1.0)
    return visit


def _summarise(visit: dict[str, Any], key: str = "readings") -> dict[str, Any]:
    readings = [r for r in visit.get(key, []) if "error" not in r]
    bars_seen = [b for r in readings for b in r.get("bars", [])]
    visible_bars = [b for b in bars_seen if b.get("visible")]
    blocked = [
        r
        for r in readings
        if (r.get("settings_trigger") or {}).get("top_is_cookie_bar")
        or (r.get("image_submit") or {}).get("top_is_cookie_bar")
    ]
    return {
        "samples": len(visit.get(key, [])),
        "readable": len(readings),
        "bar_elements_matched": len(bars_seen),
        "bar_elements_visible": len(visible_bars),
        "glue_resources": sorted({u for r in readings for u in r.get("glue_resources", [])}),
        "glue_globals": sorted({g for r in readings for g in r.get("glue_globals", [])}),
        "samples_where_a_control_was_covered_by_the_bar": len(blocked),
        "trigger_rendered_in": sum(
            1 for r in readings if (r.get("settings_trigger") or {}).get("rendered")
        ),
        "trigger_hit_testable_in": sum(
            1 for r in readings if (r.get("settings_trigger") or {}).get("hit_testable")
        ),
        "submit_rendered_in": sum(
            1 for r in readings if (r.get("image_submit") or {}).get("rendered")
        ),
        "submit_hit_testable_in": sum(
            1 for r in readings if (r.get("image_submit") or {}).get("hit_testable")
        ),
        "bottom_fixed_tags": sorted(
            {
                f"{e.get('tag')}.{'.'.join(e.get('cls') or [])}"
                for r in readings
                for e in r.get("bottom_fixed", [])
            }
        ),
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--samples", type=int, default=6)
    ap.add_argument("--skip-labs", action="store_true", help="migrated arm only")
    ap.add_argument(
        "--dismiss",
        action="store_true",
        help="control arm: re-read after the driver's own _dismiss_cookie_bar (clicks consent)",
    )
    args = ap.parse_args()

    profile_dir = resolve_profile_dir(args.profile)
    result: dict[str, Any] = {
        "profile": args.profile,
        "project": args.project,
        "question": (
            "does the glue cookie bar render on flow.google.com/project/<id>, and does it "
            "win elementFromPoint over the settings trigger or the image submit? (#780/#781)"
        ),
        "cost": "credit-free: navigation and DOM reads only; the bar is observed, never clicked",
        "cookie_bar_selector": COOKIE_BAR,
        "cookie_bar_buttons_selector": COOKIE_BAR_BUTTONS,
        "visits": [],
    }

    async with build_client(profile_dir) as client:
        result["visits"].append(
            await _visit(
                client,
                MIGRATED_URL.format(project_id=args.project),
                label="migrated",
                samples=args.samples,
                dismiss=args.dismiss,
            )
        )
        if not args.skip_labs:
            result["visits"].append(
                await _visit(
                    client,
                    LABS_URL.format(project_id=args.project),
                    label="labs",
                    samples=args.samples,
                )
            )

    summary: dict[str, Any] = {}
    for visit in result["visits"]:
        summary[visit["label"]] = _summarise(visit)
        if "readings_after_dismiss" in visit:
            summary[f"{visit['label']}_after_dismiss"] = _summarise(
                visit, "readings_after_dismiss"
            )
            summary[f"{visit['label']}_dismiss_error"] = visit.get("dismiss_error")
    result["summary"] = summary
    out = default_out_path("spike_migrated_cookie_bar", ".json")
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    step("done", f"wrote {out}")
    print(json.dumps(result["summary"], indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
