"""Measure the account-locale probe, cold vs cached (#587). Zero credits.

The claim in #587 is a *timing* claim, so no unit test can close it: the probe
costs the full ``URL_SETTLE_TIMEOUT_MS`` only on an account Flow does not
redirect, and only against live Flow.

**What "warm" means here.** The navigation is bare on BOTH arms — the cache never
supplies the URL, only the answer to "does this account redirect?". So the warm
arm differs from the cold one in exactly one way: when the cached answer is
``NOT_REDIRECTED`` the settle is skipped. On a *redirecting* account (e.g. a pt
profile) warm and cold are expected to be identical, and that row is the control:
it shows what the cold-then-warm ordering is worth on its own.

**Why the warm arm primes first.** A no-redirect observation is PROVISIONAL until
a second run agrees, so the run right after a cold one still probes. The warm arm
therefore primes to a terminal cache state before timing anything.

**Why it repeats.** A single cold/warm pair measures the fix plus whatever
browser/OS warm-up the first run paid. The first version of this script reported
a 4.42 s delta against a 4.0 s mechanism ceiling — an excess that can only be
noise. Each arm is therefore run ``--rounds`` times and the **minimum** is
reported, the standard way to read a timing sample whose noise is one-sided.

    uv run python scripts/dev/measure_locale_probe.py denon82 ffroliva
    uv run python scripts/dev/measure_locale_probe.py --rounds 5 ffroliva

The only side effect is the ``.gflow_locale`` file this feature exists to write.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gflow_cli.api.client import FlowApiClient  # noqa: E402
from gflow_cli.profile_store import (  # noqa: E402
    LOCALE_FILE,
    PROVISIONAL,
    read_account_locale,
)

from _spike_common import resolve_profile_dir  # noqa: E402, isort: skip


async def _setup_once(profile_dir: Path, *, cold: bool) -> tuple[float, str | None]:
    if cold:
        (profile_dir / LOCALE_FILE).unlink(missing_ok=True)
    started = perf_counter()
    async with FlowApiClient(profile_dir=profile_dir) as client:
        elapsed = perf_counter() - started
        return elapsed, client._account_locale  # noqa: SLF001 — dev instrument


async def _prime(profile_dir: Path) -> None:
    """Run until the cache reaches a terminal state, so "warm" really is warm.

    A no-redirect observation is only PROVISIONAL until a second run agrees
    (#587), so on such an account the second run still probes. Measuring it as
    the warm arm would report no improvement at all — the instrument, not the fix.
    """
    for _ in range(3):
        if read_account_locale(profile_dir) not in (None, PROVISIONAL):
            return
        await _setup_once(profile_dir, cold=False)


async def _main(profiles: list[str], rounds: int) -> int:
    # Resolve every profile BEFORE launching anything: a typo must not create and
    # ACL-harden a junk profile dir halfway through a run.
    dirs = {name: resolve_profile_dir(name) for name in profiles}

    print(f"{'profile':<12} {'arm':<6} {'best':>8} {'of':>3}  locale   cached")
    print("-" * 56)
    regressed = False
    for name, pdir in dirs.items():
        best: dict[str, float] = {}
        locales: dict[str, str | None] = {}
        for arm, cold in (("cold", True), ("warm", False)):
            if arm == "warm":
                await _prime(pdir)
            samples: list[float] = []
            for _ in range(rounds):
                elapsed, locale = await _setup_once(pdir, cold=cold)
                samples.append(elapsed)
                locales[arm] = locale
            best[arm] = min(samples)
            print(
                f"{name:<12} {arm:<6} {best[arm]:>7.2f}s {rounds:>3}  "
                f"{locales[arm] or '-':<8} {read_account_locale(pdir)!r}"
            )
        # A redirecting account is the CONTROL: warm == cold is the expected,
        # correct result there, so only a slower warm arm is a real regression.
        if best["warm"] > best["cold"] + 0.5:
            print(f"  !! {name}: warm arm slower than cold — investigate")
            regressed = True
    return 1 if regressed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("profiles", nargs="+", help="profile names to measure")
    ap.add_argument("--rounds", type=int, default=3, help="samples per arm (default: 3)")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.profiles, max(1, args.rounds))))
