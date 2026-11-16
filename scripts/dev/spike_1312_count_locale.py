"""#1312 — what the count radios say outside English (e.g. German hl=de).

Zero credits spent: opens the settings pane in an existing project, reads every radio,
runs the shipped _select(axis='count', text='x1') and records what matches.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gflow_cli.api.transports._mc_common import (  # noqa: E402
    RADIO,
    _exact,
)
from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    MigratedComposer,
)

from _spike_common import build_client, resolve_profile_dir  # noqa: E402, isort: skip

_RADIO_DUMP = """els => els.map(e => ({
    text: (e.textContent || '').trim(),
    checked: e.getAttribute('aria-checked'),
    attrs: Object.fromEntries([...e.attributes].map(a => [a.name, a.value])),
}))"""


def _count(label: str) -> re.Pattern[str]:
    """Locale-invariant count matcher: matches 'x1', '1x', '1-mal', bare '1'."""
    number = label.removeprefix("x").removesuffix("x").strip()
    return re.compile(
        r"^\s*(?:x\s*)?" + re.escape(number) + r"(?!\d)(?:\s*[-a-zA-Z\s]+)?$", re.IGNORECASE
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", help="Profile name or path")
    parser.add_argument("project_id", help="Flow project ID")
    parser.add_argument("--hl", default="de", help="UI language (default: de)")
    args = parser.parse_args()

    profile_dir = resolve_profile_dir(args.profile)
    async with build_client(profile_dir) as client:
        page = client._page  # noqa: SLF001
        assert page is not None
        composer = MigratedComposer()

        url = f"https://flow.google.com/project/{args.project_id}?hl={args.hl}"
        print(f"Navigating to {url}...")
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await composer.ensure_editor(page, args.project_id)

        pane = await composer._open_pane(page)  # noqa: SLF001
        radios = await pane.locator(RADIO).evaluate_all(_RADIO_DUMP)
        print(f"Found {len(radios)} radios in settings pane:")
        for r in radios:
            print(f"  - text={r['text']!r}, checked={r['checked']}")

        print("\nTesting shipped _exact('x1'):")
        exact_hits = [r["text"] for r in radios if _exact("x1").search(r["text"])]
        print(f"  Hits: {exact_hits}")

        print("\nTesting proposed locale-invariant _count('x1'):")
        count_hits = [r["text"] for r in radios if _count("x1").search(r["text"])]
        print(f"  Hits: {count_hits}")

        print("\nTesting actual composer._select(axis='count', text='x1') with worktree code:")
        await composer._select(page, pane, axis="count", text="x1")  # noqa: SLF001
        print("  SUCCESS: composer._select completed without error!")

        # Verify that '1-mal' is now checked:
        radios_after = await pane.locator(RADIO).evaluate_all(_RADIO_DUMP)
        for r in radios_after:
            if _count("x1").search(r["text"]):
                print(
                    f"  Verified selected radio state: text={r['text']!r}, checked={r['checked']}"
                )
                assert r["checked"] == "true", (
                    f"Expected {r['text']} to be checked=true, got {r['checked']}"
                )

        await composer._close_pane(page, strict=False)  # noqa: SLF001


if __name__ == "__main__":
    asyncio.run(main())
