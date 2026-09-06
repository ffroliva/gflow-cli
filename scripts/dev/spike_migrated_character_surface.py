r"""Does flow.google.com have a character surface at all, and what drives it? ($0)

The claim on the table — "the character editor is labs-only, ever" — comes from a
selector that timed out, not from a recon. Two things were never separated:

  1. flow.google.com has no cast/character feature.
  2. flow.google.com HAS one, and gflow is asking for the wrong URL.

gflow builds a **labs** route (``labs.google/fx/<locale>/tools/flow/project/P/
character/E``) and lets the app hand off. The handoff strips ``/fx/<locale>/
tools/flow``, so we land on ``flow.google.com/project/P/character/E`` — a path we
never verified exists on that app. A SPA serving its shell for an unknown path
renders no prompt box and looks exactly like "no feature".

This spike goes **straight to flow.google.com** — no labs bounce — and asks:

* what does the migrated project page expose (ligatures, roles, routes)?
* is there a cast/character affordance, and what URL does it navigate to?
* does ``/project/P/character/E`` render an editor, a 404, or the bare shell?
* which ``batchexecute`` rpcids fire while we look?

Credit-free: navigation, clicks on navigation affordances, and DOM reads. Nothing
is typed and nothing is submitted.

    python scripts/dev/spike_migrated_character_surface.py --profile ci-probe \
        --project 1e4efe0d-afcf-4e0d-ae4d-b4431f2d73de
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

# Locale-invariant only: ligature text, ARIA roles, hrefs. aria-labels are
# recorded so we know what NOT to anchor on (AGENTS.md locale-invariance rule).
_INVENTORY_JS = r"""() => {
  const tally = (items) => {
    const m = new Map();
    for (const k of items) m.set(k, (m.get(k) || 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]).map(([k, n]) => ({k, n}));
  };
  const LIG = '.google-symbols, .material-symbols-outlined, .material-icons, mat-icon';
  const ligEls = [...document.querySelectorAll(LIG)];
  const isLig = (t) => /^[a-z0-9_]{2,40}$/.test(t);
  return {
    url: location.href,
    path: location.pathname,
    title: document.title,
    ligatures: tally(ligEls.map(e => (e.textContent || '').trim()).filter(isLig)),
    roles: tally([...document.querySelectorAll('[role]')].map(e => e.getAttribute('role'))),
    hrefs: tally([...document.querySelectorAll('a[href]')]
      .map(a => a.getAttribute('href'))
      .filter(h => h && !h.startsWith('http') && h.length < 120)),
    aria_labels: tally([...document.querySelectorAll('[aria-label]')]
      .map(e => e.getAttribute('aria-label')).filter(Boolean)).slice(0, 60),
    textboxes: document.querySelectorAll(
      'div[role="textbox"], textarea, [contenteditable="true"]').length,
    buttons: document.querySelectorAll('button').length,
    body_head: (document.body.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 500),
  };
}"""

# Anything whose ligature or href smells like a cast/character/person surface.
_CAST_PROBE_JS = r"""() => {
  const LIG = '.google-symbols, .material-symbols-outlined, .material-icons, mat-icon';
  const WORDS = /(cast|character|person|face|actor|people|account_box|theater)/i;
  const out = [];
  for (const el of document.querySelectorAll(LIG)) {
    const t = (el.textContent || '').trim();
    if (!WORDS.test(t)) continue;
    const btn = el.closest('button, a, [role="button"], [role="tab"]');
    out.push({
      ligature: t,
      carrier: el.tagName.toLowerCase(),
      clickable: !!btn,
      tag: btn ? btn.tagName.toLowerCase() : null,
      href: btn ? btn.getAttribute('href') : null,
      aria: btn ? btn.getAttribute('aria-label') : null,
    });
  }
  for (const a of document.querySelectorAll('a[href]')) {
    const h = a.getAttribute('href') || '';
    if (WORDS.test(h)) out.push({ligature: null, href: h, tag: 'a', clickable: true,
                                 aria: a.getAttribute('aria-label')});
  }
  return out;
}"""


def _rpcids(url: str) -> list[str]:
    """Pull the ``rpcids=`` query values out of a batchexecute URL."""
    if "batchexecute" not in url or "rpcids=" not in url:
        return []
    raw = url.split("rpcids=", 1)[1].split("&", 1)[0]
    return [p for p in raw.split("%2C") if p]


async def _main(profile: str, project: str, entity: str | None) -> int:
    profile_dir = resolve_profile_dir(profile)
    step("profile", f"{profile} -> {profile_dir}")

    findings: dict[str, Any] = {"profile": profile, "project": project}
    seen_rpcs: list[str] = []

    async with build_client(profile_dir) as client:
        context = client._context  # noqa: SLF001 - spike reads the live context
        page = await context.new_page()

        def _on_request(req: Any) -> None:
            for rid in _rpcids(req.url):
                if rid not in seen_rpcs:
                    seen_rpcs.append(rid)

        page.on("request", _on_request)

        async def _settle() -> None:
            try:
                await page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:  # noqa: BLE001 - settle is best-effort
                pass
            await page.wait_for_timeout(2000)

        try:
            # --- 1. the project page, DIRECT (no labs bounce) ----------------
            direct = f"{_MIGRATED_ROOT}/project/{project}"
            step("goto", f"DIRECT {direct}")
            await page.goto(direct, wait_until="domcontentloaded", timeout=45_000)
            await _settle()
            step("landed", page.url)
            findings["project_page"] = await page.evaluate(_INVENTORY_JS)
            findings["project_page"]["cast_candidates"] = await page.evaluate(_CAST_PROBE_JS)
            shot = default_out_path("migrated_project_direct", ".png")
            await page.screenshot(path=str(shot))
            findings["project_page"]["screenshot"] = shot.name
            step(
                "project",
                f"textboxes={findings['project_page']['textboxes']} "
                f"buttons={findings['project_page']['buttons']} "
                f"cast_candidates={len(findings['project_page']['cast_candidates'])}",
            )

            # --- 2. the character route gflow currently asks for -------------
            if entity:
                char_url = f"{_MIGRATED_ROOT}/project/{project}/character/{entity}"
                step("goto", f"DIRECT {char_url}")
                await page.goto(char_url, wait_until="domcontentloaded", timeout=45_000)
                await _settle()
                step("landed", page.url)
                findings["character_route"] = await page.evaluate(_INVENTORY_JS)
                findings["character_route"]["redirected"] = entity not in page.url
                shot2 = default_out_path("migrated_character_route", ".png")
                await page.screenshot(path=str(shot2))
                findings["character_route"]["screenshot"] = shot2.name
                step(
                    "character_route",
                    f"path={findings['character_route']['path']} "
                    f"textboxes={findings['character_route']['textboxes']} "
                    f"redirected={findings['character_route']['redirected']}",
                )

            findings["batchexecute_rpcids"] = seen_rpcs
            step("rpcs", ", ".join(seen_rpcs) or "(none seen)")

        finally:
            out = default_out_path("migrated_character_surface")
            out.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
            step("wrote", str(out))

    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="ci-probe")
    ap.add_argument("--project", required=True)
    ap.add_argument("--entity", default=None, help="existing entity id to probe the route with")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.profile, args.project, args.entity)))
