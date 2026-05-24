"""Capture the DOM of Flow's image-mode Add Media popover on `ffroliva`.

For issue #56: confirms whether the Radix popover that opens after clicking
the editor's `add_2` button in image mode contains an intermediate menu
(upload-from-device / select-from-library) before the OS file chooser, or
goes straight to a file input like the video R2V flow does on @svasakorn's
account.

Reuses production helpers (`_enter_editor`, `_dismiss_blocking_overlays`,
`_switch_to_image_mode`) via `FlowApiClient` so the editor entry mirrors
the exact production sequence — no selector drift.

Zero credits: we never click Generate. We enter the editor, switch to
image mode, click the editor's Add Media button, dump the popover HTML,
and quit.

Usage:
    .venv/Scripts/python.exe scripts/dev/capture_image_add_media_dom.py \\
        [--profile ffroliva] [--out-html tmp/image_add_media_dom.html]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from gflow_cli.api.client import FlowApiClient
from gflow_cli.paths import default_home, profile_subdir

# Editor-level Add Media — svasakorn's snapshot shows aria-haspopup='dialog'
# (Radix popover) with the same add_2 icon, distinct from the project-level
# "+" because it controls a radix-* popover id.
EDITOR_ADD_MEDIA_BUTTON = (
    "button[aria-haspopup='dialog'][aria-controls^='radix-']:has(i.google-symbols:text('add_2'))"
)


async def capture(profile_name: str, out_html: Path, out_json: Path) -> None:
    profile_dir = profile_subdir(default_home(), profile_name)
    print(f"Profile dir: {profile_dir}")
    if not profile_dir.exists():
        sys.exit(f"Profile dir does not exist: {profile_dir}")

    async with FlowApiClient(profile_dir=profile_dir, headless=False) as client:
        transport = client.transport
        if transport is None:
            sys.exit("FlowApiClient.transport is None")
        page = await client._checkout_page()
        print("FlowApiClient ready, page checked out.")

        # Reuse production sequence: enter editor (clicks +, waits for URL),
        # dismiss any What's-new/changelog overlay, switch to image mode.
        print("Entering editor (production _enter_editor)...")
        await transport._enter_editor(page, None)  # type: ignore[attr-defined]
        print("Dismissing blocking overlays...")
        await transport._dismiss_blocking_overlays(page, None)  # type: ignore[attr-defined]
        print("Switching to image mode (production _switch_to_image_mode)...")
        await transport._switch_to_image_mode(page, out_dir=None)  # type: ignore[attr-defined]

        print("Locating editor Add Media button (aria-haspopup=dialog add_2)...")
        add_media = page.locator(EDITOR_ADD_MEDIA_BUTTON).first
        await add_media.wait_for(state="visible", timeout=15000)

        button_meta = await add_media.evaluate(
            """(el) => ({
                outerHTML: el.outerHTML,
                ariaHaspopup: el.getAttribute('aria-haspopup'),
                ariaControls: el.getAttribute('aria-controls'),
                ariaExpanded: el.getAttribute('aria-expanded'),
                ariaLabel: el.getAttribute('aria-label'),
                dataState: el.getAttribute('data-state'),
                srOnlyText: el.querySelector('[style*=\"sr-only\"], .sr-only')?.innerText,
            })"""
        )
        radix_id = button_meta["ariaControls"]
        print(f"Add Media button aria-controls: {radix_id}")
        print(f"Add Media a11y label / sr-only: {button_meta['srOnlyText']}")

        # Count file inputs BEFORE click — for the delta.
        file_inputs_before = await page.evaluate(
            "() => document.querySelectorAll('input[type=\"file\"]').length"
        )

        print("Clicking Add Media (no upload — just to surface the popover)...")
        await add_media.click()
        print(f"Waiting for popover #{radix_id} to enter data-state='open'...")
        await page.wait_for_function(
            "(id) => document.getElementById(id)?.getAttribute('data-state') === 'open'",
            arg=radix_id,
            timeout=10000,
        )
        print("Popover open — sampling 1s for late-render content...")
        await page.wait_for_timeout(1000)

        capture_data = await page.evaluate(
            """(args) => {
                const [radixId, before] = args;
                const popover = document.getElementById(radixId);
                const openDialogs = Array.from(document.querySelectorAll(
                    '[role=\"dialog\"][data-state=\"open\"], [data-state=\"open\"][role=\"dialog\"]'
                )).map(el => el.outerHTML.slice(0, 2500));
                const fileInputs = Array.from(document.querySelectorAll('input[type=\"file\"]'));
                const fileInputsAfter = fileInputs.length;
                // List buttons / clickable elements inside the popover — the
                // candidates for the intermediate menu hypothesis.
                const popoverButtons = popover ? Array.from(
                    popover.querySelectorAll('button, [role=\"menuitem\"], [role=\"option\"]')
                ).map(el => ({
                    tag: el.tagName,
                    role: el.getAttribute('role'),
                    text: (el.innerText || '').trim().slice(0, 100),
                    ariaLabel: el.getAttribute('aria-label'),
                    dataTestid: el.getAttribute('data-testid'),
                })) : [];
                return {
                    popoverOuterHTML: popover?.outerHTML ?? null,
                    popoverDataState: popover?.getAttribute('data-state') ?? null,
                    popoverRole: popover?.getAttribute('role') ?? null,
                    popoverInnerTextPreview: (popover?.innerText || '').slice(0, 500),
                    openDialogsCount: openDialogs.length,
                    openDialogsOuterHTML: openDialogs,
                    fileInputsBefore: before,
                    fileInputsAfter,
                    fileInputDelta: fileInputsAfter - before,
                    fileInputAccept: fileInputs.map(i => i.getAttribute('accept')),
                    popoverButtons,
                };
            }""",
            arg=[radix_id, file_inputs_before],
        )

        out_html.parent.mkdir(parents=True, exist_ok=True)
        out_html.write_text(
            f"<!-- Add Media button (ffroliva, en-US, Chrome strategy) -->\n"
            f"{button_meta['outerHTML'] or ''}\n\n"
            f"<!-- Popover after click (id={radix_id}, "
            f"data-state={capture_data['popoverDataState']}, "
            f"role={capture_data['popoverRole']}) -->\n"
            f"{capture_data['popoverOuterHTML'] or '(popover element not found)'}\n\n"
            f"<!-- file inputs delta: {capture_data['fileInputDelta']} "
            f"(before {capture_data['fileInputsBefore']}, "
            f"after {capture_data['fileInputsAfter']}) -->\n",
            encoding="utf-8",
        )
        out_json.write_text(
            json.dumps(
                {"button_meta": button_meta, "capture": capture_data},
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nWrote {out_html} ({out_html.stat().st_size} bytes)")
        print(f"Wrote {out_json} ({out_json.stat().st_size} bytes)")
        print(
            f"\nFile inputs delta: {capture_data['fileInputDelta']} "
            f"(before={capture_data['fileInputsBefore']}, "
            f"after={capture_data['fileInputsAfter']})"
        )
        print(f"Popover buttons/menuitems: {len(capture_data['popoverButtons'])}")
        for b in capture_data["popoverButtons"][:10]:
            print(f"  - [{b['tag']} role={b['role']}] {b['text']!r}")
        print(
            "\nInterpretation: file-input delta == 0 + visible buttons in popover "
            "= intermediate-menu variant (ffroliva hypothesis confirmed). "
            "Delta > 0 + no menu items = direct-chooser variant (svasakorn variant)."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="ffroliva")
    parser.add_argument("--out-html", default="tmp/image_add_media_dom.html", type=Path)
    parser.add_argument("--out-json", default="tmp/image_add_media_dom.json", type=Path)
    args = parser.parse_args()
    asyncio.run(capture(args.profile, args.out_html, args.out_json))


if __name__ == "__main__":
    main()
