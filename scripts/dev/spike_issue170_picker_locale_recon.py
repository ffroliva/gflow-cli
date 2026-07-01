#!/usr/bin/env python3
r"""Issue #170 spike — Tier 1 (0 credits): picker include-action DOM recon.

Answers the two recon questions that blocked the locale-free selector fix
(issue #170, shipped in v0.16.0 via PR #173).
Ligature icons are locale-invariant, so a pt-BR account answers both:

  Q1. Right-click context menu on a Personagens entity tile — what container
      does it render in ([role='menu']? data-state? portal parent chain) and
      what ligature does the include item carry ('add' expected, per the ru
      report on issue #170)?
  Q2. The Vozes-flow include button (PICKER_INCLUDE_BUTTON) — does it carry a
      ligature icon / structural anchor, or is localized text its only handle?

No generation request is ever fired: the spike opens the resource picker,
right-clicks a tile, dumps DOM, and exits. Nothing is submitted.

Usage (headed, supervised):

    ! .venv\Scripts\python.exe scripts\dev\spike_issue170_picker_locale_recon.py \
        --profile denon82 --project 580a6bbf-d433-4153-80b9-1842b5a560ea

Outputs (gitignored): scripts/dev/_spike_out/spike_issue170_picker_locale_recon_<ts>.{json,png}
"""

from __future__ import annotations

import argparse
import asyncio
import json
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
    PICKER_VOZES_TAB,
    VideoGenerationMixin,
)

_ENTITY_TILE = "[data-tile-id^='fe_id_']"

# Dump every visible menu / menuitem plus the portal parent chain — enough to
# derive a scoped Tier-1 selector without guessing the container shape.
_DUMP_MENUS_JS = """
() => {
  const ICON = "i.google-symbols,.google-symbols," +
    ".material-symbols-outlined,.material-symbols-rounded";
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const lig = (el) => { const i = el.querySelector(ICON); return i ? i.textContent.trim() : null; };
  const desc = (el) => ({
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute("role"),
    dataState: el.getAttribute("data-state"),
    ariaLabel: el.getAttribute("aria-label"),
    ligature: lig(el),
    text: (el.textContent || "").trim().slice(0, 80),
  });
  const chain = (el) => {
    const out = []; let p = el.parentElement;
    for (let i = 0; i < 5 && p; i++) { out.push(desc(p)); p = p.parentElement; }
    return out;
  };
  const menus = [...document.querySelectorAll("[role='menu']")].filter(vis).map((m) => ({
    container: desc(m),
    parentChain: chain(m),
    items: [...m.querySelectorAll("[role='menuitem'],button")].filter(vis).map(desc),
  }));
  const orphanItems = [...document.querySelectorAll("[role='menuitem']")]
    .filter(vis).filter((mi) => !mi.closest("[role='menu']")).map((mi) => ({
      ...desc(mi), parentChain: chain(mi),
    }));
  return { menus, orphanItems };
}
"""

# Dump every visible button (ligature + text) so the include button's anchor —
# icon, aria, or position — is identifiable even if its caption is localized.
_DUMP_BUTTONS_JS = """
() => {
  const ICON = "i.google-symbols,.google-symbols," +
    ".material-symbols-outlined,.material-symbols-rounded";
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const lig = (el) => { const i = el.querySelector(ICON); return i ? i.textContent.trim() : null; };
  return [...document.querySelectorAll("button,[role='option']")].filter(vis).map((el) => ({
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute("role"),
    ariaLabel: el.getAttribute("aria-label"),
    ligature: lig(el),
    text: (el.textContent || "").trim().slice(0, 60),
    dialogScoped: !!el.closest("[role='dialog']"),
  }));
}
"""


async def _open_picker(page: Any) -> None:
    add = page.locator(ADD_MEDIA_BUTTON).first
    await add.wait_for(state="visible", timeout=8000)
    await add.click()
    await page.wait_for_timeout(900)


async def _run(*, profile_dir: Path, project_id: str, locale: str, out_path: Path) -> int:
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "spike": "issue170-picker-locale-recon",
        "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project": project_id,
        "locale": locale,
    }

    async with build_client(profile_dir, headless=False) as client:
        page = await client._checkout_page()  # noqa: SLF001
        try:
            url = routes.project_editor_url(locale, project_id)
            step("1", f"goto {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(4_000)
            await page.keyboard.press("Escape")
            await VideoGenerationMixin._exit_agent_mode(page)  # noqa: SLF001
            await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)  # noqa: SLF001
            await VideoGenerationMixin._switch_video_sub_mode(page, "references", out_dir=None)  # noqa: SLF001
            await page.wait_for_timeout(800)

            # ---- Q1: Personagens right-click context menu -------------------
            step("2", "open picker -> Personagens -> right-click first entity tile")
            await _open_picker(page)
            ptab = page.locator(PICKER_PERSONAGENS_TAB).first
            await ptab.wait_for(state="visible", timeout=8000)
            await ptab.click()
            await page.wait_for_timeout(900)
            tile = page.locator(_ENTITY_TILE).first
            await tile.wait_for(state="visible", timeout=8000)
            result["entityTileId"] = await tile.get_attribute("data-tile-id")
            await tile.scroll_into_view_if_needed(timeout=8000)
            await tile.click(button="right")
            await page.wait_for_timeout(700)
            result["contextMenu"] = await page.evaluate(_DUMP_MENUS_JS)
            await page.screenshot(path=str(out_dir / "Q1_context_menu.png"))
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(400)

            # ---- Q2: Vozes include button -----------------------------------
            # The Escape above may have closed the whole picker (not just the
            # context menu) — reopen it if the Vozes tab is gone. Q2 failures
            # are recorded, never fatal: Q1 findings must survive to the JSON.
            step("3", "Vozes tab -> select first option -> dump buttons")
            try:
                vtab = page.locator(PICKER_VOZES_TAB).first
                if not await vtab.is_visible():
                    await _open_picker(page)
                    vtab = page.locator(PICKER_VOZES_TAB).first
                await vtab.wait_for(state="visible", timeout=8000)
                await vtab.click()
                await page.wait_for_timeout(900)
                result["vozesButtonsBeforeSelect"] = await page.evaluate(_DUMP_BUTTONS_JS)
                opt = page.locator("[role='option']").first
                await opt.wait_for(state="visible", timeout=5000)
                await opt.click()
                await page.wait_for_timeout(700)
                result["vozesButtonsAfterSelect"] = await page.evaluate(_DUMP_BUTTONS_JS)
                await page.screenshot(path=str(out_dir / "Q2_vozes_include.png"))
            except Exception as e:  # noqa: BLE001
                result["vozesError"] = f"{type(e).__name__}: {e}"
                await page.screenshot(path=str(out_dir / "Q2_vozes_FAILED.png"))
            await page.keyboard.press("Escape")
        finally:
            client._checkin_page(page)  # noqa: SLF001

    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- compact findings ----------------------------------------------------
    print(f"\n[recon] full dump: {out_path}")
    for menu in result.get("contextMenu", {}).get("menus", []):
        c = menu["container"]
        print(
            f"[recon] Q1 menu container: <{c['tag']} role={c['role']} "
            f"data-state={c['dataState']}> items:"
        )
        for it in menu["items"]:
            print(f"[recon]   - role={it['role']} ligature={it['ligature']!r} text={it['text']!r}")
    if not result.get("contextMenu", {}).get("menus"):
        print("[recon] Q1: NO [role='menu'] container found — see orphanItems in the dump")
        for it in result.get("contextMenu", {}).get("orphanItems", []):
            print(f"[recon]   orphan role=menuitem ligature={it['ligature']!r} text={it['text']!r}")
    include_like = [
        b
        for b in result.get("vozesButtonsAfterSelect", result.get("vozesButtonsBeforeSelect", []))
        if "Incluir" in (b.get("text") or "")
    ]
    if include_like:
        for b in include_like:
            print(
                f"[recon] Q2 include button: ligature={b['ligature']!r} "
                f"aria-label={b['ariaLabel']!r} text={b['text']!r} dialog={b['dialogScoped']}"
            )
    else:
        print("[recon] Q2: no 'Incluir' button visible — inspect vozesButtons* in the dump")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--locale", default="pt")
    args = ap.parse_args()
    profile_dir = resolve_profile_dir(args.profile)
    out_path = default_out_path("spike_issue170_picker_locale_recon", ".json")
    return asyncio.run(
        _run(
            profile_dir=profile_dir,
            project_id=args.project,
            locale=args.locale,
            out_path=out_path,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
