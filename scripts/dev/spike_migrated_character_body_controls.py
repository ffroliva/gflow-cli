r"""How is "Create body" anchored on the migrated character editor? ($0)

`character create --body-prompt` fails on flow.google.com at body-mode
activation: `character_slot_add_failed`, then "Body prompt box did not mount
within 10s". The face slot works, so this is the last gap to a full character
(name + personality + voice + portrait + body).

The selectors in play were written against labs.google:

    _CHARACTER_SLOT_ADD_SELECTOR   [role='button']:has(i.google-symbols:text-is('add_2'))
    _CHARACTER_BODY_MODE_SELECTOR  button:has(img) + button:has(…'accessibility_new')

Both assume labs' ligature carrier (`<i class="google-symbols">`) and labs' DOM
shape. A bare `accessibility_new` selector is NOT an acceptable fallback: the
project-level Characters navigation carries the same ligature, and clicking it
navigates away instead of activating body mode. So the replacement has to be
*scoped*, which means knowing the real structure rather than guessing it.

This dumps, for every control carrying `portrait`, `accessibility_new`, `add_2`
or `upload`: its tag, its ligature carrier tag, its own attributes, and its
parent + previous/next sibling — enough to write a scoped, locale-invariant
anchor.

Credit-free: one free createEntity, navigation, DOM reads, one free delete.

    python scripts/dev/spike_migrated_character_body_controls.py \
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

_MIGRATED_ROOT = "https://flow.google.com"

_CONTROLS_JS = r"""() => {
  const WANTED = new Set(['portrait', 'accessibility_new', 'add_2', 'upload', 'arrow_drop_down']);
  const LIG = '.google-symbols, .material-symbols-outlined, .material-icons, mat-icon';
  const desc = (el) => {
    if (!el) return null;
    const ligs = [...el.querySelectorAll(LIG)]
      .map(e => (e.textContent || '').trim())
      .filter(t => /^[a-z0-9_]{2,40}$/.test(t));
    return {
      tag: el.tagName.toLowerCase(),
      cls: (el.className || '').toString().slice(0, 80),
      ligatures: ligs.slice(0, 4),
      text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40),
    };
  };
  const out = [];
  for (const icon of document.querySelectorAll(LIG)) {
    const lig = (icon.textContent || '').trim();
    if (!WANTED.has(lig)) continue;
    const ctl = icon.closest('button, a, [role="button"], [role="tab"], [role="radio"]');
    if (!ctl) continue;
    const rect = ctl.getBoundingClientRect();
    const attrs = {};
    for (const a of ctl.attributes) attrs[a.name] = (a.value || '').slice(0, 60);
    out.push({
      ligature: lig,
      carrier_tag: icon.tagName.toLowerCase(),
      carrier_cls: (icon.className || '').toString().slice(0, 60),
      control_tag: ctl.tagName.toLowerCase(),
      control_attrs: attrs,
      control_text: (ctl.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40),
      visible: rect.width > 0 && rect.height > 0,
      parent: desc(ctl.parentElement),
      prev: desc(ctl.previousElementSibling),
      next: desc(ctl.nextElementSibling),
    });
  }
  return out;
}"""


async def _main(profile: str, project: str) -> int:
    profile_dir = resolve_profile_dir(profile)
    step("profile", f"{profile} -> {profile_dir}")
    findings: dict[str, Any] = {"profile": profile, "project": project}

    async with build_client(profile_dir) as client:
        entity_id = await client.create_entity(project)
        step("entity", f"created {entity_id} (free tRPC)")
        try:
            context = client._context  # noqa: SLF001 - spike reads the live context
            page = await context.new_page()
            url = f"{_MIGRATED_ROOT}/project/{project}/character/{entity_id}"
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:  # noqa: BLE001 - settle is best-effort
                pass
            await page.wait_for_timeout(3000)
            step("landed", page.url)

            controls = await page.evaluate(_CONTROLS_JS)
            findings["controls"] = controls
            shot = default_out_path("migrated_body_controls", ".png")
            await page.screenshot(path=str(shot))
            findings["screenshot"] = shot.name

            for c in controls:
                step(
                    c["ligature"],
                    f"<{c['control_tag']}> carrier=<{c['carrier_tag']}> "
                    f"vis={c['visible']} text={c['control_text']!r} "
                    f"attrs={sorted(c['control_attrs'])}",
                )
                step(
                    "  ctx",
                    f"parent={c['parent'] and c['parent']['tag']}"
                    f"({c['parent'] and c['parent']['ligatures']}) "
                    f"prev={c['prev'] and c['prev']['tag']}"
                    f"({c['prev'] and c['prev']['ligatures']}) "
                    f"next={c['next'] and c['next']['tag']}"
                    f"({c['next'] and c['next']['ligatures']})",
                )
        finally:
            try:
                await client.delete_characters(project, [entity_id])
                step("cleanup", f"deleted {entity_id}")
            except Exception as exc:  # noqa: BLE001 - cleanup is best-effort
                step("cleanup", f"FAILED to delete {entity_id}: {exc}")
            out = default_out_path("migrated_body_controls")
            out.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
            step("wrote", str(out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="ci-probe")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.profile, args.project)))
