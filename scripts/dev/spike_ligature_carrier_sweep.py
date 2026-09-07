"""Spike: which `<i>`-only ligature selectors actually miss on the migrated host (#730)?

Two bugs in one week came from the same split: labs renders Material Symbols ligatures in
``<i class="google-symbols">``, the migrated ``flow.google.com`` Angular frontend renders the
SAME ligature in ``<mat-icon class="… google-symbols …">``. A cascade anchored on one carrier
returns a flat zero on the other host while the control is fully visible (#727, #731).

A static sweep says **24 constants** in ``src/gflow_cli`` are ``<i>``-only. It cannot say which
of them matter: a selector whose surface does not exist on this host is not a bug, and one
whose miss is rescued by a text fallback is a bug that is currently invisible —
``SUBMIT_BUTTON_SELECTORS`` is exactly that, observed live 2026-09-07 firing its third entry
``button:has-text('arrow_forward')`` because both ``<i>`` entries missed.

So this measures, per ligature, on a live migrated surface:

* ``i.google-symbols:text-is(L)``   — what we ship today
* ``mat-icon:text-is(L)``           — the twin we would add
* ``.google-symbols:text-is(L)``    — **the hypothesis**: the class is on BOTH carriers, so a
  class-only anchor may cover both hosts with no cascade entry at all. If that holds, the fix
  for 24 constants is one character each rather than 24 duplicated entries.
* ``:text-is(L)`` on any element     — proves the ligature is present at all, so a zero in the
  three above is about the SELECTOR and not about the feature.

That last row is the control, and it is the point: a sweep that reports zeros everywhere is
worthless unless something known-present resolved in the same run.

Probes two surfaces because the constants split across them: the project composer and the
character editor. Both are reported separately — a ligature absent from one may be present in
the other, and collapsing them would manufacture a false absence.

FREE — navigation, one free tRPC ``createEntity`` (deleted again), and DOM reads. Nothing is
submitted, nothing generated, no credit and no image quota.

Usage:
    uv run python scripts/dev/spike_ligature_carrier_sweep.py --project <id> [--profile denon82]
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
from gflow_cli.api.client import FlowApiClient  # noqa: E402
from gflow_cli.api.routes import project_editor_url  # noqa: E402
from gflow_cli.api.transports.ui_automation import UiAutomationTransport  # noqa: E402

logger = structlog.get_logger("spike_ligature_carrier_sweep")

# Every ligature reachable from an `<i>`-only constant, from the static sweep. Kept as a
# literal list rather than re-derived at runtime so the probe measures a FIXED question and
# a later refactor cannot silently shrink what it asks.
_LIGATURES = [
    "accessibility_new",
    "add",
    "add_2",
    "apps_spark_2",
    "arrow_drop_down",
    "arrow_forward",
    "article_spark",
    "cancel",
    "chrome_extension",
    "clear",
    "close",
    "crop_16_9",
    "crop_9_16",
    "crop_free",
    "crop_landscape",
    "crop_original",
    "crop_portrait",
    "crop_square",
    "dashboard",
    "edit_square",
    "image",
    "left_panel_close",
    "play_circle",
    "tune",
    "upload",
    "voice_selection",
]

_CHAR_EDITOR_READY = UiAutomationTransport._CHARACTER_EDITOR_READY_SELECTOR

# Counts every carrier form for one ligature in a single evaluate, so all four numbers
# describe the SAME DOM instant. Four separate Playwright locators would each see a
# slightly different page on a live Angular app.
_SWEEP_JS = """(ligatures) => {
    const exact = (el, lig) => (el.textContent || '').trim() === lig;
    const out = {};
    for (const lig of ligatures) {
        const all = Array.from(
            document.querySelectorAll('i, mat-icon, span, [class*=google-symbols]')
        );
        const hits = all.filter(el => exact(el, lig));
        out[lig] = {
            i_google_symbols: hits.filter(el =>
                el.tagName === 'I' && el.classList.contains('google-symbols')).length,
            mat_icon: hits.filter(el => el.tagName === 'MAT-ICON').length,
            class_only: hits.filter(el => el.classList.contains('google-symbols')).length,
            any_element: hits.length,
            carrier_tags: Array.from(new Set(hits.map(el => el.tagName.toLowerCase()))),
        };
    }
    return out;
}"""


async def _sweep_surface(page: Any, surface: str) -> dict[str, Any]:
    counts = await page.evaluate(_SWEEP_JS, _LIGATURES)
    present = {k: v for k, v in counts.items() if v["any_element"] > 0}
    logger.info(
        "surface_swept",
        surface=surface,
        url=page.url,
        ligatures_present=len(present),
        ligatures_probed=len(_LIGATURES),
    )
    for lig, c in sorted(present.items()):
        logger.info(
            "ligature",
            surface=surface,
            ligature=lig,
            i_google_symbols=c["i_google_symbols"],
            mat_icon=c["mat_icon"],
            class_only=c["class_only"],
            carriers=",".join(c["carrier_tags"]),
        )
    return counts


async def _verify_shipped_anchors(page: Any, surface: str) -> dict[str, Any]:
    """Count the SHIPPED constants, plus the pre-#730 forms they replaced.

    This is the proof half of the spike. The sweep above says what the DOM contains; this
    says whether the code we actually ship resolves against it, and whether the form it
    replaced did not. A fix asserted from a JSON dump is a fix nobody ran.
    """
    from gflow_cli.api.transports.ui_automation import (  # noqa: PLC0415
        IMAGE_MODEL_PICKER_TRIGGER,
        SUBMIT_BUTTON_SELECTORS,
    )

    pairs = {
        "submit": (
            SUBMIT_BUTTON_SELECTORS[0],
            "button:has(i.google-symbols:text('arrow_forward'))",
        ),
        "image_model_picker": (
            IMAGE_MODEL_PICKER_TRIGGER,
            "button[aria-haspopup='menu']:has(i.google-symbols:text-is('arrow_drop_down'))",
        ),
    }
    out: dict[str, Any] = {}
    for name, (shipped, old) in pairs.items():
        now = await page.locator(shipped).count()
        before = await page.locator(old).count()
        out[name] = {"shipped": shipped, "shipped_count": now, "pre_730_count": before}
        logger.info(
            "shipped_anchor",
            surface=surface,
            anchor=name,
            shipped_count=now,
            pre_730_count=before,
            selector=shipped,
        )

    # The de-blinded diagnostics: the artifact an incident bundle would carry. Before #730
    # this returned an empty list on the migrated host, so a bundle from a migrated user
    # named no ligatures at all.
    from gflow_cli.diagnostics import STRUCTURAL_DOM_JS  # noqa: PLC0415

    dom = await page.evaluate(STRUCTURAL_DOM_JS)
    ligatures = dom.get("ligatures") or []
    out["structural_dom_ligature_count"] = dom.get("ligatureCount", 0)
    out["structural_dom_ligatures"] = ligatures[:12]
    out["structural_dom_crop_present"] = dom.get("cropPresent")
    logger.info(
        "structural_dom_capture",
        surface=surface,
        ligature_count=dom.get("ligatureCount", 0),
        distinct=len(ligatures),
        sample=ligatures[:8],
    )
    return out


async def run_spike(profile_name: str | None, project_id: str) -> None:
    resolved_profile = _resolve_profile(profile_name)
    profile_dir = _make_provider_dir(resolved_profile)
    dump = default_out_path(f"ligature_carrier_sweep_{Path(resolved_profile).name}")
    out_dir = dump.parent

    report: dict[str, Any] = {
        "profile": resolved_profile,
        "project_id": project_id,
        "ligatures_probed": _LIGATURES,
    }
    control_error: str | None = None

    async with FlowApiClient(profile_dir=profile_dir, out_dir=out_dir) as client:
        transport = cast("UiAutomationTransport", client.transport)
        # Mint the scratch entity BEFORE taking the page. `create_entity` reaches Flow
        # through `_post_json`, which checks a page out of the pool itself — and the pool
        # holds ONE. Calling it while this script already holds that page is the deadlock
        # that hung a spike for 32 minutes on 2026-09-07. The guard test cannot see this
        # ordering: it only checks that `_checkin_page` appears somewhere in the file.
        scratch_entity: str | None = await client.create_entity(project_id)
        logger.info("created_scratch_entity", entity_id=scratch_entity)

        page = await client._checkout_page()
        try:
            # --- Surface 1: the project composer -------------------------------
            # Navigate straight to the project rather than going through
            # `_enter_editor`, which clicks the gallery's "New project" CTA via
            # NEW_PROJECT_SELECTORS — one of the very constants under test. A probe
            # must not depend on the thing it is measuring: the first run of this
            # spike died at exactly that CTA on the migrated root
            # (`Could not find 'New project' CTA`, 2026-09-07), which is a finding
            # about that selector and NOT a reason to lose both surfaces.
            await page.goto(
                project_editor_url(None, project_id), wait_until="domcontentloaded", timeout=45_000
            )
            await transport._dismiss_blocking_overlays(page, None)  # type: ignore[attr-defined]
            # Wait for the icons themselves, not a fixed interval. A 3s sleep produced a
            # zero-ligature composer once already; the thing being counted is the thing to
            # wait for. Still bounded, and the control below reports the outcome either way.
            with contextlib.suppress(Exception):
                await page.locator(".google-symbols").first.wait_for(
                    state="attached", timeout=20_000
                )
            await page.wait_for_timeout(2000)
            report["composer_url"] = page.url
            report["composer"] = await _sweep_surface(page, "composer")
            report["composer_anchors"] = await _verify_shipped_anchors(page, "composer")
            # PER-SURFACE CONTROL. The composer originally had none, and a run on
            # 2026-09-07 came back with zero ligatures of ANY kind on it — the page had
            # simply not rendered its icons yet at the 3s mark, while an earlier run on the
            # same URL saw 8. Those zeros are "did not arrive", not "does not match", and
            # without this they are indistinguishable from a real absence. Every surface
            # needs its own control; one control on one surface certifies only that surface.
            composer_hits = sum(v["any_element"] for v in report["composer"].values())
            report["control_composer_ligature_hits"] = composer_hits
            logger.info("control_check", surface="composer", ligature_hits=composer_hits)
            if composer_hits == 0:
                control_error = (
                    f"CONTROL MISSED: composer at {page.url} rendered no ligatures at all — "
                    "its counts measure nothing. Do not read its zeros as absence."
                )
            await page.screenshot(path=str(out_dir / f"{dump.stem}_composer.png"))

            # --- Surface 2: the character editor -------------------------------
            await transport._enter_character_editor(
                page, project_id=project_id, entity_id=scratch_entity, locale="en-US"
            )
            await page.wait_for_timeout(2000)
            report["editor_url"] = page.url
            report["editor"] = await _sweep_surface(page, "character_editor")
            report["editor_anchors"] = await _verify_shipped_anchors(page, "character_editor")
            await page.screenshot(path=str(out_dir / f"{dump.stem}_editor.png"))

            # CONTROL — recorded, raised on only after the dump is written. A run where
            # the editor never mounted measures nothing, and its zeros must not be read
            # as absence (skills/spike/SKILL.md: never put a guard in front of a probe).
            boxes = await page.locator(_CHAR_EDITOR_READY).count()
            report["control_prompt_boxes"] = boxes
            total_hits = sum(
                v["any_element"]
                for v in report["editor"].values()  # type: ignore[union-attr]
            )
            report["control_total_ligature_hits"] = total_hits
            logger.info("control_check", prompt_boxes=boxes, ligature_hits=total_hits)
            if boxes == 0 or total_hits == 0:
                control_error = (
                    f"CONTROL MISSED: prompt_boxes={boxes}, ligature_hits={total_hits} on "
                    f"{page.url} — this run measures nothing. Do not read its zeros as absence."
                )
        finally:
            with contextlib.suppress(Exception):
                await page.screenshot(path=str(out_dir / f"{dump.stem}_final.png"))
            # Return the page BEFORE any client.<verb>(): the pool holds one page and
            # `_checkout_page()` waits forever (tests/scripts/test_spike_page_pool.py).
            client._checkin_page(page)
            dump.write_text(json.dumps(report, indent=2), encoding="utf-8")
            logger.info("report_written", path=str(dump))
            if scratch_entity is not None:
                with contextlib.suppress(Exception):
                    await client.delete_characters(project_id, [scratch_entity])
                    logger.info("deleted_scratch_entity", entity_id=scratch_entity)

    if control_error is not None:
        raise RuntimeError(control_error)


def main() -> None:
    parser = argparse.ArgumentParser(description="Spike: ligature carrier sweep (#730)")
    parser.add_argument("--project", required=True, help="Flow project id")
    parser.add_argument("--profile", default=None, help="Profile for the live session")
    args = parser.parse_args()

    asyncio.run(run_spike(args.profile, args.project))


if __name__ == "__main__":
    main()
