"""Spike: does clicking Flow's Format button actually REWRITE the prompt, and when?

#727 fixed the anchor — `format_character_prompt` now finds and clicks the button. It
then waits ~500 ms of jitter, logs `ui_automation.prompt_formatted`, and returns; the
caller submits immediately. That event therefore proves **a click**, never **a rewrite**
([[playwright-click-no-downstream-event-signature]]).

If Flow's rewrite is a server round trip, ~500 ms may be short, and `_send_prompt` would
submit the prompt the user typed while the reshaped text was still in flight — the
`--format-prompt` flag silently doing nothing, again, behind a green exit code and a
green e2e. This spike measures whether that is real:

1. type a known prompt, and record it (CONTROL — a rewrite is only detectable against a
   baseline we know landed);
2. click Format;
3. poll the box on a fixed cadence and record **when** the text changes, if it does;
4. record every request the page makes in that window, so a rewrite that is a server
   call is distinguishable from one that is local.

A text that never changes is only meaningful with the polling timeline beside it: this
script reports the full series, so "no change in N s" is a positive observation with a
shape, not a wait that expired.

**It never submits.** No image is generated, no video, no credit. Not measured: whether
the rewrite call itself draws on any server-side quota — nothing observable here says,
and the e2e has been clicking this button routinely for months.

Usage:
    uv run python scripts/dev/spike_format_prompt_effect.py --project <id> [--entity <id>]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
import time
from pathlib import Path
from typing import Any, cast

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import default_out_path  # noqa: E402, isort: skip

from gflow_cli._cli_helpers import _make_provider_dir, _resolve_profile  # noqa: E402
from gflow_cli.api.client import FlowApiClient  # noqa: E402
from gflow_cli.api.transports.ui_automation import (  # noqa: E402
    PROMPT_FORMAT_SELECTORS,
    UiAutomationTransport,
)

logger = structlog.get_logger("spike_format_prompt_effect")

# Borrowed from the transport, never copied — see spike_character_prompt_format.py.
_CONTROL_SELECTOR = UiAutomationTransport._CHARACTER_EDITOR_READY_SELECTOR

# Deliberately terse and unpolished — a rewrite has something to do, so a no-change
# result cannot be explained away as "the prompt was already well formed".
_SEED_PROMPT = "girl, red hair, sad"

_POLL_INTERVAL_S = 0.25
_POLL_WINDOW_S = 20.0


async def run_spike(profile_name: str | None, project_id: str, entity_id: str | None) -> None:
    resolved_profile = _resolve_profile(profile_name)
    profile_dir = _make_provider_dir(resolved_profile)
    dump = default_out_path(f"format_prompt_effect_{Path(resolved_profile).name}")
    out_dir = dump.parent

    report: dict[str, Any] = {
        "profile": resolved_profile,
        "project_id": project_id,
        "seed_prompt": _SEED_PROMPT,
        "poll_interval_s": _POLL_INTERVAL_S,
        "poll_window_s": _POLL_WINDOW_S,
    }
    control_error: str | None = None

    async with FlowApiClient(profile_dir=profile_dir, out_dir=out_dir) as client:
        scratch_entity: str | None = None
        if entity_id is None:
            entity_id = scratch_entity = await client.create_entity(project_id)
            logger.info("created_scratch_entity", entity_id=entity_id)
        report["entity_id"] = entity_id

        transport = cast("UiAutomationTransport", client.transport)
        page = await client._checkout_page()

        try:
            await transport._enter_character_editor(
                page, project_id=project_id, entity_id=entity_id, locale="en-US"
            )
            report["url"] = page.url

            # Every request the page makes from here on. This is what separates
            # "the rewrite is a server round trip" from "it is local", which is the
            # whole question behind how long the caller must wait.
            requests: list[dict[str, Any]] = []
            t_zero = time.monotonic()

            # One clock for requests AND polls, so the two timelines are directly
            # comparable — the gap between them is the finding.
            def _elapsed() -> float:
                return round(time.monotonic() - t_zero, 3)

            def _on_request(req: Any) -> None:
                requests.append({"t": _elapsed(), "method": req.method, "url": req.url})

            page.on("request", _on_request)

            box = page.locator(_CONTROL_SELECTOR).first
            await box.click()
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Delete")
            await page.keyboard.insert_text(_SEED_PROMPT)
            await page.wait_for_timeout(800)

            # CONTROL — the baseline must be in the box, or a later "unchanged" reading
            # is measuring an empty box, not an absent rewrite.
            before = (await box.inner_text()).strip()
            report["text_before"] = before
            logger.info("baseline", text=before)
            if before != _SEED_PROMPT:
                control_error = (
                    f"CONTROL MISSED: box holds {before!r}, expected {_SEED_PROMPT!r} — "
                    "the seed never landed, so nothing below measures a rewrite."
                )

            # Find the button through the SHIPPED cascade, so this spike also re-tests
            # the #727 anchor every time it runs.
            clicked_via: str | None = None
            for selector in PROMPT_FORMAT_SELECTORS:
                loc = page.locator(selector).first
                if await loc.count() and await loc.is_visible() and await loc.is_enabled():
                    await loc.click()
                    clicked_via = selector
                    break
            report["clicked_via"] = clicked_via
            logger.info("format_clicked", selector=clicked_via)
            if clicked_via is None and control_error is None:
                control_error = (
                    "CONTROL MISSED: no enabled Format button matched the shipped "
                    f"cascade {PROMPT_FORMAT_SELECTORS} — the #727 anchor may have drifted "
                    "again. This run measures nothing about the rewrite."
                )

            click_at = _elapsed()
            report["click_at_s"] = click_at
            timeline: list[dict[str, Any]] = []
            changed_at: float | None = None
            while _elapsed() - click_at < _POLL_WINDOW_S:
                now = (await box.inner_text()).strip()
                elapsed = round(_elapsed() - click_at, 3)
                if not timeline or timeline[-1]["text"] != now:
                    timeline.append({"t": elapsed, "text": now, "chars": len(now)})
                    logger.info("box_text", t=elapsed, chars=len(now), text=now[:70])
                    if changed_at is None and now != before:
                        changed_at = elapsed
                await asyncio.sleep(_POLL_INTERVAL_S)

            report["timeline"] = timeline
            report["changed_at_s"] = changed_at
            report["text_after"] = timeline[-1]["text"]
            report["requests"] = requests
            await page.screenshot(path=str(out_dir / f"{dump.stem}.png"))

            logger.info(
                "verdict",
                changed=changed_at is not None,
                changed_at_s=changed_at,
                # The number that decides the design: gflow currently waits ~0.5 s.
                within_current_wait=(changed_at is not None and changed_at <= 0.5),
                requests_after_click=sum(1 for r in requests if r["t"] >= click_at),
            )
        finally:
            with contextlib.suppress(Exception):
                await page.screenshot(path=str(out_dir / f"{dump.stem}_final.png"))
            # Return the page BEFORE any client.<verb>() — the pool holds one page and
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
    parser = argparse.ArgumentParser(description="Spike: does Format actually rewrite the prompt?")
    parser.add_argument("--project", required=True, help="Flow project id")
    parser.add_argument("--entity", default=None, help="Character entity id (default: fresh)")
    parser.add_argument("--profile", default=None, help="Profile for the live Playwright session")
    args = parser.parse_args()

    asyncio.run(run_spike(args.profile, args.project, args.entity))


if __name__ == "__main__":
    main()
