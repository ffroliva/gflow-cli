"""Measure the account-locale probe, cold vs cached (#587). Zero credits.

The claim in #587 is a *timing* claim, so no unit test can close it: the probe
costs the full ``URL_SETTLE_TIMEOUT_MS`` only on an account Flow does not
redirect, and only against live Flow. This harness runs the real client setup
on a real profile, twice per profile:

    cold  — cache file removed; the probe runs and its outcome is persisted
    warm  — cache present; the bootstrap goes straight to the localised URL

and prints the setup wall-clock for each. Nothing is generated and no project is
created, so the run is credit-free.

Run both arms of the issue's table:

    uv run python scripts/dev/measure_locale_probe.py denon82 ffroliva

The only side effect is the ``.gflow_locale`` file this feature exists to write.
"""

from __future__ import annotations

import asyncio
import sys
from time import perf_counter

from gflow_cli.api.client import FlowApiClient
from gflow_cli.auth import profile_dir
from gflow_cli.profile_store import LOCALE_FILE, read_account_locale


async def _setup_once(profile: str, *, cold: bool) -> tuple[float, str | None]:
    pdir = profile_dir(profile)
    if cold:
        (pdir / LOCALE_FILE).unlink(missing_ok=True)
    started = perf_counter()
    async with FlowApiClient(profile_dir=pdir) as client:
        elapsed = perf_counter() - started
        return elapsed, client._account_locale


async def _main(profiles: list[str]) -> int:
    print(f"{'profile':<12} {'arm':<6} {'setup':>8}  locale   cached")
    print("-" * 52)
    for profile in profiles:
        for cold in (True, False):
            elapsed, locale = await _setup_once(profile, cold=cold)
            cached = read_account_locale(profile_dir(profile))
            print(
                f"{profile:<12} {'cold' if cold else 'warm':<6} "
                f"{elapsed:>7.2f}s  {locale or '-':<8} {cached!r}"
            )
    return 0


if __name__ == "__main__":
    names = sys.argv[1:]
    if not names:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(_main(names)))
