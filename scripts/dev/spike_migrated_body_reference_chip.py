r"""What proves body mode settled on the migrated character editor? ($0)

With the `<flow-slot-chip-button>` anchor in place, `character create
--body-prompt` now clicks **Create body** on flow.google.com and fails one step
later:

    "Create Body was selected, but its generated-face reference did not mount."

That settle check exists for a real reason (#395): activating body mode reuses
the SAME prompt box, so editing it before the transition lands types the body
prompt into the face composer. The signal it waits for is labs-shaped:

    button[data-card-open]:has(img[src*='media.getMediaUrlRedirect'])
                          :has(i.google-symbols:text-is('cancel'))

This dumps what actually appears on the migrated host, before and after the
click, for an entity that ALREADY HAS a portrait (the chip is `disabled`
without one). It records every image-bearing control and every custom element,
so the replacement signal can be a component boundary rather than a guess.

Pass an existing character's entity id with --entity.

Credit-free: navigation, one click on a mode control, DOM reads. Nothing is
typed and nothing is submitted.

    python scripts/dev/spike_migrated_body_reference_chip.py \
        --profile ci-probe --project <p> --entity <e>
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

_MIGRATED_ROOT = "https://flow.google.com"

_SNAPSHOT_JS = r"""() => {
  const LIG = '.google-symbols, .material-symbols-outlined, .material-icons, mat-icon';
  const ligs = (el) => [...el.querySelectorAll(LIG)]
    .map(e => (e.textContent || '').trim())
    .filter(t => /^[a-z0-9_]{2,40}$/.test(t));
  // Custom elements are the component boundaries an anchor should use.
  const customs = {};
  for (const el of document.querySelectorAll('*')) {
    const tag = el.tagName.toLowerCase();
    if (!tag.includes('-')) continue;
    customs[tag] = (customs[tag] || 0) + 1;
  }
  const imaged = [];
  for (const img of document.querySelectorAll('img')) {
    const ctl = img.closest('button, [role="button"], flow-slot-chip-button, *[class*="chip"]');
    const host = ctl || img.parentElement;
    if (!host) continue;
    const r = host.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    const attrs = {};
    for (const a of host.attributes) attrs[a.name] = (a.value || '').slice(0, 50);
    imaged.push({
      host_tag: host.tagName.toLowerCase(),
      host_attrs: attrs,
      host_ligatures: ligs(host).slice(0, 5),
      img_src_head: (img.getAttribute('src') || '').slice(0, 90),
      parent_tag: host.parentElement ? host.parentElement.tagName.toLowerCase() : null,
      text: (host.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40),
    });
  }
  return {
    custom_elements: Object.entries(customs)
      .filter(([t]) => t.startsWith('flow-') || t.startsWith('mat-'))
      .sort((a, b) => b[1] - a[1]).slice(0, 25),
    imaged_controls: imaged.slice(0, 25),
    textboxes: document.querySelectorAll(
      'div[role="textbox"], textarea, .ProseMirror[contenteditable="true"]').length,
  };
}"""


async def _main(profile: str, project: str, entity: str) -> int:
    profile_dir = resolve_profile_dir(profile)
    step("profile", f"{profile} -> {profile_dir}")
    findings: dict[str, Any] = {"profile": profile, "project": project, "entity": entity}

    async with build_client(profile_dir) as client:
        context = client._context  # noqa: SLF001 - spike reads the live context
        page = await context.new_page()
        url = f"{_MIGRATED_ROOT}/project/{project}/character/{entity}"
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:  # noqa: BLE001 - settle is best-effort
            pass
        await page.wait_for_timeout(3000)
        step("landed", page.url)

        findings["before"] = await page.evaluate(_SNAPSHOT_JS)
        step("before", f"imaged_controls={len(findings['before']['imaged_controls'])}")
        for c in findings["before"]["imaged_controls"][:6]:
            step("  img", f"<{c['host_tag']}> {sorted(c['host_attrs'])} src={c['img_src_head'][:60]}")

        body_sel = "flow-slot-chip-button:has(mat-icon:text-is('accessibility_new')) button"
        chip = page.locator(body_sel).first
        if not await chip.count():
            step("abort", "Create body chip not found — does this entity have a portrait?")
        else:
            disabled = await chip.get_attribute("disabled")
            step("chip", f"found; disabled={disabled!r}")
            await chip.click(timeout=5000)
            await page.wait_for_timeout(4000)
            findings["after"] = await page.evaluate(_SNAPSHOT_JS)
            step("after", f"imaged_controls={len(findings['after']['imaged_controls'])}")
            for c in findings["after"]["imaged_controls"][:8]:
                step(
                    "  img",
                    f"<{c['host_tag']}> parent=<{c['parent_tag']}> "
                    f"ligs={c['host_ligatures']} attrs={sorted(c['host_attrs'])} "
                    f"src={c['img_src_head'][:55]}",
                )
            step("customs", str(findings["after"]["custom_elements"][:12]))
            shot = default_out_path("migrated_body_reference", ".png")
            await page.screenshot(path=str(shot))
            findings["screenshot"] = shot.name

        out = default_out_path("migrated_body_reference")
        out.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
        step("wrote", str(out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="ci-probe")
    ap.add_argument("--project", required=True)
    ap.add_argument("--entity", required=True)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.profile, args.project, args.entity)))
