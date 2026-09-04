r"""What does the migrated flow.google.com editor actually expose to a driver? ($0)

The goal on the table is to make flow.google.com the frontend gflow drives.
Memory already says a migrated driver is "a new driver, not a selector patch"
(ligatures moved <i> -> <mat-icon>, 0 [role='tab'], 0 [role='menu'], translated
aria-labels). That was a dead-end note. This spike turns it into a scoped
inventory so a driver can be PLANNED against facts instead of a vibe:

* every Material Symbols ligature text and its carrier tag -- ligatures are
  locale-invariant, which is the one anchor class AGENTS.md permits
* every ARIA role and its count -- the structural anchors
* every aria-label -- recorded so we know exactly what NOT to anchor on
  (these translate); the run is on a pt-BR/en-GB account for that reason
* the composer: is there a textarea / contenteditable, and what ligature sits
  on the generate control
* the settings trigger: does it open, and what the opened container contains

Runs on `ffroliva` because that account IS migrated (5/5, 7/7). A labs.google
account cannot answer this.

Credit-free: navigation and DOM reads only. Nothing is typed, nothing submitted.

    python scripts/dev/spike_migrated_editor_dom_inventory.py --profile ffroliva
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gflow_cli.api.routes import EDITOR_BOOTSTRAP_URL  # noqa: E402

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

# A project on this account, taken from the 2026-09-03 flip captures. The
# migrated app serves /project/<id> with no locale segment.
DEFAULT_PROJECT = "c5550ed7-7b6e-43db-8cd3-4d56a74b1244"

_INVENTORY_JS = r"""() => {
  const count = (sel) => document.querySelectorAll(sel).length;
  const tally = (items) => {
    const m = new Map();
    for (const k of items) m.set(k, (m.get(k) || 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]).map(([k, n]) => ({k, n}));
  };
  const LIG = '.google-symbols, .material-symbols-outlined, .material-icons, mat-icon';
  const ligText = (e) => (e.textContent || '').trim();
  const isLig = (t) => /^[a-z0-9_]{2,40}$/.test(t);
  const ligEls = [...document.querySelectorAll(LIG)];
  const ligatures = tally(ligEls.map(ligText).filter(isLig));
  const carriers = tally(ligEls.map(e => e.tagName.toLowerCase()));
  const roles = tally([...document.querySelectorAll('[role]')]
    .map(e => e.getAttribute('role')));
  const ariaLabels = tally([...document.querySelectorAll('[aria-label]')]
    .map(e => (e.getAttribute('aria-label') || '').trim().slice(0, 60)).filter(Boolean));
  const btnLig = (b) => { const l = b.querySelector(LIG); return l ? ligText(l) : ''; };
  const buttons = [...document.querySelectorAll('button')];
  const buttonsWithLig = buttons.map(btnLig).filter(Boolean);
  // The two unknowns that decide a driver's shape: how you submit, and how
  // you pick a model. Recorded structurally. (Angular buttons default to
  // type=submit, so `type` is NOT a discriminator -- ligature only.)
  const SUBMIT = /^(arrow_forward|send|auto_awesome|play_arrow|arrow_upward)$/;
  const submit_candidates = buttons.filter(b => SUBMIT.test(btnLig(b))).map(b => ({
    lig: btnLig(b), type: b.type, disabled: b.disabled, inForm: !!b.closest('form'),
    aria: (b.getAttribute('aria-label') || '').slice(0, 40),
  })).slice(0, 8);
  const composer = [...document.querySelectorAll('textarea, [contenteditable="true"]')]
    .map(e => ({
      tag: e.tagName.toLowerCase(), role: e.getAttribute('role'),
      inForm: !!e.closest('form'),
      placeholderish: (e.getAttribute('placeholder') || e.getAttribute('aria-label') || '')
        .slice(0, 40),
    })).slice(0, 4);
  const PICK = 'mat-select, [role="combobox"], button:has(mat-icon)';
  const model_picker = [...document.querySelectorAll(PICK)].filter(e =>
    ligText(e.querySelector('mat-icon') || e) === 'arrow_drop_down'
    || e.tagName.toLowerCase() === 'mat-select' || e.getAttribute('role') === 'combobox'
  ).map(e => ({
    tag: e.tagName.toLowerCase(), role: e.getAttribute('role'),
    text: (e.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 50),
    haspopup: e.getAttribute('aria-haspopup'),
  })).slice(0, 6);
  return {
    host: location.host,
    path: location.pathname,
    html_lang: document.documentElement.getAttribute('lang'),
    title: document.title.slice(0, 80),
    counts: {
      i_google_symbols: count('i.google-symbols'),
      any_google_symbols: count('.google-symbols'),
      mat_icon: count('mat-icon'),
      role_tab: count("[role='tab']"),
      role_menu: count("[role='menu']"),
      role_listbox: count("[role='listbox']"),
      role_radiogroup: count("[role='radiogroup']"),
      role_dialog: count("[role='dialog']"),
      textarea: count('textarea'),
      contenteditable: count('[contenteditable="true"]'),
      buttons: buttons.length,
      settings_trigger: count('button[aria-label="Settings trigger"], .settings-trigger-button'),
    },
    ligature_carriers: carriers,
    ligatures: ligatures.slice(0, 60),
    button_ligatures: tally(buttonsWithLig).slice(0, 40),
    roles,
    aria_labels: ariaLabels.slice(0, 50),
    submit_candidates, composer, model_picker,
  };
}"""

_OPENED_JS = r"""() => {
  const tally = (items) => {
    const m = new Map();
    for (const k of items) m.set(k, (m.get(k) || 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]).map(([k, n]) => ({k, n}));
  };
  const txt = (e) => (e.textContent || '').replace(/\s+/g, ' ').trim();
  const isLig = (t) => /^[a-z0-9_]{2,40}$/.test(t);
  // Overlays live at body level in Angular Material (cdk-overlay), not under
  // the trigger that opened them.
  const pane = document.querySelector('.cdk-overlay-container') || document.body;
  const q = (sel) => [...pane.querySelectorAll(sel)];
  return {
    overlay_present: !!document.querySelector('.cdk-overlay-container .cdk-overlay-pane'),
    roles_in_overlay: tally(q('[role]').map(e => e.getAttribute('role'))),
    ligatures_in_overlay: tally(q('.google-symbols, mat-icon').map(txt).filter(isLig)),
    aria_in_overlay: tally(q('[aria-label]')
      .map(e => (e.getAttribute('aria-label') || '').trim().slice(0, 60))
      .filter(Boolean)).slice(0, 40),
    radiogroups: q("[role='radiogroup']").map(g =>
      [...g.querySelectorAll("[role='radio']")].map(r => txt(r).slice(0, 24))
    ).slice(0, 8),
    model_picker_in_overlay: q('mat-select, [role="combobox"], button').filter(e =>
      txt(e.querySelector('mat-icon') || e) === 'arrow_drop_down'
    ).map(e => ({tag: e.tagName.toLowerCase(), text: txt(e).slice(0, 50)})).slice(0, 4),
    option_texts: q("[role='option'], [role='radio'], mat-option, mat-radio-button")
      .map(e => txt(e).slice(0, 50)).slice(0, 40),
  };
}"""


async def _inventory(page: Any, label: str) -> dict[str, Any]:
    data = await page.evaluate(_INVENTORY_JS)
    c = data["counts"]
    step(
        label,
        f"{data['host']}{data['path']}  lang={data['html_lang']}  "
        f"mat-icon={c['mat_icon']} i.gs={c['i_google_symbols']} tab={c['role_tab']} "
        f"menu={c['role_menu']} listbox={c['role_listbox']} textarea={c['textarea']} "
        f"settings_trigger={c['settings_trigger']}",
    )
    return data


async def _main(profile: str, project: str) -> int:
    profile_dir = resolve_profile_dir(profile)
    step("profile", f"{profile} -> {profile_dir}")

    async with build_client(profile_dir) as client:
        context = client._context  # noqa: SLF001 - spike reads the live context
        page = await context.new_page()
        try:
            # Go via labs.google exactly as gflow does; the app hands off itself.
            await page.goto(
                f"{EDITOR_BOOTSTRAP_URL.split('?')[0]}/project/{project}",
                wait_until="domcontentloaded",
            )
            await page.wait_for_url(lambda u: "flow.google.com" in u, timeout=20_000)
            step("handoff", page.url)
            # Settle on the network rather than on a guessed element -- the first
            # run waited 20 s for a <button> that never came and learned nothing.
            try:
                await page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:  # noqa: BLE001 - settle is best-effort
                pass
            await page.wait_for_timeout(2500)
            shot = default_out_path("migrated_editor", ".png")
            await page.screenshot(path=str(shot), full_page=False)
            body_head = await page.evaluate(
                r"() => (document.body.innerText || '').replace(/\s+/g,' ').trim().slice(0, 400)"
            )
            step("state", f"screenshot={shot.name}  body='{body_head[:120]}'")
            editor = await _inventory(page, "editor")
            editor["body_text_head"] = body_head
            editor["screenshot"] = shot.name

            opened: dict[str, Any] = {"attempted": False}
            trig = page.locator(
                'button[aria-label="Settings trigger"], .settings-trigger-button'
            ).first
            if await trig.count():
                opened["attempted"] = True
                await trig.click(timeout=5000)
                await page.wait_for_timeout(1200)
                opened.update(await page.evaluate(_OPENED_JS))
                step(
                    "settings",
                    f"overlay={opened.get('overlay_present')}  "
                    f"roles={[r['k'] for r in opened.get('roles_in_overlay', [])][:8]}",
                )
                await page.keyboard.press("Escape")
        finally:
            await page.close()

    payload = {
        "profile": profile,
        "project": project[:8] + "...",
        "note": "credit-free: navigation + DOM reads only; nothing typed or submitted",
        "purpose": "inventory the migrated editor so a flow.google.com driver is scoped on facts",
        "editor": editor,
        "settings_opened": opened,
    }
    out = default_out_path("migrated_editor_dom_inventory")
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    step("out", str(out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="ffroliva")
    ap.add_argument("--project", default=DEFAULT_PROJECT)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.profile, args.project)))
