r"""What IS the prompt box on the migrated character editor, and what hides it? ($0)

Follow-up to ``spike_migrated_character_surface.py``, which established that
``flow.google.com/project/P/character/E`` does NOT redirect and renders a real
character editor (ligatures ``portrait``, ``accessibility_new``,
``voice_selection``, ``upload``). Two facts from that run need nailing down
before any driver work:

  1. the route's ARIA role tally contained **no ``textbox``** — yet three
     textbox-ish elements existed. gflow's readiness gate waits for
     ``div[role="textbox"][data-slate-editor="true"]``, a Slate anchor. If the
     migrated editor is a ``<textarea>`` or a bare ``contenteditable``, the gate
     can never pass and the surface looks absent when it is merely different.
  2. the body text carried a cookie-consent banner. A consent overlay would keep
     an element that EXISTS from ever being ``visible``, which is what the gate
     actually waits on.

This spike creates a real entity over tRPC (free, and proven to work on the
migrated host), navigates straight to its editor route, dumps every candidate
prompt element with its tag / attributes / visibility / occluder, then deletes
the entity again so nothing is left behind.

Credit-free: one free createEntity, navigation, DOM reads, one free delete.
Nothing is typed and nothing is submitted.

    python scripts/dev/spike_migrated_character_editor_anchor.py \
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

# Every element that could plausibly be "the prompt box", with the facts a
# selector needs: tag, the attributes that are locale-invariant, whether it is
# actually visible, and — when it is not — what is sitting on top of it.
_CANDIDATES_JS = r"""() => {
  const SEL = 'div[role="textbox"], textarea, [contenteditable="true"], input[type="text"]';
  const out = [];
  for (const el of document.querySelectorAll(SEL)) {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const visible = r.width > 0 && r.height > 0 &&
                    cs.visibility !== 'hidden' && cs.display !== 'none' &&
                    cs.opacity !== '0';
    let occluder = null;
    if (r.width > 0 && r.height > 0) {
      const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      if (top && top !== el && !el.contains(top)) {
        occluder = {
          tag: top.tagName.toLowerCase(),
          cls: (top.className || '').toString().slice(0, 120),
          role: top.getAttribute('role'),
          text: (top.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80),
        };
      }
    }
    const attrs = {};
    for (const a of el.attributes) attrs[a.name] = (a.value || '').slice(0, 100);
    out.push({
      tag: el.tagName.toLowerCase(),
      attrs,
      visible,
      rect: {w: Math.round(r.width), h: Math.round(r.height),
             x: Math.round(r.left), y: Math.round(r.top)},
      slate: el.hasAttribute('data-slate-editor'),
      placeholder: el.getAttribute('placeholder') || el.getAttribute('data-placeholder'),
      occluder,
    });
  }
  return out;
}"""

# Consent / cookie / dialog overlays, anchored structurally (never on text).
_OVERLAY_JS = r"""() => {
  const out = [];
  const sels = ['[role="dialog"]', '[role="alertdialog"]', 'dialog',
                '[aria-modal="true"]', 'iframe'];
  for (const s of sels) {
    for (const el of document.querySelectorAll(s)) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      out.push({
        sel: s,
        tag: el.tagName.toLowerCase(),
        src: el.getAttribute('src') ? el.getAttribute('src').slice(0, 120) : null,
        cls: (el.className || '').toString().slice(0, 120),
        rect: {w: Math.round(r.width), h: Math.round(r.height)},
        text: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120),
      });
    }
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
        findings["entity_id"] = entity_id
        try:
            context = client._context  # noqa: SLF001 - spike reads the live context
            page = await context.new_page()
            url = f"{_MIGRATED_ROOT}/project/{project}/character/{entity_id}"
            step("goto", f"DIRECT {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:  # noqa: BLE001 - settle is best-effort
                pass
            await page.wait_for_timeout(3000)
            step("landed", page.url)
            findings["landed_url"] = page.url
            findings["entity_still_in_url"] = entity_id in page.url

            findings["candidates"] = await page.evaluate(_CANDIDATES_JS)
            findings["overlays"] = await page.evaluate(_OVERLAY_JS)

            # What gflow actually waits for, asked directly.
            gate = 'div[role="textbox"][data-slate-editor="true"]'
            findings["gflow_gate_selector"] = gate
            findings["gflow_gate_count"] = await page.locator(gate).count()
            shot = default_out_path("migrated_char_anchor", ".png")
            await page.screenshot(path=str(shot))
            findings["screenshot"] = shot.name

            step(
                "gate",
                f"gflow selector matches {findings['gflow_gate_count']} element(s); "
                f"{len(findings['candidates'])} candidate box(es); "
                f"{len(findings['overlays'])} overlay(s)",
            )
            for c in findings["candidates"]:
                step(
                    "candidate",
                    f"<{c['tag']}> visible={c['visible']} slate={c['slate']} "
                    f"rect={c['rect']['w']}x{c['rect']['h']} "
                    f"occluder={(c['occluder'] or {}).get('tag')} "
                    f"attrs={sorted(c['attrs'])}",
                )
            for o in findings["overlays"]:
                step("overlay", f"{o['tag']} {o['rect']} src={o['src']} text={o['text'][:60]!r}")
        finally:
            try:
                await client.delete_characters(project, [entity_id])
                step("cleanup", f"deleted {entity_id}")
                findings["cleaned_up"] = True
            except Exception as exc:  # noqa: BLE001 - cleanup is best-effort
                step("cleanup", f"FAILED to delete {entity_id}: {exc}")
                findings["cleaned_up"] = False
            out = default_out_path("migrated_char_anchor")
            out.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
            step("wrote", str(out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="ci-probe")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.profile, args.project)))
