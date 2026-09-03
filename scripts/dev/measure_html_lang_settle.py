"""Does `<html lang>` change between goto-return and hydration, and what signals it? (#651)

The reporter measured `locale=en` on a pt-BR account whose pages serve
`<html lang="pt">`, and offered the mechanism as an explicit guess: the probe reads
the initial HTML shell before the app sets `lang`.

Run 1 confirmed the race on the OLD host (no migrated account needed):
`en` until ~1.9 s, `pt` from ~2.9 s.

This run adds `readyState` and a DOM-node count alongside `lang`, to find a
*hydration signal* the fix can key on instead of a guessed timing constant.

Zero credits: navigation only.
"""

from __future__ import annotations

import asyncio
import sys
from time import perf_counter
from typing import Any

sys.path.insert(0, r"C:\development\github\gflow-cli\src")
sys.path.insert(0, r"C:\development\github\gflow-cli\scripts\dev")

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.routes import locale_segment_from_lang_attr  # noqa: E402

from _spike_common import build_client, resolve_profile_dir  # noqa: E402, isort: skip

SAMPLES_MS = [0, 200, 400, 600, 900, 1200, 1600, 2000, 2400, 2800, 3200, 4000, 6000]

_SNAP = """() => ({
  lang: document.documentElement.lang || '',
  ready: document.readyState,
  nodes: document.querySelectorAll('*').length
})"""


async def _sample(page: Any, label: str, url: str) -> None:
    t0 = perf_counter()
    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    rows: list[tuple[int, str, str | None, str, int]] = []
    prev = 0
    for target in SAMPLES_MS:
        if target - prev > 0:
            await page.wait_for_timeout(target - prev)
        prev = target
        try:
            snap = await page.evaluate(_SNAP)
        except Exception as exc:  # noqa: BLE001
            snap = {"lang": f"<{type(exc).__name__}>", "ready": "?", "nodes": -1}
        rows.append(
            (
                round((perf_counter() - t0) * 1000),
                snap["lang"],
                locale_segment_from_lang_attr(snap["lang"]),
                snap["ready"],
                snap["nodes"],
            )
        )

    print(f"\n--- {label}  {url}")
    print(f"{'t_ms':>7}  {'lang':<8} {'seg':<8} {'readyState':<12} {'DOM nodes'}")
    for ms, lang, seg, ready, nodes in rows:
        print(f"{ms:>7}  {lang!r:<8} {seg!r:<8} {ready:<12} {nodes}")


async def _main(profile: str) -> int:
    pdir = resolve_profile_dir(profile)
    async with build_client(pdir) as client:
        page = client._page  # noqa: SLF001 — dev instrument
        assert page is not None
        print(f"profile={profile}  client-resolved locale={client._account_locale!r}")  # noqa: SLF001
        await _sample(page, "bootstrap", routes.EDITOR_BOOTSTRAP_URL)
        await _sample(page, "bootstrap (2nd load)", routes.EDITOR_BOOTSTRAP_URL)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1] if len(sys.argv) > 1 else "denon82")))
