r"""Is the migrated composer's submit control really absent after typing? ($0)

On 2026-09-07 every `gflow video t2v` on the migrated host died with
``UiSelectorDriftError: the submit button (arrow_forward) is missing after the prompt
was typed``, reproducibly. The incident bundle is useless for diagnosing it: the video
path's ``finally`` parks the pooled page at ``about:blank`` BEFORE the CLI layer captures
the incident, so ``ui.json`` reports ``div: 0, button: 0, textarea: 0`` and the
screenshot is a blank white frame. The capture describes the teardown, not the failure.

So the driver says "missing" and the evidence says nothing at all — which is exactly the
shape this repo has been bitten by twice this week. This probe makes the positive
observation instead: it opens the composer, inventories every button and ligature, types
a prompt, inventories again, and diffs the two.

If ``arrow_forward`` is present after typing, the driver's locator is wrong. If a
DIFFERENT ligature appears only after typing, that is the new submit control and its name
is the finding. If nothing appears, the composer genuinely does not offer submit to this
account, and THAT is worth saying — but only then.

Nothing is submitted. The prompt is typed and left; no generation is started, so no
credits are spent.

    python scripts/dev/spike_migrated_submit_anchor.py \
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

PROMPT = "A slow push in across a high desert ridge at golden hour."

#: Every control the composer could plausibly submit with, recorded structurally.
_INVENTORY_JS = """
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return {
      w: Math.round(r.width), h: Math.round(r.height),
      visible: r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none',
      disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
    };
  };
  const buttons = [...document.querySelectorAll('button, [role="button"]')].map(b => ({
    tag: b.tagName.toLowerCase(),
    ligatures: [...b.querySelectorAll('mat-icon, i')].map(i => (i.textContent || '').trim()),
    aria_label: b.getAttribute('aria-label'),
    type: b.getAttribute('type'),
    classes: (typeof b.className === 'string' ? b.className : '').trim().split(/\\s+/).slice(0, 4),
    ...vis(b),
  }));
  return {
    button_count: buttons.length,
    // Every ligature ON THE PAGE, not only inside buttons -- the submit control may
    // not be a button element at all on this host.
    all_ligatures: [...new Set(
      [...document.querySelectorAll('mat-icon, i.google-symbols')]
        .map(e => (e.textContent || '').trim()).filter(Boolean)
    )].sort(),
    buttons: buttons.filter(b => b.visible),
    editable: [...document.querySelectorAll('[contenteditable="true"], textarea')].map(e => ({
      tag: e.tagName.toLowerCase(),
      classes: (typeof e.className === 'string' ? e.className : '').trim().split(/\\s+/).slice(0, 3),
      text_len: (e.textContent || e.value || '').length,
      ...vis(e),
    })),
    url: location.href,
  };
}
"""


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    args = ap.parse_args()

    findings: dict[str, Any] = {"profile": args.profile, "project": args.project}
    url = f"https://flow.google.com/project/{args.project}"

    async with build_client(resolve_profile_dir(args.profile)) as client:
        page = await client._context.new_page()  # noqa: SLF001 - spike reads the live context
        step("goto", url)
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(8_000)
        if "flow.google.com/project/" not in page.url:
            print(f"[spike] PROBE FAILED: landed on {page.url}", file=sys.stderr)
            return 3

        findings["before"] = await page.evaluate(_INVENTORY_JS)
        step("before", f"{findings['before']['button_count']} buttons, "
                       f"ligatures={findings['before']['all_ligatures']}")

        # Type into the composer exactly as the driver does, then look again.
        box = page.locator('div.ProseMirror[contenteditable="true"], [contenteditable="true"]').first
        try:
            await box.wait_for(state="visible", timeout=20_000)
            await box.click()
            await page.keyboard.type(PROMPT, delay=8)
            await page.wait_for_timeout(3_000)
            findings["typed"] = True
        except Exception as exc:  # noqa: BLE001 - a failure to type is itself the finding
            findings["typed"] = False
            findings["type_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            step("type", f"FAILED {findings['type_error']}")

        findings["after"] = await page.evaluate(_INVENTORY_JS)
        step("after", f"{findings['after']['button_count']} buttons, "
                      f"ligatures={findings['after']['all_ligatures']}")

        shot = default_out_path("spike_migrated_submit_anchor", ".png")
        await page.screenshot(path=str(shot))
        findings["screenshot"] = shot.name

    before = set(findings["before"]["all_ligatures"])
    after = set(findings["after"]["all_ligatures"])
    findings["ligatures_appearing_after_typing"] = sorted(after - before)
    findings["arrow_forward_present_after"] = "arrow_forward" in after
    findings["submit_candidates_after"] = [
        b for b in findings["after"]["buttons"] if b["ligatures"] and not b["disabled"]
    ]

    out = default_out_path("spike_migrated_submit_anchor")
    out.write_text(json.dumps(findings, indent=2), encoding="utf-8")

    step("NEW AFTER TYPING", str(findings["ligatures_appearing_after_typing"]) or "(none)")
    step("arrow_forward present", str(findings["arrow_forward_present_after"]))
    step("done", f"wrote {out} and {shot.name}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
