"""Recon: what the migrated ``flow.google.com`` editor renders for R2V (Ingredients).

Zero credits — it selects a sub-mode, opens whatever picker it finds, and reads. Nothing
is typed into the composer and nothing is submitted.

**The question this exists to answer.** ``run_video`` refuses everything but T2V on the
migrated host, and its reason is explicit: *"i2v/r2v attach media through labs-shaped
slots that have not been recon'd on this host."* One half is already known — the
2026-09-05 wire-protocol spike captured the settings pane's sub-mode row
(``crop_free`` Frames / ``chrome_extension`` Ingredients), so selecting Ingredients needs
no new machinery. The other half, **how a reference is attached once Ingredients is
selected**, has never been captured. On labs it is a picker dialog driven by
``_attach_r2v_references`` → ``VIDEO_SUBMODE_SELECTORS`` → an include button; whether the
migrated host renders anything of that shape is exactly what is unknown.

Guessing here is not cheap: a wrong selector fails a run *after* it has spent credits, so
this dumps the surface instead. It reports, for the editor with Ingredients selected:
every Material Symbols ligature on the page, every button with its text, any dialog or
file input, and the same again after clicking each plausible add-media affordance.

    uv run python scripts/dev/capture_migrated_r2v_surface.py <profile> <project-id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    COMPOSER,
    MigratedComposer,
)

from _spike_common import build_client, resolve_profile_dir  # noqa: E402, isort: skip

#: Ligatures worth clicking blind: on labs the add-media affordance is an icon button.
ADD_MEDIA_LIGATURES = ("add", "add_photo_alternate", "attach_file", "upload", "image", "movie")


async def _surface(page: Any) -> dict[str, Any]:
    """Everything structural that is currently on the page, as plain data."""
    return await page.evaluate(
        """() => {
            const txt = (e) => (e.textContent || '').trim().slice(0, 80);
            return {
                ligatures: [...new Set([...document.querySelectorAll('mat-icon')]
                    .map(txt).filter(Boolean))],
                buttons: [...document.querySelectorAll('button')]
                    .filter(b => b.offsetParent !== null)
                    .map(b => ({
                        text: txt(b),
                        aria: (b.getAttribute('aria-label') || '').slice(0, 60),
                    }))
                    .slice(0, 60),
                dialogs: [...document.querySelectorAll('[role=dialog], mat-dialog-container')]
                    .map(txt),
                file_inputs: document.querySelectorAll('input[type=file]').length,
                overlays: document.querySelectorAll('.cdk-overlay-pane').length,
                composer_present: !!document.querySelector("[contenteditable='true']"),
            };
        }"""
    )


async def _probe(page: Any, project_id: str) -> dict[str, Any]:
    composer = MigratedComposer()
    await composer.ensure_editor(page, project_id)

    report: dict[str, Any] = {"url": page.url}
    report["before_submode"] = await _surface(page)

    # Ingredients is a radio in the settings pane, exactly like mode/aspect/count.
    pane = await composer._open_pane(page)  # noqa: SLF001 — dev instrument
    try:
        await composer._select(page, pane, axis="mode", lig="videocam")  # noqa: SLF001
        await composer._select(page, pane, axis="submode", lig="chrome_extension")  # noqa: SLF001
        report["submode_selected"] = "chrome_extension (Ingredients)"
    except Exception as exc:  # noqa: BLE001 — the failure IS a result
        report["submode_selected"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    await composer._close_pane(page, strict=False)  # noqa: SLF001

    report["after_submode"] = await _surface(page)

    # Whatever the add-media affordance is, it should be new after the switch.
    before = {(b["text"], b["aria"]) for b in report["before_submode"]["buttons"]}
    after = [b for b in report["after_submode"]["buttons"] if (b["text"], b["aria"]) not in before]
    report["buttons_new_after_submode"] = after
    report["ligatures_new_after_submode"] = sorted(
        set(report["after_submode"]["ligatures"]) - set(report["before_submode"]["ligatures"])
    )

    # Click each candidate ligature and record what it opens. Free — a picker is not a
    # submit, and Escape closes it.
    opened: list[dict[str, Any]] = []
    for lig in ADD_MEDIA_LIGATURES:
        button = page.locator("button").filter(has=page.locator("mat-icon", has_text=lig))
        try:
            count = await button.count()
        except Exception:  # noqa: BLE001
            continue
        if not count:
            continue
        try:
            await button.first.click(timeout=3000)
            await page.wait_for_timeout(1200)
            after_click = await _surface(page)
            opened.append(
                {
                    "ligature": lig,
                    "dialogs": after_click["dialogs"],
                    "file_inputs": after_click["file_inputs"],
                    "overlays": after_click["overlays"],
                    "buttons": [b["text"] for b in after_click["buttons"] if b["text"]][:25],
                }
            )
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(400)
        except Exception as exc:  # noqa: BLE001 — observation only
            opened.append({"ligature": lig, "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
    report["add_media_candidates"] = opened

    # Phase 2: the `add` overlay is the attach entry point (it carries "Upload"). Dump its
    # interior — that structure is what an implementation has to drive.
    add_button = page.locator("button").filter(has=page.locator("mat-icon", has_text="add"))
    if await add_button.count():
        await add_button.first.click(timeout=3000)
        await page.wait_for_timeout(1500)
        report["add_overlay"] = await page.evaluate(
            """() => {
                const pane = [...document.querySelectorAll('.cdk-overlay-pane')]
                    .filter(p => p.offsetParent !== null).pop();
                if (!pane) return {found: false};
                const walk = (el, d) => {
                    if (d > 4) return null;
                    const kids = [...el.children].map(c => walk(c, d + 1)).filter(Boolean);
                    const own = (el.textContent || '').trim().slice(0, 60);
                    const role = el.getAttribute('role');
                    const cls = (el.className || '').toString().slice(0, 50);
                    if (!kids.length && !own && !role) return null;
                    return {tag: el.tagName.toLowerCase(), role, cls, text: own,
                            children: kids.slice(0, 12)};
                };
                return {
                    found: true,
                    tabs: [...pane.querySelectorAll('[role=tab], [role=radio]')]
                        .map(e => (e.textContent || '').trim().slice(0, 40)),
                    buttons: [...pane.querySelectorAll('button')]
                        .map(b => ({text: (b.textContent || '').trim().slice(0, 40),
                                    aria: (b.getAttribute('aria-label') || '').slice(0, 40)})),
                    file_inputs: pane.querySelectorAll('input[type=file]').length,
                    inputs: [...pane.querySelectorAll('input, textarea')]
                        .map(i => i.getAttribute('placeholder') || i.type),
                    tree: walk(pane, 0),
                };
            }"""
        )
        await page.keyboard.press("Escape")
    # Phase 3: the add-menu turned out to be the LIBRARY (upload / collection / character
    # / scene), not a composer slot, and Ingredients adds no visible control. On a
    # ProseMirror composer the remaining candidate is an `@` mention, which is also how
    # gflow already names characters on labs. Type one and see what opens.
    try:
        await page.locator(COMPOSER).first.click(timeout=5000)
        await page.keyboard.insert_text("@")
        await page.wait_for_timeout(1500)
        report["at_mention"] = await page.evaluate(
            """() => {
                const panes = [...document.querySelectorAll('.cdk-overlay-pane')]
                    .filter(p => p.offsetParent !== null);
                const pane = panes.pop();
                return {
                    overlays_visible: panes.length + (pane ? 1 : 0),
                    menu_text: pane ? (pane.textContent || '').trim().slice(0, 400) : null,
                    items: pane ? [...pane.querySelectorAll('[role=menuitem], [role=option], li')]
                        .map(e => (e.textContent || '').trim().slice(0, 50)).slice(0, 20) : [],
                    composer_text: (document.querySelector("[contenteditable='true']")
                        || {}).textContent || '',
                };
            }"""
        )
        await page.keyboard.press("Escape")
    except Exception as exc:  # noqa: BLE001 — observation only
        report["at_mention"] = f"{type(exc).__name__}: {str(exc)[:200]}"

    # Phase 4: the `@` picker's real structure. Phase 3 proved the picker opens; driving
    # it blind then failed (the click did not insert a mention and no "Add to prompt"
    # button matched), so dump what the elements actually ARE before guessing again.
    try:
        await page.locator(COMPOSER).first.click(timeout=5000)
        await page.keyboard.insert_text("@")
        await page.wait_for_timeout(1500)
        report["picker_structure"] = await page.evaluate(
            """() => {
                const pane = [...document.querySelectorAll('.cdk-overlay-pane')]
                    .filter(p => p.offsetParent !== null).pop();
                if (!pane) return {found: false};
                const desc = (e) => ({
                    tag: e.tagName.toLowerCase(),
                    role: e.getAttribute('role'),
                    cls: (e.className || '').toString().slice(0, 60),
                    text: (e.textContent || '').trim().slice(0, 50),
                    disabled: e.hasAttribute('disabled') || e.getAttribute('aria-disabled'),
                });
                return {
                    found: true,
                    all_clickables: [...pane.querySelectorAll(
                        'button, [role=menuitem], [role=option], [role=tab], [role=radio], a')]
                        .map(desc).slice(0, 40),
                    add_to_prompt: [...pane.querySelectorAll('*')]
                        .filter(e => /add to prompt/i.test((e.textContent || '').trim())
                                     && e.children.length === 0)
                        .map(desc),
                    grid_items: [...pane.querySelectorAll('[class*=item], [class*=card], [class*=tile]')]
                        .map(desc).slice(0, 15),
                };
            }"""
        )
        await page.keyboard.press("Escape")
    except Exception as exc:  # noqa: BLE001 — observation only
        report["picker_structure"] = f"{type(exc).__name__}: {str(exc)[:200]}"

    report["note"] = "NOTHING SUBMITTED — no credits spent"
    return report


async def _main(profile: str, project_id: str) -> int:
    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 — dev instrument
        assert page is not None
        report = await _probe(page, project_id)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile")
    parser.add_argument("project_id")
    args = parser.parse_args()
    return asyncio.run(_main(args.profile, args.project_id))


if __name__ == "__main__":
    raise SystemExit(main())
