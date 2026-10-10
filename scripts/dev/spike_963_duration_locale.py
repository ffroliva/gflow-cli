"""#963 — what the duration radios say outside English, and whether ``_duration`` binds them.

Zero credits: opens the settings pane, selects the Omni Flash model (the cohort's only
duration row), reads every radio, runs the shipped ``_select(axis="duration")`` for each
requested length and reads ``aria-checked`` back. Nothing is typed, nothing is submitted.

Two questions, both about the live DOM rather than our fake:

1. Is there a structural anchor (an attribute that carries the length) that would make a
   text match unnecessary? The locale rule in AGENTS.md admits a token match only when
   there is none — so every radio's attributes are printed verbatim.
2. Does the number-token matcher pick the right radio in a translated pane, and never a
   sibling that shares the leading digit?

The UI language is forced per pass with ``?hl=`` plus an ``Accept-Language`` header.
Whether Flow honours them is itself recorded: a pass whose labels are still English says
the override did not take, not that the matcher works in that language.

    uv run python scripts/dev/spike_963_duration_locale.py <profile> <project-id> [--hl vi --hl pt]
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
    COOKIE_BAR,
    RADIO,
    MigratedComposer,
    _duration,
)
from gflow_cli.api.video import VideoModel  # noqa: E402

from _spike_common import build_client, default_out_path, resolve_profile_dir  # noqa: E402, isort: skip

_RADIO_DUMP = """els => els.map(e => ({
    text: (e.textContent || '').trim(),
    checked: e.getAttribute('aria-checked'),
    attrs: Object.fromEntries([...e.attributes].map(a => [a.name, a.value])),
}))"""


async def _pass(page: Any, project_id: str, hl: str, lengths: list[int]) -> dict[str, Any]:
    composer = MigratedComposer()
    await page.set_extra_http_headers({"Accept-Language": hl} if hl else {})
    await page.goto("about:blank", wait_until="commit", timeout=10_000)
    await composer.ensure_editor(page, project_id)
    if hl:
        sep = "&" if "?" in page.url else "?"
        await page.goto(f"{page.url}{sep}hl={hl}", wait_until="domcontentloaded")
        await composer.ensure_editor(page, project_id)
    row: dict[str, Any] = {"hl": hl or "(default)", "url": page.url}
    row["html_lang"] = await page.evaluate("document.documentElement.lang")
    # The forced-language pass met a consent bar whose reject button the driver's selector
    # never resolved (first run, 2026-10-10). Record what that bar offers, verbatim, then
    # clear it here so the duration question can still be answered.
    await page.wait_for_timeout(1500)
    bar = page.locator(COOKIE_BAR).first
    if await bar.is_visible():
        row["cookie_bar_buttons"] = await bar.locator("button, a[role='button']").evaluate_all(
            "els => els.map(e => ({text: (e.textContent||'').trim(), cls: e.className,"
            " visible: !!e.offsetParent}))"
        )
        buttons = bar.locator("button:visible")
        if await buttons.count():
            await buttons.last.click(timeout=3000)
    pane = await composer._open_pane(page)  # noqa: SLF001 — dev instrument
    try:
        await composer._select_model(page, pane, VideoModel.OMNI_FLASH)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 — observation only
        row["model"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    row["radios"] = await pane.locator(RADIO).evaluate_all(_RADIO_DUMP)
    picks = []
    for seconds in lengths:
        wanted = f"{seconds}s"
        hits = [r["text"] for r in row["radios"] if _duration(wanted).search(r["text"])]
        outcome = "selected"
        try:
            await composer._select(page, pane, axis="duration", text=wanted)  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001 — the refusal IS a result
            outcome = f"{type(exc).__name__}: {str(exc)[:200]}"
        checked = [
            r["text"]
            for r in await pane.locator(RADIO).evaluate_all(_RADIO_DUMP)
            if r["checked"] == "true"
        ]
        picks.append({"wanted": wanted, "matcher_hits": hits, "outcome": outcome,
                      "checked_after": checked})
    row["picks"] = picks
    await composer._close_pane(page, strict=False)  # noqa: SLF001
    return row


async def _main(
    profile: str, project_id: str, hls: list[str], lengths: list[int], out: Path
) -> int:
    report: list[dict[str, Any]] = []
    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 — dev instrument
        assert page is not None
        for hl in ["", *hls]:
            try:
                report.append(await _pass(page, project_id, hl, lengths))
            except Exception as exc:  # noqa: BLE001 — one failed pass must not hide the rest
                report.append({"hl": hl, "error": f"{type(exc).__name__}: {str(exc)[:400]}"})
            # Written after every pass: logs share stdout, and a later crash keeps the rest.
            out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        await page.set_extra_http_headers({})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile")
    parser.add_argument("project_id")
    parser.add_argument("--hl", action="append", default=[], help="UI language (repeatable)")
    parser.add_argument("--length", action="append", type=int, default=[], help="seconds")
    parser.add_argument("--out", type=Path, default=default_out_path("spike_963_duration_locale"))
    args = parser.parse_args()
    return asyncio.run(
        _main(args.profile, args.project_id, args.hl, args.length or [4, 8], args.out)
    )


if __name__ == "__main__":
    raise SystemExit(main())
