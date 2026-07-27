"""Spike: confirm the ligature behind Flow's character-editor "Format" button (#383).

Navigates to a character editor and dumps every ``i.google-symbols`` ligature with
its host button, so the Format button's locale-stable anchor is read off live DOM
instead of guessed.

Ran 2026-07-27 — confirmed ``personal_recommendations``, and found that the button
carries no aria-label and ships ``disabled`` on an empty prompt.  See
``PROMPT_FORMAT_SELECTORS``.  Re-run this whenever the character editor drifts.

FREE — navigation + DOM read only.  Nothing is generated, nothing is clicked.

Usage:
    uv run python scripts/dev/spike_character_prompt_format.py --project <id> [--entity <id>]

Without ``--entity`` a fresh (free) scratch entity is minted: a character that
already has images renders a saved view with no prompt composer and no button.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import cast

import structlog

from gflow_cli._cli_helpers import _make_provider_dir, _resolve_profile
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports.ui_automation import (
    PROMPT_FORMAT_SELECTORS,
    UiAutomationTransport,
)

logger = structlog.get_logger("spike_character_prompt_format")

# Every google-symbols icon in the editor, with the button hosting it. The Format
# button's ligature is whichever entry sits on a button whose label/tooltip reads
# "Format" (or its localised equivalent) — that ligature is the durable anchor.
_LIGATURE_DUMP_JS = """() => Array.from(document.querySelectorAll('i.google-symbols'))
    .map(i => {
        const host = i.closest('button,[role=button]');
        return {
            ligature: (i.textContent || '').trim(),
            host_tag: host?.tagName || null,
            host_label: host?.getAttribute('aria-label') || null,
            host_title: host?.getAttribute('title') || null,
            host_text: (host?.innerText || '').trim().slice(0, 60),
        };
    })"""


async def run_spike(profile_name: str | None, project_id: str, entity_id: str | None) -> None:
    resolved_profile = _resolve_profile(profile_name)
    profile_dir = _make_provider_dir(resolved_profile)
    out_dir = Path("./tmp/spike_character_prompt_format")
    out_dir.mkdir(parents=True, exist_ok=True)

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
        try:
            await transport._enter_character_editor(
                page,
                project_id=project_id,
                entity_id=entity_id,
                locale="en-US",
            )
        except Exception as e:
            # Non-fatal: the readiness gate is not the point of the spike. Dump
            # whatever DID render — that is itself the finding.
            logger.warning("editor_not_ready_dumping_anyway", error=str(e))

        await page.wait_for_timeout(3000)
        logger.info("page_state", url=page.url, title=await page.title())

        # The EN-text entries are NOT candidate selectors — they are here only to
        # locate the button so its ligature can be read off. Flow localises the
        # label ([[flow-locale-leak-icon-ligatures]]), so text can never be the anchor.
        for sel in (*PROMPT_FORMAT_SELECTORS, 'button:has-text("Format")'):
            loc = page.locator(sel)
            count = await loc.count()
            visible = await loc.first.is_visible() if count else False
            logger.info("selector_check", selector=sel, count=count, visible=visible)
            if visible:
                html = await loc.first.evaluate("el => el.outerHTML")
                logger.info("element_outer_html", selector=sel, html=html[:300])

        ligatures = await page.evaluate(_LIGATURE_DUMP_JS)
        logger.info("google_symbols_ligatures", count=len(ligatures))
        dump = out_dir / "ligatures.json"
        dump.write_text(json.dumps(ligatures, indent=2), encoding="utf-8")
        logger.info("ligatures_written", path=str(dump))
        for lig in ligatures:
            logger.info("ligature", **{k: ascii(v) for k, v in lig.items()})

        await page.screenshot(path=str(out_dir / "character_editor.png"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Spike: find the character-editor Format button")
    parser.add_argument("--project", required=True, help="Flow project id")
    parser.add_argument("--entity", default=None, help="Character entity id (default: first)")
    parser.add_argument("--profile", default=None, help="Profile for the live Playwright session")
    args = parser.parse_args()

    asyncio.run(run_spike(args.profile, args.project, args.entity))


if __name__ == "__main__":
    main()
