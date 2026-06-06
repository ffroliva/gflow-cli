#!/usr/bin/env python3
r"""Movie-consistency spike — Tier 1 (0 credits): resource-picker SELECTION recon.

Follow-up to spike_movie_entity_recon.py. That spike proved the picker tabs +
the "Incluir no comando" button exist. This spike answers the live e2e blocker
(_attach_character_entities timed out clicking the include button at
ui_automation_video.py:1183): once a character tile is *selected*, does the
include button actually appear, and which selector reliably picks the CHARACTER
tile (not a same-named image tile)?

Findings to capture (all credit-free — no generation fires):
  - State A: picker just opened (default tab) — tiles + include-button presence.
  - State B: after search.fill('<name>') — which role=option tiles match, with
    their name label / type label / aria-selected.
  - State C: after clicking the option whose NAME LABEL == name exactly
    (:text-is) — did aria-selected flip to true? did the include button appear
    + become visible?
  - State D: Personagens-tab grid — is the search box removed? what tiles show?

Usage (headed, supervised):

    ! .venv\Scripts\python.exe scripts\dev\spike_movie_picker_select.py \
        --profile denon82 --project 6ba50219-0fb5-4471-a96e-83257784dfd8 \
        --name Stickman --locale pt

Outputs (gitignored): scripts/dev/_spike_out/spike_movie_picker_select_<ts>.{json,*.png}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.transports.ui_automation_video import (  # noqa: E402
    ADD_MEDIA_BUTTON,
    PICKER_PERSONAGENS_TAB,
    PICKER_SEARCH_INPUT,
    VideoGenerationMixin,
)

# In-page reporter: compact, structured snapshot of the open picker. Returns the
# tile grid (name label / type label / aria-selected / visible) plus include-
# button presence + visibility. Locale-agnostic structure; only the include
# button regex is locale-flavoured (pt: 'comando').
_REPORT_JS = r"""
(() => {
  const norm = (s) => (s || '').trim().replace(/\s+/g, ' ');
  const vis = (el) => !!(el && el.offsetParent !== null);
  const search = document.querySelector('#add-menu-input');
  const tabs = [...document.querySelectorAll("[role='tab']")].map(t => ({
    text: norm(t.innerText || t.textContent),
    selected: t.getAttribute('aria-selected'),
  }));
  const opts = [...document.querySelectorAll("[role='option']")].map((o, i) => {
    const labels = [...o.querySelectorAll('div')]
      .map(d => norm(d.childNodes.length === 1 && d.firstChild && d.firstChild.nodeType === 3
        ? d.textContent : ''))
      .filter(Boolean);
    const img = o.querySelector('img');
    return {
      i,
      selected: o.getAttribute('aria-selected'),
      name: labels[0] || null,
      type: labels[1] || null,
      alt: img ? img.getAttribute('alt') : null,
      visible: vis(o),
    };
  });
  // Include button — locale flavour 'comando' (pt). Also record the dialog's
  // iconless footer buttons as locale-agnostic candidates.
  const allBtns = [...document.querySelectorAll('button')];
  const includeByText = allBtns
    .filter(b => /incluir no comando|add to prompt|comando/i.test(b.textContent || ''))
    .map(b => ({ text: norm(b.innerText || b.textContent), visible: vis(b), disabled: b.disabled }));
  const dialog = document.querySelector("[role='dialog']");
  let footerIconless = [];
  if (dialog) {
    footerIconless = [...dialog.querySelectorAll('button')]
      .filter(b => !b.querySelector('i.google-symbols,i.material-icons-outlined') && norm(b.textContent))
      .map(b => ({ text: norm(b.textContent), visible: vis(b), disabled: b.disabled }));
  }
  return {
    url: location.href,
    searchPresent: !!search,
    searchVisible: vis(search),
    tabs,
    optCount: opts.length,
    selectedCount: opts.filter(o => o.selected === 'true').length,
    opts: opts.slice(0, 40),
    includeByText,
    footerIconless,
  };
})()
"""


# Probe the target card: walk the thumbnail img's ancestors + enumerate candidate
# selection affordances (buttons / checkboxes / icon ligatures) within the card.
_PROBE_CARD_JS = r"""
(thumb) => {
  const norm = (s) => (s || '').trim().replace(/\s+/g, ' ').slice(0, 60);
  const img = document.querySelector(`img[src*='${thumb}']`);
  if (!img) return { found: false };
  const chain = [];
  let el = img, depth = 0;
  while (el && depth < 9) {
    chain.push({
      d: depth,
      tag: el.tagName ? el.tagName.toLowerCase() : String(el.nodeName),
      role: el.getAttribute && el.getAttribute('role'),
      aria: el.getAttribute && el.getAttribute('aria-label'),
      tabindex: el.getAttribute && el.getAttribute('tabindex'),
      cls: el.className && el.className.baseVal !== undefined ? norm(el.className.baseVal) : norm(el.className),
      data: el.attributes ? [...el.attributes].map(a => a.name).filter(n => n.startsWith('data-')).join(',') : '',
    });
    el = el.parentElement; depth++;
  }
  // Nearest ancestor that is a card-ish container (has a label + the img).
  let card = img;
  for (let i = 0; i < 6 && card.parentElement; i++) card = card.parentElement;
  const within = (root) => ({
    buttons: [...root.querySelectorAll('button')].map(b => ({
      aria: norm(b.getAttribute('aria-label')), text: norm(b.textContent),
      icon: norm((b.querySelector('i.google-symbols,i.material-icons-outlined') || {}).textContent),
    })),
    checkboxes: [...root.querySelectorAll("[role='checkbox'],input[type='checkbox']")].length,
    icons: [...new Set([...root.querySelectorAll('i.google-symbols,i.material-icons-outlined')].map(i => norm(i.textContent)).filter(Boolean))],
    selectedAttr: root.querySelector("[aria-selected='true'],[aria-checked='true']") ? true : false,
  });
  return { found: true, chain, card: within(card) };
}
"""


async def _shot(page: Any, out_dir: Path, name: str) -> None:
    try:
        await page.screenshot(path=str(out_dir / f"{name}.png"))
    except Exception as e:  # noqa: BLE001
        step("shot-fail", f"{name}: {e}", prefix="pick")


async def _report(page: Any) -> Any:
    return await page.evaluate(_REPORT_JS)


async def _run(
    *, profile_dir: Path, project_id: str, name: str, thumb: str | None, locale: str, out_path: Path
) -> int:
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "spike": "movie-picker-select",
        "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "projectId": project_id,
        "name": name,
        "locale": locale,
        "states": {},
        "actions": {},
    }

    async with build_client(profile_dir, headless=False) as client:
        page = await client._checkout_page()  # noqa: SLF001
        try:
            url = routes.project_editor_url(locale, project_id)
            step("1", f"goto {url}", prefix="pick")
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(4_000)
            await page.keyboard.press("Escape")

            await VideoGenerationMixin._exit_agent_mode(page)  # noqa: SLF001
            await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)  # noqa: SLF001
            await VideoGenerationMixin._switch_video_sub_mode(page, "references", out_dir=None)  # noqa: SLF001
            await page.wait_for_timeout(800)

            # --- open picker -------------------------------------------------
            btn = page.locator(ADD_MEDIA_BUTTON).first
            await btn.wait_for(state="visible", timeout=8000)
            await btn.click()
            await page.wait_for_timeout(1500)
            await _shot(page, out_dir, "A_picker_open")
            report["states"]["A_open"] = await _report(page)

            # --- State B: search by name ------------------------------------
            search = page.locator(PICKER_SEARCH_INPUT).first
            try:
                await search.wait_for(state="visible", timeout=8000)
                await search.fill(name)
                await page.wait_for_timeout(1200)
                report["actions"]["search_filled"] = True
            except Exception as e:  # noqa: BLE001
                report["actions"]["search_filled"] = f"{type(e).__name__}: {e}"
            await _shot(page, out_dir, "B_searched")
            report["states"]["B_searched"] = await _report(page)

            # --- State C: click the exact-name option -----------------------
            # The character tile is the role=option whose NAME LABEL is exactly
            # `name` (image tiles read e.g. 'Stickman with round head').
            tile = page.locator(
                f"[role='option']:has(div:text-is('{name}'))"
            ).first
            try:
                await tile.wait_for(state="visible", timeout=8000)
                await tile.click()
                await page.wait_for_timeout(900)
                report["actions"]["exact_tile_clicked"] = True
            except Exception as e:  # noqa: BLE001
                report["actions"]["exact_tile_clicked"] = f"{type(e).__name__}: {e}"
            await _shot(page, out_dir, "C_selected")
            report["states"]["C_selected"] = await _report(page)

            # --- State D: Personagens tab (separate picker reopen) -----------
            # Close picker (Escape) and reopen, then click Personagens tab to
            # record whether the search box survives + what the grid shows.
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(600)
            try:
                btn2 = page.locator(ADD_MEDIA_BUTTON).first
                await btn2.wait_for(state="visible", timeout=8000)
                await btn2.click()
                await page.wait_for_timeout(1200)
                ptab = page.locator(PICKER_PERSONAGENS_TAB).first
                await ptab.wait_for(state="visible", timeout=8000)
                await ptab.click()
                await page.wait_for_timeout(1000)
                report["actions"]["personagens_tab_clicked"] = True
            except Exception as e:  # noqa: BLE001
                report["actions"]["personagens_tab_clicked"] = f"{type(e).__name__}: {e}"
            await _shot(page, out_dir, "D_personagens")
            report["states"]["D_personagens"] = await _report(page)

            # Dump the FULL page HTML for the Personagens-tab state (the picker
            # container is NOT [role='dialog'] here, so target it offline).
            try:
                page_html = await page.content()
                (out_dir / "D_personagens_page.html").write_text(page_html, encoding="utf-8")
                report["actions"]["personagens_page_html"] = len(page_html)
            except Exception as e:  # noqa: BLE001
                report["actions"]["personagens_page_html"] = f"{type(e).__name__}: {e}"

            # --- State E: PROBE the target card's structure (NO click — a card
            # click navigates into the editor). Walk the thumbnail img's ancestor
            # chain + list candidate select affordances (buttons/checkboxes) so we
            # can derive the correct Personagens-tab selection gesture.
            if thumb:
                try:
                    probe = await page.evaluate(_PROBE_CARD_JS, thumb)
                    report["states"]["E_card_probe"] = probe
                    report["actions"]["card_probed"] = True
                except Exception as e:  # noqa: BLE001
                    report["actions"]["card_probed"] = f"{type(e).__name__}: {e}"
                # Hover the card (no click) — Flow often reveals a select overlay
                # only on hover. Capture the post-hover structure + a screenshot.
                try:
                    await page.locator(f"img[src*='{thumb}']").first.hover(timeout=4000)
                    await page.wait_for_timeout(500)
                    await _shot(page, out_dir, "E_card_hover")
                    report["states"]["E_card_probe_hover"] = await page.evaluate(
                        _PROBE_CARD_JS, thumb
                    )
                except Exception as e:  # noqa: BLE001
                    report["actions"]["card_hover"] = f"{type(e).__name__}: {e}"
        finally:
            client._checkin_page(page)  # noqa: SLF001

    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[pick] json -> {out_path}", flush=True)
    # Console digest so the key answers surface without re-reading the file.
    for sk, sv in report["states"].items():
        inc = sv.get("includeByText", [])
        print(
            f"[pick] {sk}: search(present={sv.get('searchPresent')},vis={sv.get('searchVisible')}) "
            f"opts={sv.get('optCount')} selected={sv.get('selectedCount')} "
            f"includeBtn={[(b['visible'], b['disabled']) for b in inc]}",
            flush=True,
        )
    print(f"[pick] actions = {report['actions']}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Movie spike: picker SELECTION recon (0 credits).")
    p.add_argument("--profile", default=os.environ.get("GFLOW_CLI_PROFILE", "denon82"))
    p.add_argument("--project", required=True)
    p.add_argument("--name", default="Stickman", help="character display name to select")
    p.add_argument("--thumb", default=None, help="thumbnail_media_id of the target entity (State E)")
    p.add_argument("--locale", default="pt")
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    profile_dir = resolve_profile_dir(args.profile)
    out_path = Path(args.out) if args.out else default_out_path("spike_movie_picker_select", ".json")
    step("--", f"profile={args.profile} project={args.project} name={args.name}", prefix="pick")
    try:
        return asyncio.run(
            _run(
                profile_dir=profile_dir,
                project_id=args.project,
                name=args.name,
                thumb=args.thumb,
                locale=args.locale,
                out_path=out_path,
            )
        )
    except KeyboardInterrupt:
        print("[pick] aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
