#!/usr/bin/env python3
r"""#493 DOM capture — what does the Agent pill actually look like when classic
recovery fails?

WHY: `mode_control.AGENT_TOGGLE_SELECTOR` is
``button[aria-pressed]:has(span.content)``. #493's reporter has an "Agente" pill
that matches neither it nor the composer-scoped variant, so `ensure_media_mode`
clicks nothing (`acted=False`), the classic media panel is never restored, the
`crop_*` `mode_switch_trigger` probe misses, and the run dies with
`UiSelectorDriftError` exit 23.

That exact state was hit locally on 2026-08-14 (`ensure_media_mode acted=False`
→ "could not open the settings popover at all"), so the failing DOM is
capturable without the reporter.

This dumps every plausible toggle candidate with the attributes our selectors
key on, and reports which production selectors match. Evidence for a selector
fix — not a guess.

**Credit-free:** navigation + DOM reads only. No prompt, no Generate.

Usage:
    PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\capture_agent_pill_dom.py \
        --profile denon82 --project <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts" / "dev"))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.transports.mode_control import (  # noqa: E402
    AGENT_TOGGLE_SELECTOR,
    CROP_SELECTORS,
    SIDEBAR_CLOSE_SELECTOR,
)

_DOM_JS = """() => {
  const attrs = (e) => Object.fromEntries(
    [...e.attributes].map(a => [a.name, a.value.slice(0, 120)]));
  // Every button-ish node that could plausibly be the Agent pill.
  const cands = [...document.querySelectorAll(
      "button, [role='button'], [role='switch'], [role='tab']")]
    .map(e => ({
      tag: e.tagName.toLowerCase(),
      text: (e.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 40),
      attrs: attrs(e),
      ligatures: [...e.querySelectorAll('i')].map(i => (i.textContent || '').trim()),
      child_spans: [...e.querySelectorAll('span')]
        .map(s => s.className).filter(Boolean).slice(0, 4),
      has_aria_pressed: e.hasAttribute('aria-pressed'),
      aria_pressed: e.getAttribute('aria-pressed'),
    }))
    // Keep the ones that look like a mode toggle: aria-pressed, or agent-ish text.
    .filter(o => o.has_aria_pressed || /agent|agente/i.test(o.text))
    .slice(0, 30);
  return {
    candidates: cands,
    total_aria_pressed: document.querySelectorAll('[aria-pressed]').length,
    lang: document.documentElement.lang || null,
  };
}"""


async def _wait_editor(page: Any) -> bool:
    for _ in range(30):
        if (
            await page.locator('div[role="textbox"], textarea').count() > 0
            or await page.locator("button").count() > 8
        ):
            return True
        await page.wait_for_timeout(1000)
    return False


async def _selector_hits(page: Any) -> dict[str, int]:
    hits = {
        "AGENT_TOGGLE_SELECTOR": await page.locator(AGENT_TOGGLE_SELECTOR).count(),
        "SIDEBAR_CLOSE_SELECTOR": await page.locator(SIDEBAR_CLOSE_SELECTOR).count(),
    }
    hits["ANY_CROP_TRIGGER"] = sum(
        [await page.locator(sel).count() for sel in CROP_SELECTORS],
    )
    return hits


async def _run(profile: str, project: str) -> int:
    out: dict[str, Any] = {"profile": profile, "project": project, "phases": {}}
    async with build_client(resolve_profile_dir(profile)) as client:
        ctx = client._context  # noqa: SLF001
        assert ctx is not None
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        await page.goto(
            routes.project_editor_url("en", project),
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        await _wait_editor(page)

        on_load = {"selectors": await _selector_hits(page), "dom": await page.evaluate(_DOM_JS)}
        out["phases"]["on_load"] = on_load
        step("load", f"selectors={on_load['selectors']} lang={on_load['dom']['lang']}")

        # Drive the production recovery and capture what it saw.
        from gflow_cli.api.transports import mode_control  # noqa: PLC0415

        acted = await mode_control.ensure_media_mode(page, allow_reload=True)
        await _wait_editor(page)
        after = {
            "ensure_media_mode_acted": acted,
            "selectors": await _selector_hits(page),
            "dom": await page.evaluate(_DOM_JS),
        }
        out["phases"]["after_ensure_media_mode"] = after
        step("recover", f"acted={acted} selectors={after['selectors']}")

        classic_ok = after["selectors"]["ANY_CROP_TRIGGER"] > 0
        out["classic_restored"] = classic_ok
        step("verdict", f"classic media panel restored = {classic_ok}")

    path = default_out_path("agent_pill_dom")
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    step("done", f"wrote {path}")

    print("\n=== VERDICT ===")
    for phase, data in out["phases"].items():
        s = data["selectors"]
        print(
            f"  {phase:<26} crop={s['ANY_CROP_TRIGGER']} agent_toggle={s['AGENT_TOGGLE_SELECTOR']}"
        )
        for c in data["dom"]["candidates"][:6]:
            print(
                f"      {c['tag']:<7} pressed={c['aria_pressed']!s:<5} "
                f"text={c['text'][:22]!r:<24} spans={c['child_spans']}"
            )
    print(f"  classic restored: {out['classic_restored']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    return asyncio.run(_run(args.profile, args.project))


if __name__ == "__main__":
    raise SystemExit(main())
