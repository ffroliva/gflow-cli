r"""Does the migrated project composer's settings overlay carry an IMAGE mode? ($0)

Settles the contradiction at the heart of #692.

`src/gflow_cli/api/client.py:2426-2436` asserts, as "measured, not assumed", that "the
migrated project composer has no image-generation mode ... its settings radios are
grid/batch and size". The 2026-09-04 handoff-mechanism spike doc (lines 123-136)
enumerates the SAME overlay — same trigger, same `.cdk-overlay-pane`, same
`[role='radiogroup']` — and records six radiogroups, the first of which is:

    [imageImage, videocamVideo]                     mode

and `MigratedComposer.apply_video_settings` already drives that axis, always passing
`videocam`.

Only one of those can be true of the same panel. This probe opens the overlay and reads
it, so the answer is an observation rather than a citation.

WHY THIS SCRIPT EXISTS RATHER THAN `spike_migrated_image_capability.py`: that script's
overlay-open is best-effort and swallows its exception (`:138-146`), so it completes and
prints "the migrated composer looks VIDEO-ONLY" even when it read only the default view
and never opened the panel. A probe that cannot reach the surface it exists to read must
FAIL, never conclude. Every step below is therefore a hard error, and the exit code says
which one.

    A SELECTOR THAT DOES NOT MATCH IS EVIDENCE ABOUT THE SELECTOR.
    IT IS NEVER EVIDENCE ABOUT THE FEATURE.

Credit-free: navigation, one click on the settings trigger, DOM reads. Nothing is typed,
nothing is submitted, nothing is created or deleted.

    python scripts/dev/spike_migrated_composer_mode_axis.py \
        --profile ci-probe --project 1e4efe0d-afcf-4e0d-ae4d-b4431f2d73de
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

# The composer's own anchors, copied from migrated_composer.py:72-82 so this probe and
# the driver cannot disagree about which panel is under discussion.
READY_ANCHOR = ".settings-trigger-button"
OVERLAY = ".cdk-overlay-pane"
RADIOGROUP = "[role='radiogroup']"
RADIO = "[role='radio']"

#: Read the overlay exactly as the 2026-09-04 capture did: every radiogroup, every radio,
#: with its ligature carrier text AND its own aria-label, plus whether it is selected,
#: enabled and hit-testable. "Present" is not the same as "clickable" — record both.
_READ_OVERLAY_JS = """
(sel) => {
  const panes = [...document.querySelectorAll(sel.overlay)]
      .filter(p => p.querySelector(sel.radiogroup));
  const pane = panes[panes.length - 1];
  if (!pane) return {pane_found: false, pane_count: document.querySelectorAll(sel.overlay).length};
  const groups = [...pane.querySelectorAll(sel.radiogroup)].map((g, gi) => ({
    index: gi,
    aria_label: g.getAttribute('aria-label'),
    radios: [...g.querySelectorAll(sel.radio)].map(r => {
      const box = r.getBoundingClientRect();
      const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
      const top = (box.width && box.height) ? document.elementFromPoint(cx, cy) : null;
      return {
        text: (r.textContent || '').trim(),
        aria_label: r.getAttribute('aria-label'),
        aria_checked: r.getAttribute('aria-checked'),
        aria_disabled: r.getAttribute('aria-disabled'),
        ligatures: [...r.querySelectorAll('mat-icon, i')].map(i => (i.textContent || '').trim()),
        w: Math.round(box.width), h: Math.round(box.height),
        hit_testable: !!top && (top === r || r.contains(top) || top.contains(r)),
      };
    }),
  }));
  return {
    pane_found: true,
    group_count: groups.length,
    radio_total: groups.reduce((n, g) => n + g.radios.length, 0),
    groups,
  };
}
"""


class ProbeFailedError(RuntimeError):
    """A step this probe cannot complete. Never downgraded to a verdict."""


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    args = ap.parse_args()

    profile_dir = resolve_profile_dir(args.profile)
    findings: dict[str, Any] = {"profile": args.profile, "project": args.project}
    url = f"https://flow.google.com/project/{args.project}"

    async with build_client(profile_dir) as client:
        context = client._context  # noqa: SLF001 - spike reads the live context
        page = await context.new_page()

        step("goto", url)
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(6_000)
        findings["landed_url"] = page.url
        step("landed", page.url)
        if "flow.google.com/project/" not in page.url:
            raise ProbeFailedError(
                f"did not land on a migrated project page (got {page.url}) — "
                "the account is signed out or not on the migrated cohort, so this run "
                "says NOTHING about the composer"
            )

        trigger = page.locator(READY_ANCHOR).first
        await trigger.wait_for(state="visible", timeout=30_000)
        findings["trigger_found"] = True
        step("trigger", f"{READY_ANCHOR} visible")

        # HARD, not best-effort. A settings panel that will not open is a failed probe.
        await trigger.click(timeout=10_000)
        await page.wait_for_timeout(2_000)

        overlay = page.locator(OVERLAY).filter(has=page.locator(RADIOGROUP)).last
        try:
            await overlay.wait_for(state="visible", timeout=15_000)
        except Exception as exc:
            raise ProbeFailedError(
                f"the settings trigger was clicked but no {OVERLAY} containing "
                f"{RADIOGROUP} became visible — the panel did not open, so this run "
                "cannot speak to what is inside it"
            ) from exc
        step("overlay", "open")

        result = await page.evaluate(
            _READ_OVERLAY_JS,
            {"overlay": OVERLAY, "radiogroup": RADIOGROUP, "radio": RADIO},
        )
        if not result.get("pane_found"):
            raise ProbeFailedError(f"overlay visible but unreadable: {result}")
        findings["overlay"] = result

    # ---- the question, answered from the read -------------------------------
    flat = [
        {"group": g["index"], "group_label": g["aria_label"], **r}
        for g in findings["overlay"]["groups"]
        for r in g["radios"]
    ]
    image_radios = [
        r
        for r in flat
        if "image" in (r["text"] or "").casefold()
        or "image" in (r["aria_label"] or "").casefold()
        or any("image" in lig.casefold() for lig in r["ligatures"])
    ]
    findings["image_radios"] = image_radios
    findings["answer"] = {
        "image_mode_present": bool(image_radios),
        "image_mode_hit_testable": any(r["hit_testable"] for r in image_radios),
        "group_count": findings["overlay"]["group_count"],
        "radio_total": findings["overlay"]["radio_total"],
    }

    out = default_out_path("spike_migrated_composer_mode_axis")
    out.write_text(json.dumps(findings, indent=2), encoding="utf-8")

    step("groups", f"{findings['overlay']['group_count']} groups, "
                   f"{findings['overlay']['radio_total']} radios")
    for g in findings["overlay"]["groups"]:
        labels = ", ".join(
            f"{(r['text'] or r['aria_label'] or '?')}"
            f"{'*' if r['aria_checked'] == 'true' else ''}"
            for r in g["radios"]
        )
        step(f"group{g['index']}", f"[{labels}]  aria-label={g['aria_label']!r}")
    step(
        "ANSWER",
        f"image_mode_present={findings['answer']['image_mode_present']}  "
        f"hit_testable={findings['answer']['image_mode_hit_testable']}",
    )
    step("done", f"wrote {out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except ProbeFailedError as exc:
        print(f"[spike] PROBE FAILED: {exc}", file=sys.stderr, flush=True)
        print(
            "[spike] This is a failed measurement, NOT evidence of absence.",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(3)
