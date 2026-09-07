"""Spike: read the character-editor "Format" button's anchor off live DOM (#383, #727).

Dumps every icon-ligature carrier in the character editor with its host button, so the
Format button's locale-stable anchor is measured instead of guessed — on **either**
frontend. labs.google renders ligatures in ``<i class="google-symbols">``; the migrated
``flow.google.com`` Angular frontend renders the same ligatures in ``<mat-icon>``.

Two runs behind this file:

* **2026-07-27 (labs)** — confirmed the ligature is ``personal_recommendations``, that
  the button carries no ``aria-label`` (the label is a ``<span>`` child), and that it
  ships ``disabled`` while the prompt box is empty.  See ``PROMPT_FORMAT_SELECTORS``.
* **2026-09-07 (migrated, #727)** — ``PROMPT_FORMAT_SELECTORS`` stopped matching and
  ``character create --format-prompt`` degraded to a silent no-op.  This probe was
  rewritten to be carrier-agnostic, to type into the prompt box (the button is disabled
  until then, so an empty-box read measures the wrong state), and to carry a **control**.

The control is the point.  A selector sweep that returns zero everywhere is worthless
unless something *known present* also resolved in the same run — otherwise "nothing
matched" and "the probe never reached the editor" are the same observation.  This script
hard-errors if the editor-ready anchor misses, so a flat zero can never be read as
absence.

FREE — navigation, a free tRPC ``createEntity``, DOM reads and typing.  Nothing is
submitted, nothing is generated, no credit and no image quota is spent.

Usage:
    uv run python scripts/dev/spike_character_prompt_format.py --project <id> [--entity <id>]

Without ``--entity`` a fresh (free) scratch entity is minted: a character that already
has images renders a saved view with no prompt composer and no button.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import structlog

from gflow_cli._cli_helpers import _make_provider_dir, _resolve_profile
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports.ui_automation import (
    PROMPT_FORMAT_SELECTORS,
    UiAutomationTransport,
)

logger = structlog.get_logger("spike_character_prompt_format")

# Editor-mounted anchor, both frontends (Slate on labs, ProseMirror on migrated).
# This is the CONTROL: if it misses, the probe reached no editor and every other
# count in this run is meaningless.
_CONTROL_SELECTOR = (
    'div[role="textbox"][data-slate-editor="true"], div.ProseMirror[contenteditable="true"]'
)

# Every icon ligature in the document, whichever carrier renders it, with the button
# hosting it. The Format button's ligature is whichever entry sits on a button whose
# label/tooltip reads "Format" (or its localised equivalent) — that ligature, paired
# with its carrier tag, is the durable anchor.
_LIGATURE_DUMP_JS = """() => Array.from(
    document.querySelectorAll('i.google-symbols, span.google-symbols, mat-icon')
).map(el => {
    const host = el.closest('button,[role=button]');
    return {
        ligature: (el.textContent || '').trim(),
        carrier_tag: el.tagName.toLowerCase(),
        carrier_class: el.getAttribute('class'),
        host_tag: host ? host.tagName.toLowerCase() : null,
        host_custom_parent: host && host.parentElement
            ? host.parentElement.tagName.toLowerCase() : null,
        host_label: host?.getAttribute('aria-label') || null,
        host_title: host?.getAttribute('title') || null,
        host_disabled: host ? (host.hasAttribute('disabled')
            || host.getAttribute('aria-disabled') === 'true') : null,
        host_text: (host?.innerText || '').trim().slice(0, 60),
    };
})"""

# Every button in the composer subtree (the prompt box's nearest common container),
# ligature-carrying or not — so a Format control that dropped its icon entirely still
# shows up. Walks up five levels from the prompt box; that is enough to clear the
# toolbar row on both frontends without swallowing the whole page.
_COMPOSER_BUTTON_DUMP_JS = """(sel) => {
    const box = document.querySelector(sel);
    if (!box) return null;
    let root = box;
    for (let i = 0; i < 5 && root.parentElement; i++) root = root.parentElement;
    return Array.from(root.querySelectorAll('button,[role=button]')).map(b => ({
        tag: b.tagName.toLowerCase(),
        parent_tag: b.parentElement ? b.parentElement.tagName.toLowerCase() : null,
        aria_label: b.getAttribute('aria-label'),
        title: b.getAttribute('title'),
        classes: b.getAttribute('class'),
        disabled: b.hasAttribute('disabled') || b.getAttribute('aria-disabled') === 'true',
        text: (b.innerText || '').trim().slice(0, 60),
        child_tags: Array.from(b.children).map(c => c.tagName.toLowerCase()),
    }));
}"""

# Does anything sit on top of the element? Present + visible + not clickable is a
# distinct failure from absent, and only elementFromPoint tells them apart.
_OCCLUSION_JS = """(sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return {
        rect: {x: r.x, y: r.y, w: r.width, h: r.height},
        hit_tag: hit ? hit.tagName.toLowerCase() : null,
        hit_is_self_or_child: hit ? (el === hit || el.contains(hit)) : false,
    };
}"""


async def _sweep(page: Any, phase: str) -> list[dict[str, Any]]:
    """Run the shipped cascade plus an EN-text locator and log every count."""
    rows: list[dict[str, Any]] = []
    # The EN-text entry is NOT a candidate selector — it is here only to locate the
    # button so its structure can be read off. Flow localises that label, so display
    # text can never be the anchor (locale-invariance rule, AGENTS.md).
    for sel in (*PROMPT_FORMAT_SELECTORS, 'button:has-text("Format")'):
        loc = page.locator(sel)
        count = await loc.count()
        row: dict[str, Any] = {"phase": phase, "selector": sel, "count": count}
        if count:
            row["visible"] = await loc.first.is_visible()
            row["enabled"] = await loc.first.is_enabled()
            row["outer_html"] = (await loc.first.evaluate("el => el.outerHTML"))[:400]
        logger.info("selector_check", **row)
        rows.append(row)
    return rows


async def run_spike(profile_name: str | None, project_id: str, entity_id: str | None) -> None:
    resolved_profile = _resolve_profile(profile_name)
    profile_dir = _make_provider_dir(resolved_profile)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_dir = Path("./scripts/dev/_spike_out")
    out_dir.mkdir(parents=True, exist_ok=True)

    # FlowApiClient acquires the profile lease before Chrome starts — never launch a
    # browser on a profile this process does not own (skills/spike/SKILL.md).
    async with FlowApiClient(profile_dir=profile_dir, out_dir=out_dir) as client:
        if entity_id is None:
            # A character that already has images renders a saved-character view with
            # no prompt composer — and no Format button. The composer (and the button)
            # only exist on an entity with empty slots, which is the state the saga
            # navigates into. create_entity is FREE (tRPC, no credit, no generation).
            entity_id = await client.create_entity(project_id)
            logger.info("created_scratch_entity", entity_id=entity_id, project_id=project_id)

        transport = cast("UiAutomationTransport", client.transport)
        page = await client._checkout_page()

        await transport._enter_character_editor(
            page,
            project_id=project_id,
            entity_id=entity_id,
            locale="en-US",
        )
        logger.info("page_state", url=page.url, title=await page.title())

        # CONTROL — established before anything is counted. A zero here means the
        # probe never reached the editor, not that the editor lacks a Format button.
        control_count = await page.locator(_CONTROL_SELECTOR).count()
        logger.info("control_check", selector=_CONTROL_SELECTOR, count=control_count)
        if control_count == 0:
            msg = (
                "CONTROL MISSED: no prompt box on "
                f"{page.url} — this run measures nothing. Do not read its zeros as absence."
            )
            raise RuntimeError(msg)

        report: dict[str, Any] = {
            "captured_at": stamp,
            "profile": resolved_profile,
            "url": page.url,
            "project_id": project_id,
            "entity_id": entity_id,
            "control_selector": _CONTROL_SELECTOR,
            "control_count": control_count,
        }

        # Both sides of the transition. The button ships `disabled` on an empty box,
        # so an empty-box read measures the wrong state — but the empty read is what
        # proves the enabled state that follows is caused by the typing.
        report["ligatures_empty"] = await page.evaluate(_LIGATURE_DUMP_JS)
        report["sweep_empty"] = await _sweep(page, "empty")
        await page.screenshot(path=str(out_dir / f"char_format_empty_{stamp}.png"))

        box = page.locator(_CONTROL_SELECTOR).first
        await box.click()
        await page.keyboard.insert_text("a woman with short silver hair, studio portrait")
        await page.wait_for_timeout(1500)

        report["ligatures_typed"] = await page.evaluate(_LIGATURE_DUMP_JS)
        report["sweep_typed"] = await _sweep(page, "typed")
        report["composer_buttons"] = await page.evaluate(
            _COMPOSER_BUTTON_DUMP_JS, _CONTROL_SELECTOR
        )
        report["control_occlusion"] = await page.evaluate(_OCCLUSION_JS, _CONTROL_SELECTOR)
        await page.screenshot(path=str(out_dir / f"char_format_typed_{stamp}.png"))

        for lig in report["ligatures_typed"]:
            logger.info("ligature", **{k: ascii(v) for k, v in lig.items()})

        dump = out_dir / f"char_format_anchor_{resolved_profile}_{stamp}.json"
        dump.write_text(json.dumps(report, indent=2), encoding="utf-8")
        logger.info(
            "report_written",
            path=str(dump),
            ligatures=len(report["ligatures_typed"]),
            composer_buttons=len(report["composer_buttons"] or []),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Spike: find the character-editor Format button")
    parser.add_argument("--project", required=True, help="Flow project id")
    parser.add_argument("--entity", default=None, help="Character entity id (default: fresh)")
    parser.add_argument("--profile", default=None, help="Profile for the live Playwright session")
    args = parser.parse_args()

    asyncio.run(run_spike(args.profile, args.project, args.entity))


if __name__ == "__main__":
    main()
