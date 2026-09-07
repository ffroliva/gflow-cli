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

**Two controls, and both fail AFTER the dump, never before it.**  A selector sweep that
returns zero everywhere is worthless unless something *known present* also resolved in
the same run — otherwise "nothing matched" and "the probe never reached the editor" are
the same observation.  So the script records (1) that the editor-ready anchor resolves
and (2) that the typed text actually landed in the box — the second is the load-bearing
one, because a click that hits a consent overlay leaves the box empty, the Format button
disabled, and produces a false absence indistinguishable from a real measurement.

Both raise, but only from a ``finally`` that has already written the capture and the
screenshots.  ``skills/spike/SKILL.md``: *never put a guard in front of a probe* — a
fail-fast that runs before the evidence is collected deletes the evidence that would
correct it, which is exactly how #701 made a false claim unfalsifiable.

FREE — navigation, a free tRPC ``createEntity``, DOM reads and typing.  Nothing is
submitted, nothing is generated, no credit and no image quota is spent, and a scratch
entity the spike minted is deleted again on the way out.

Usage:
    uv run python scripts/dev/spike_character_prompt_format.py --project <id> [--entity <id>]

Without ``--entity`` a fresh (free) scratch entity is minted: a character that already
has images renders a saved view with no prompt composer and no button.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from pathlib import Path
from typing import Any, cast

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import default_out_path  # noqa: E402, isort: skip

from gflow_cli._cli_helpers import _make_provider_dir, _resolve_profile  # noqa: E402
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
    # `default_out_path` anchors on the repo ROOT. Hand-rolling `Path("./scripts/dev/…")`
    # is CWD-relative, and `.gitignore`'s `scripts/dev/_spike_out/` entry has a mid-pattern
    # slash, so it anchors to the root too: run from `scripts/dev/` and the capture lands
    # in an UNIGNORED `scripts/dev/scripts/dev/_spike_out/`, carrying the profile name, the
    # live URL, entity ids and authenticated `outer_html` ([[git-add-all-sweeps-scratch-files]]).
    dump = default_out_path(f"char_format_anchor_{Path(resolved_profile).name}")
    out_dir = dump.parent

    report: dict[str, Any] = {
        "captured_at": dump.stem,
        "profile": resolved_profile,
        "project_id": project_id,
    }
    control_error: str | None = None

    # FlowApiClient acquires the profile lease before Chrome starts — never launch a
    # browser on a profile this process does not own (skills/spike/SKILL.md).
    async with FlowApiClient(profile_dir=profile_dir, out_dir=out_dir) as client:
        scratch_entity: str | None = None
        if entity_id is None:
            # A character that already has images renders a saved-character view with
            # no prompt composer — and no Format button. The composer (and the button)
            # only exist on an entity with empty slots, which is the state the saga
            # navigates into. create_entity is FREE (tRPC, no credit, no generation).
            entity_id = scratch_entity = await client.create_entity(project_id)
            logger.info("created_scratch_entity", entity_id=entity_id, project_id=project_id)
        report["entity_id"] = entity_id

        transport = cast("UiAutomationTransport", client.transport)
        page = await client._checkout_page()

        try:
            await transport._enter_character_editor(
                page,
                project_id=project_id,
                entity_id=entity_id,
                locale="en-US",
            )
            report["url"] = page.url
            logger.info("page_state", url=page.url, title=await page.title())

            # CONTROL — recorded before anything is counted, but NOT raised on here.
            # `skills/spike/SKILL.md`: never put a guard in front of a probe. A fail-fast
            # that runs before the evidence is collected deletes the evidence that would
            # correct it — that is the #701 failure this whole file exists to prevent. So
            # the verdict is deferred to the `finally`, after the dump is on disk.
            control_count = await page.locator(_CONTROL_SELECTOR).count()
            report["control_selector"] = _CONTROL_SELECTOR
            report["control_count"] = control_count
            logger.info("control_check", selector=_CONTROL_SELECTOR, count=control_count)
            if control_count == 0:
                control_error = (
                    f"CONTROL MISSED: no prompt box on {page.url} — this run measures "
                    "nothing. Do not read its zeros as absence."
                )

            report["sweep_empty"] = await _sweep(page, "empty")
            await page.screenshot(path=str(out_dir / f"{dump.stem}_empty.png"))

            # Both sides of the transition: the button ships `disabled` on an empty box,
            # so the empty read is what proves the enabled state that follows was caused
            # by the typing.
            box = page.locator(_CONTROL_SELECTOR).first
            await box.click()
            await page.keyboard.insert_text("a woman with short silver hair, studio portrait")
            await page.wait_for_timeout(1500)

            # SECOND CONTROL, and the load-bearing one. If that click landed on a consent
            # overlay instead of the box, the text never arrives, the button stays disabled
            # and `sweep_typed` records a FALSE ABSENCE that looks exactly like a real
            # measurement. Recorded, and raised on in the `finally` — never before the dump.
            typed_text = (await box.inner_text()).strip()
            report["typed_control_text"] = typed_text[:80]
            logger.info("typed_control", chars=len(typed_text))
            if not typed_text and control_error is None:
                control_error = (
                    "TYPED CONTROL MISSED: the prompt box is still empty after click + "
                    "insert_text — the enabled/disabled reads below measure nothing."
                )

            report["ligatures_typed"] = await page.evaluate(_LIGATURE_DUMP_JS)
            report["sweep_typed"] = await _sweep(page, "typed")
            report["composer_buttons"] = await page.evaluate(
                _COMPOSER_BUTTON_DUMP_JS, _CONTROL_SELECTOR
            )
            await page.screenshot(path=str(out_dir / f"{dump.stem}_typed.png"))

            for lig in report["ligatures_typed"]:
                logger.info("ligature", **{k: ascii(v) for k, v in lig.items()})
        finally:
            # Evidence first, verdict second. Whatever DID render is itself the finding.
            with contextlib.suppress(Exception):
                await page.screenshot(path=str(out_dir / f"{dump.stem}_final.png"))
            # Return the page BEFORE any `client.<verb>()` below. The pool holds one
            # page and `_checkout_page()` waits forever on an empty queue, so a client
            # call made while this script still holds the page deadlocks in silence —
            # measured 2026-09-07: the capture and all three screenshots were on disk
            # and the process never exited, holding the profile lease. `suppress` does
            # not help; a block is not an exception.
            # Pinned by tests/scripts/test_spike_page_pool.py.
            client._checkin_page(page)
            dump.write_text(json.dumps(report, indent=2), encoding="utf-8")
            logger.info(
                "report_written",
                path=str(dump),
                ligatures=len(report.get("ligatures_typed") or []),
                composer_buttons=len(report.get("composer_buttons") or []),
            )
            if scratch_entity is not None:
                # Delete what the spike created (skills/spike/SKILL.md). FREE.
                with contextlib.suppress(Exception):
                    await client.delete_characters(project_id, [scratch_entity])
                    logger.info("deleted_scratch_entity", entity_id=scratch_entity)

    if control_error is not None:
        raise RuntimeError(control_error)


def main() -> None:
    parser = argparse.ArgumentParser(description="Spike: find the character-editor Format button")
    parser.add_argument("--project", required=True, help="Flow project id")
    parser.add_argument("--entity", default=None, help="Character entity id (default: fresh)")
    parser.add_argument("--profile", default=None, help="Profile for the live Playwright session")
    args = parser.parse_args()

    asyncio.run(run_spike(args.profile, args.project, args.entity))


if __name__ == "__main__":
    main()
