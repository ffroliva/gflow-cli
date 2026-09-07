r"""Why does the character editor's readiness gate time out while a textbox is on the page? ($0)

On 2026-09-07 ``gflow character create --profile promo-denon82`` failed with
``Character editor not ready: prompt textbox not visible within 20 s`` — the exact
signature that produced the false "character create is labs-only, ever" claim on
2026-09-06. The incident bundle's ``ui.json`` disagreed with the message: it reported
``textarea: 1``, ``textboxes: 1``, ``video: 51``, ``img: 35``, ``overlays: []`` and
``dialog: 0``. A prompt-ish element WAS on the page.

So the gate's own selector
``div[role="textbox"][data-slate-editor="true"], div.ProseMirror[contenteditable="true"]``
either (a) does not match this account's carrier, or (b) matches an element that is
never ``visible`` because something occludes it — the two iframes in the tally are the
prime suspects (a cookie-consent banner keeps an element that EXISTS from ever being
visible, which is what ``wait_for(state="visible")`` actually waits on).

This spike makes the POSITIVE observation the rule demands: it navigates to the
character editor route on BOTH hosts and, for every plausible prompt element, records
tag / attributes / box / visibility / and what ``elementFromPoint`` returns over its
centre. It types nothing and submits nothing.

Credit-free: navigation and DOM reads only. Nothing is created and nothing is deleted —
it probes an entity that already exists.

    python scripts/dev/spike_character_editor_ready_gate.py \
        --profile promo-denon82 \
        --project 7702600d-ea17-4f39-946f-99cf7fa4b39f \
        --entity 0fcb2a5d-91a2-4719-9bf2-13bbd778dcfb
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

# The gate's own selector, split so a miss names WHICH half missed.
GATE_SLATE = 'div[role="textbox"][data-slate-editor="true"]'
GATE_PROSEMIRROR = 'div.ProseMirror[contenteditable="true"]'

#: Every element that could plausibly be "the prompt box". Deliberately wider than
#: the gate, so a miss is attributable to the anchor rather than to absence.
CANDIDATES: dict[str, str] = {
    "gate_slate": GATE_SLATE,
    "gate_prosemirror": GATE_PROSEMIRROR,
    "gate_full": f"{GATE_SLATE}, {GATE_PROSEMIRROR}",
    "role_textbox_any": '[role="textbox"]',
    "contenteditable_any": '[contenteditable="true"]',
    "prosemirror_any": ".ProseMirror",
    "slate_any": "[data-slate-editor]",
    "textarea": "textarea",
    "input_text": 'input[type="text"], input:not([type])',
}

# Structural inventory JS. Returns what IS there — never a label, never a guess.
_INVENTORY_JS = """
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return {
      w: Math.round(r.width), h: Math.round(r.height),
      x: Math.round(r.x), y: Math.round(r.y),
      display: s.display, visibility: s.visibility, opacity: s.opacity,
      // Playwright's "visible" is: non-empty box AND not visibility:hidden.
      pw_visible: r.width > 0 && r.height > 0 && s.visibility !== 'hidden',
    };
  };
  const occluder = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return null;
    const cx = Math.min(Math.max(r.x + r.width / 2, 1), innerWidth - 1);
    const cy = Math.min(Math.max(r.y + r.height / 2, 1), innerHeight - 1);
    const top = document.elementFromPoint(cx, cy);
    if (!top) return 'none';
    if (top === el || el.contains(top) || top.contains(el)) return 'self';
    return top.tagName.toLowerCase()
         + (top.id ? '#' + top.id : '')
         + (top.className && typeof top.className === 'string'
              ? '.' + top.className.trim().split(/\\s+/).slice(0, 3).join('.') : '');
  };
  const describe = (el) => ({
    tag: el.tagName.toLowerCase(),
    attrs: Object.fromEntries([...el.attributes].map(a => [a.name, a.value.slice(0, 120)])),
    ...vis(el),
    occluded_by: occluder(el),
    text_len: (el.textContent || '').length,
  });

  const out = { candidates: {}, iframes: [], ligature_carriers: {}, custom_elements: {} };
  for (const [name, sel] of Object.entries(window.__SPIKE_CANDIDATES__)) {
    const els = [...document.querySelectorAll(sel)];
    out.candidates[name] = { count: els.length, elements: els.slice(0, 4).map(describe) };
  }

  // Iframes: a consent frame is the classic invisible occluder.
  out.iframes = [...document.querySelectorAll('iframe')].map(f => ({
    src_host: (() => { try { return new URL(f.src, location.href).host; } catch { return f.src.slice(0, 60); } })(),
    id: f.id, title_len: (f.title || '').length, ...vis(f),
  }));

  // WHICH tag carries the icon ligatures. labs renders <i class="google-symbols">,
  // the migrated host renders <mat-icon>. A mismatch here looks exactly like a
  // missing feature.
  for (const sel of ['i.google-symbols', 'mat-icon', 'span.material-symbols-outlined']) {
    const els = [...document.querySelectorAll(sel)];
    out.ligature_carriers[sel] = {
      count: els.length,
      sample: [...new Set(els.map(e => (e.textContent || '').trim()))].slice(0, 25),
    };
  }

  // Custom elements are component boundaries — the best anchors available.
  for (const el of document.querySelectorAll('*')) {
    const t = el.tagName.toLowerCase();
    if (t.includes('-')) out.custom_elements[t] = (out.custom_elements[t] || 0) + 1;
  }

  out.body_text_head = (document.body.innerText || '').slice(0, 400);
  out.url = location.href;
  out.title = document.title;
  return out;
}
"""


async def probe(page: Any, url: str, *, settle_s: float) -> dict[str, Any]:
    """Navigate to *url*, let it settle, and return the structural inventory."""
    record: dict[str, Any] = {"requested_url": url}
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except Exception as exc:  # noqa: BLE001 - a nav failure is itself the finding
        record["nav_error"] = str(exc)[:300]
        return record
    # The gate waits 20 s; settle for longer so "it needed more time" is ruled out
    # rather than assumed.
    await page.wait_for_timeout(int(settle_s * 1000))
    record["final_url"] = page.url
    record["hopped"] = page.url.split("/")[2] != url.split("/")[2]
    await page.evaluate(
        "(c) => { window.__SPIKE_CANDIDATES__ = c; }",
        CANDIDATES,
    )
    record["inventory"] = await page.evaluate(_INVENTORY_JS)
    return record


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--entity", required=True)
    ap.add_argument("--settle", type=float, default=25.0, help="Seconds to settle (gate uses 20).")
    args = ap.parse_args()

    profile_dir = resolve_profile_dir(args.profile)
    routes = {
        "labs": (
            f"https://labs.google/fx/en/tools/flow/project/{args.project}"
            f"/character/{args.entity}"
        ),
        "migrated": f"https://flow.google.com/project/{args.project}/character/{args.entity}",
    }

    results: dict[str, Any] = {"profile": args.profile, "routes": {}}
    async with build_client(profile_dir) as client:
        context = client._context  # noqa: SLF001 - spike reads the live context
        page = await context.new_page()
        for lane, url in routes.items():
            step(lane, f"probing {url}")
            results["routes"][lane] = await probe(page, url, settle_s=args.settle)
            inv = results["routes"][lane].get("inventory", {})
            cands = inv.get("candidates", {})
            step(
                lane,
                "final="
                + str(results["routes"][lane].get("final_url", "?"))[:90]
                + "  gate_full="
                + str(cands.get("gate_full", {}).get("count"))
                + "  role_textbox="
                + str(cands.get("role_textbox_any", {}).get("count"))
                + "  contenteditable="
                + str(cands.get("contenteditable_any", {}).get("count")),
            )

    out = default_out_path("spike_character_editor_ready_gate")
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    step("done", f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
