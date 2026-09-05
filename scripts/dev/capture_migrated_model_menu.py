"""Read the migrated host's model picker — what the button says, what the menu offers.

Zero credits: it navigates, opens the settings pane, opens the model menu, reads text,
presses Escape. Nothing is typed into the composer and nothing is submitted.

**The question this exists to answer.** ``veo_3_1_lite_lower_priority`` has no captured
menu label — the 2026-08-14 two-account capability matrix, #650's duration capture and
v0.61.0's refusal A/B all recorded a picker MISS — so both drivers match it by the
``[Lower Priority]`` tag alone. A live run on 2026-09-05 then selected it on the migrated
host in 151 ms with no ``migrated.model_selected`` event, which means ``_select_model``
returned at its *button read-back* rather than opening the menu. Two states produce that
timeline and the log cannot tell them apart:

1. the picker already showed the lower-priority tier (the account's default), so the
   short-circuit was correct and the tier really was bound; or
2. the button text matched the tag for some other reason and the run generated on
   whatever tier was already selected.

Only the button's actual text and the menu's actual entries separate them, and both are
free to read. This prints them verbatim, plus which entries each shipped matcher in
``VIDEO_MODEL_MENU_MATCHERS`` claims — so a matcher that hits zero or two entries is
visible here instead of at submit time.

    uv run python scripts/dev/capture_migrated_model_menu.py <profile> <project-id>
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
    MENU_ITEM,
    OVERLAY,
    VIDEO_MODEL_MENU_MATCHERS,
    MigratedComposer,
    _ligature,
)

from _spike_common import build_client, resolve_profile_dir  # noqa: E402, isort: skip


async def _probe(page: Any, project_id: str) -> dict[str, Any]:
    composer = MigratedComposer()
    await composer.ensure_editor(page, project_id)
    pane = await composer._open_pane(page)  # noqa: SLF001 — dev instrument

    button = pane.locator("button").filter(has=_ligature(page, "arrow_drop_down")).first
    button_count = await button.count()
    button_text = (await button.text_content() or "").strip() if button_count else None

    entries: list[str] = []
    if button_count:
        await button.click(timeout=4000)
        items = page.locator(MENU_ITEM)
        try:
            await items.first.wait_for(state="visible", timeout=5000)
            entries = [t.strip() for t in await items.all_text_contents()]
        except Exception as exc:  # noqa: BLE001 — observation only
            entries = [f"<menu did not open: {type(exc).__name__}>"]
        await page.keyboard.press("Escape")
    await composer._close_pane(page)  # noqa: SLF001 — the trigger TOGGLES; leaving the
    # pane open makes the next _open_pane close it and report "0 option groups"

    return {
        "url": page.url,
        "model_button_found": bool(button_count),
        "model_button_text": button_text,
        "menu_entries": entries,
        "matches": {
            model.value: {
                "button_readback_would_short_circuit": (
                    bool(button_text) and matcher.matches(button_text or "")
                ),
                "menu_hits": [e for e in entries if matcher.matches(e)],
            }
            for model, matcher in VIDEO_MODEL_MENU_MATCHERS.items()
        },
    }


async def _drive(page: Any, project_id: str, restore_to: str | None) -> list[dict[str, Any]]:
    """Run the shipped ``_select_model`` for every tier and read the button back.

    Still zero credits: selection happens entirely before submit, which is what made
    v0.61.0's and v0.62.1's refusal matrices free, and nothing here types or submits.
    The read-back is the point — it is the only evidence that separates "the driver
    clicked the entry the user asked for" from "the driver returned success".
    """
    composer = MigratedComposer()
    rows: list[dict[str, Any]] = []
    for model in VIDEO_MODEL_MENU_MATCHERS:
        # A fresh document per tier. Re-opening the settings pane on one page load
        # leaves a detached overlay that still contains radiogroups, and _open_pane's
        # `.last` then resolves to it and reports "0 option groups" — reproduced here
        # twice. Production opens the pane once per run, so this is the probe adapting
        # to the driver, not a defect it can hit; noted rather than assumed either way.
        await page.goto("about:blank", wait_until="commit", timeout=10_000)
        await composer.ensure_editor(page, project_id)
        pane = await composer._open_pane(page)  # noqa: SLF001 — dev instrument
        outcome: str
        try:
            await composer._select_model(page, pane, model)  # noqa: SLF001 — dev instrument
            outcome = "selected"
        except Exception as exc:  # noqa: BLE001 — the refusal IS a result here
            outcome = f"{type(exc).__name__}: {str(exc)[:160]}"
        # Same `pane` locator the driver reuses after a menu switch: it filters on
        # RADIOGROUP, so it can never resolve to the detached menu overlay.
        button = pane.locator("button").filter(has=_ligature(page, "arrow_drop_down")).first
        read_back = (await button.text_content() or "").strip() if await button.count() else None
        await composer._close_pane(page)  # noqa: SLF001 — dev instrument
        rows.append({"requested": model.value, "outcome": outcome, "button_after": read_back})
    if restore_to:
        rows.append({"note": f"project left on whatever tier was last selected (was {restore_to})"})
    return rows


async def _settings(page: Any, project_id: str, model_value: str) -> dict[str, Any]:
    """Run the REAL `apply_video_settings` + `send_prompt` for *model_value*, then stop.

    Zero credits: submit is never clicked. This is the whole failing path from a field
    report — "switch the model and the next run dies, repeat the same model and it
    works" — which `--drive` cannot see, because that only calls `_select_model` and
    never touches the axes that follow a switch.
    """
    from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel

    composer = MigratedComposer()
    await page.goto("about:blank", wait_until="commit", timeout=10_000)
    await composer.ensure_editor(page, project_id)
    request = GenerateVideoRequest(
        prompt="a man crying",
        mode=Mode.T2V,
        aspect=Aspect.PORTRAIT,
        model=VideoModel.from_cli(model_value),
    )
    row: dict[str, Any] = {"requested": model_value}
    try:
        await composer.apply_video_settings(page, request)
        row["settings"] = "applied"
    except Exception as exc:  # noqa: BLE001 — the failure IS the result
        row["settings"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        return row
    # Recorded on the shipped path, after `_close_pane` has run: a visible overlay
    # here is one that would cover the composer.
    row["visible_overlays_after_settings"] = await page.locator(f"{OVERLAY}:visible").count()

    try:
        await composer.send_prompt(page, request.prompt)
        row["prompt"] = "typed"
    except Exception as exc:  # noqa: BLE001 — the failure IS the result
        row["prompt"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    row["note"] = "STOPPED BEFORE SUBMIT — no credits spent"
    return row


async def _main(profile: str, project_id: str, drive: bool, settings: list[str]) -> int:
    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 — dev instrument
        assert page is not None
        report = await _probe(page, project_id)
        if drive:
            report["drive"] = await _drive(page, project_id, report.get("model_button_text"))
        if settings:
            report["settings_runs"] = [await _settings(page, project_id, m) for m in settings]
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile")
    parser.add_argument("project_id")
    parser.add_argument(
        "--drive",
        action="store_true",
        help="also run _select_model for every tier and read the button back (still $0)",
    )
    parser.add_argument(
        "--settings",
        action="append",
        default=[],
        metavar="MODEL",
        help="run the full apply_video_settings + send_prompt for MODEL, stopping before "
        "submit (still $0). Repeatable — pass it twice to replay a model switch.",
    )
    args = parser.parse_args()
    return asyncio.run(_main(args.profile, args.project_id, args.drive, args.settings))


if __name__ == "__main__":
    raise SystemExit(main())
