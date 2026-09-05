"""Which gesture reliably inserts a mention chip? — a matrix, passive, $0.

No submit exists in this probe, so there is no credit path at all.

**Why a matrix.** A chip was inserted once (typing `@` then a query) and then twice not,
with the same code — so the gesture is not characterised and single runs cannot settle it.
Guessing one variant per browser launch is slow and proves little. This tries every
plausible gesture in ONE session, clears the composer between attempts, and reports which
produced a `.mention-chip`.

The strategies exist because each earlier failure suggested one:

* ``arrow_enter`` — the gesture the account owner uses by hand.
* ``query`` — what worked the one time it worked.
* ``query_enter`` / ``query_arrow_enter`` — the same, committed explicitly rather than
  relying on the plugin auto-selecting.
* ``slow_query`` — the picker fires `UpteDb` per keystroke; a query typed faster than the
  list can settle may leave the plugin matching against a stale set.

    uv run python scripts/dev/capture_migrated_mention_gestures.py <profile> <project-id>
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


def _stage(msg: str) -> None:
    print(f"[stage] {msg}", file=sys.stderr, flush=True)


async def _chips(page: Any) -> list[dict[str, Any]]:
    return await page.evaluate(
        """() => [...document.querySelectorAll('.mention-chip')].map(c => ({
            text: (c.textContent || '').trim(),
            entity_id: c.getAttribute('data-entity-id'),
            reference_type: c.getAttribute('data-reference-type'),
        }))"""
    )


async def _reset(page: Any) -> None:
    """Dismiss any overlay and empty the composer, so attempts cannot contaminate."""
    for _ in range(4):
        if not await page.locator(".cdk-overlay-backdrop").count():
            break
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
    try:
        await page.locator(COMPOSER).first.click(timeout=4000)
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Backspace")
    except Exception as exc:  # noqa: BLE001 — best effort between attempts
        _stage(f"reset degraded: {type(exc).__name__}")
    await page.wait_for_timeout(1200)


async def _attempt(page: Any, name: str, query: str) -> dict[str, Any]:
    await _reset(page)
    row: dict[str, Any] = {"strategy": name}
    try:
        await page.locator(COMPOSER).first.click(timeout=5000)
        await page.keyboard.type("@", delay=120)
        await page.wait_for_timeout(2200)
        row["assets_on_open"] = await page.locator("button.asset-item").count()

        if name == "arrow_enter":
            await page.keyboard.press("ArrowDown")
            await page.wait_for_timeout(1500)
            await page.keyboard.press("Enter")
        elif name == "query":
            await page.keyboard.type(query, delay=120)
        elif name == "query_enter":
            await page.keyboard.type(query, delay=120)
            await page.wait_for_timeout(2500)
            await page.keyboard.press("Enter")
        elif name == "query_arrow_enter":
            await page.keyboard.type(query, delay=120)
            await page.wait_for_timeout(2500)
            await page.keyboard.press("ArrowDown")
            await page.wait_for_timeout(1200)
            await page.keyboard.press("Enter")
        elif name == "slow_query":
            for ch in query:
                await page.keyboard.type(ch, delay=200)
                await page.wait_for_timeout(1500)

        await page.wait_for_timeout(3000)
        row["chips"] = await _chips(page)
        row["assets_after"] = await page.locator("button.asset-item").count()
        row["composer"] = await page.evaluate(
            "() => ((document.querySelector(\"[contenteditable='true']\") || {}).innerHTML || '')"
            ".slice(0, 300)"
        )
    except Exception as exc:  # noqa: BLE001 — a failed attempt is a result
        row["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    row["worked"] = bool(row.get("chips"))
    _stage(f"{name}: chips={len(row.get('chips') or [])}")
    return row


async def _probe(page: Any, project_id: str, query: str) -> dict[str, Any]:
    composer = MigratedComposer()
    await composer.ensure_editor(page, project_id)

    pane = await composer._open_pane(page)  # noqa: SLF001 — dev instrument
    await composer._select(page, pane, axis="mode", lig="videocam")  # noqa: SLF001
    await composer._select(page, pane, axis="submode", lig="chrome_extension")  # noqa: SLF001
    await composer._close_pane(page, strict=False)  # noqa: SLF001
    await page.wait_for_timeout(4000)

    rows = []
    for name in ("arrow_enter", "query", "query_enter", "query_arrow_enter", "slow_query"):
        rows.append(await _attempt(page, name, query))
    return {
        "query": query,
        "attempts": rows,
        "worked": [r["strategy"] for r in rows if r.get("worked")],
        "note": "NOTHING SUBMITTED — this probe has no submit path",
    }


async def _main(profile: str, project_id: str, query: str, out_path: str) -> int:
    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 — dev instrument
        assert page is not None
        report = await _probe(page, project_id, query)
        Path(out_path).write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        _stage(f"report written to {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile")
    parser.add_argument("project_id")
    parser.add_argument("--query", default="Me")
    parser.add_argument("--out", default="mention_gestures.json")
    args = parser.parse_args()
    return asyncio.run(_main(args.profile, args.project_id, args.query, args.out))


if __name__ == "__main__":
    raise SystemExit(main())
