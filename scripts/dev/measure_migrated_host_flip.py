"""Measure WHEN ``page.url`` flips to ``flow.google.com`` after ``goto`` returns (#639).

Zero credits — pure navigation, nothing is submitted.

**The question this exists to answer.** v0.66.1 added a fast-fail: ``get_ui_driver``
raises ``FlowHostMigratedError`` when ``flow_host_kind(page.url) == "migrated"``
(``drivers/factory.py``). In the field it never fires — the reporter of #639
measured exit 36 arriving at ~57 s on three consecutive v0.66.1 runs, through the
slow selector-probe path, with ``ui_driver.migrated_host_bail`` absent from the
timeline.

The hypothesis is that the guard reads a **pre-redirect** URL: ``project_editor_url``
only ever builds a ``labs.google`` URL, the hop to ``flow.google.com`` lands AFTER
``page.goto(wait_until="domcontentloaded")`` returns, and neither settle path waits
for it (``_settle_if_redirecting`` returns immediately with no locale;
``await_url_settled`` short-circuits with one, because the labs URL already matches
``FLOW_LOCALISED_URL_RE``).

That hypothesis is a *timing* claim, so no unit test can close it. The number it
turns on — **how long after ``goto`` the flip actually lands** — is also what
decides the fix: a flip at ~50 ms means a cheap bounded wait is viable, a flip at
several seconds means the guard has to be re-checked at a point the run already
waits at, or the navigation has to target the migrated origin directly.

**What it reports**, per navigation: the URL the moment ``goto`` returns, every
subsequent URL change with its offset, the offset at which ``flow_host_kind``
first answers ``"migrated"``, and ``document.documentElement.lang`` at the end.

    uv run python scripts/dev/measure_migrated_host_flip.py ffroliva <project-id>
    uv run python scripts/dev/measure_migrated_host_flip.py --rounds 3 ffroliva <project-id>

On Windows prefer ``.venv/Scripts/python.exe`` (memory: `uv run pytest` is broken
there; the same launcher quirk applies to long-running scripts).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.transports._common import flow_host_kind  # noqa: E402
from gflow_cli.profile_store import read_account_locale  # noqa: E402

from _spike_common import build_client, default_out_path, resolve_profile_dir  # noqa: E402, isort: skip

# Fine enough to distinguish "already flipped when goto returned" from "flipped a
# few hundred ms later" — the distinction the fix turns on.
POLL_MS = 25
WATCH_S = 20.0


async def _watch(page: Any, label: str, url: str, watch_s: float = WATCH_S) -> dict[str, Any]:
    """Navigate, then sample ``page.url`` until it settles or *watch_s* elapses."""
    started = perf_counter()
    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    at_goto_return = perf_counter() - started
    seen = page.url
    changes: list[dict[str, Any]] = [{"ms": round(at_goto_return * 1000), "url": seen}]
    flipped_ms: float | None = None
    if flow_host_kind(seen) == "migrated":
        flipped_ms = at_goto_return * 1000

    while perf_counter() - started < watch_s:
        await page.wait_for_timeout(POLL_MS)
        current = page.url
        if current == seen:
            continue
        seen = current
        offset = (perf_counter() - started) * 1000
        changes.append({"ms": round(offset), "url": current})
        if flipped_ms is None and flow_host_kind(current) == "migrated":
            flipped_ms = offset

    try:
        lang = await page.evaluate("() => document.documentElement.lang || ''")
    except Exception as exc:  # noqa: BLE001 — observation only
        lang = f"<probe failed: {type(exc).__name__}>"

    return {
        "label": label,
        "requested": url,
        "url_when_goto_returned": changes[0]["url"],
        "goto_returned_ms": round(at_goto_return * 1000),
        "host_kind_when_goto_returned": flow_host_kind(changes[0]["url"]),
        "migrated_flip_ms": None if flipped_ms is None else round(flipped_ms),
        "changes": changes,
        "final_url": seen,
        "final_host_kind": flow_host_kind(seen),
        "html_lang": lang,
    }


async def _main(profile: str, project_id: str, rounds: int, watch_s: float) -> int:
    profile_dir = resolve_profile_dir(profile)
    cached_locale = read_account_locale(profile_dir)
    rows: list[dict[str, Any]] = []

    async with build_client(profile_dir) as client:
        page = client._page  # noqa: SLF001 — dev instrument
        assert page is not None
        # The locale the client actually resolved decides which URL shape the
        # transport builds, so both are recorded rather than assumed.
        locale = client._account_locale  # noqa: SLF001 — dev instrument
        editor_url = routes.project_editor_url(locale, project_id)

        for i in range(rounds):
            rows.append(await _watch(page, f"bootstrap[{i}]", routes.EDITOR_BOOTSTRAP_URL, watch_s))
            rows.append(await _watch(page, f"project[{i}]", editor_url, watch_s))

    out = {
        "profile": profile,
        "cached_locale_state": repr(cached_locale),
        "resolved_account_locale": locale,
        "project_editor_url": editor_url,
        "poll_ms": POLL_MS,
        "watch_s": watch_s,
        "navigations": rows,
    }
    path = default_out_path("migrated_host_flip")
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    print(f"\ncached locale state: {cached_locale!r}   resolved: {locale!r}")
    print(f"project URL built:   {editor_url}")
    print(f"\n{'navigation':<14} {'goto ret':>9} {'host@goto':>10} {'flip':>8}  final host")
    print("-" * 62)
    for r in rows:
        flip = "—" if r["migrated_flip_ms"] is None else f'{r["migrated_flip_ms"]} ms'
        print(
            f'{r["label"]:<14} {r["goto_returned_ms"]:>7} ms '
            f'{str(r["host_kind_when_goto_returned"]):>10} {flip:>8}  {r["final_host_kind"]}'
        )
    print(f"\nhtml lang (last): {rows[-1]['html_lang']!r}")
    print(f"written: {path}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("profile")
    ap.add_argument("project_id")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--watch", type=float, default=WATCH_S)
    ns = ap.parse_args()
    raise SystemExit(asyncio.run(_main(ns.profile, ns.project_id, ns.rounds, ns.watch)))
