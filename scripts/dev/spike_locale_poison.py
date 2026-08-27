"""Does Flow correct a wrong-but-valid locale segment? (#587). Zero credits.

This is the load-bearing experiment behind #587's design. The issue assumed a
stale cached locale is self-correcting — *"a stale value produces one redirect,
which the #584 settle already tolerates"*. If that were true, the fast design
(navigate straight to the cached ``/fx/{seg}/...`` and skip the settle) would be
safe, and it saves ~3 s on every account rather than only the non-redirecting
ones.

It is not true. Run this and read the CONTROL row against the POISON row.

    uv run python scripts/dev/spike_locale_poison.py denon82
    uv run python scripts/dev/spike_locale_poison.py --segment ja denon82 ffroliva

Measured 2026-08-27 on a pt-BR account, poisoned with ``de``:

    arm      requested                              landed                                 lang
    control  https://labs.google/fx/tools/flow      https://labs.google/fx/pt/tools/flow   en
    poison   https://labs.google/fx/de/tools/flow   https://labs.google/fx/de/tools/flow   de

Flow serves whatever segment it is asked for. No redirect, so no correction
signal ever arrives, and the UI renders in a language the account never chose —
for as long as the stale value lives. Hence the shipped design: the cache decides
whether to WAIT, never where to GO, and only a bare navigation is evidence.

The second half asserts the shipped recovery: a poisoned cache heals on the very
next ordinary run, because that run navigates bare.

Restores the profile's original cache value on the way out.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.client import FlowApiClient  # noqa: E402
from gflow_cli.profile_store import read_account_locale, write_account_locale  # noqa: E402

from _spike_common import resolve_profile_dir  # noqa: E402, isort: skip

_ROW = "{:<8} {:<40} {:<40} {}"


async def _land_on(profile_dir: Path, url: str, settle_ms: int) -> tuple[str, str]:
    """Navigate to *url*, wait past any redirect, and report where we ended up."""
    async with FlowApiClient(profile_dir=profile_dir) as client:
        page = client._page  # noqa: SLF001 — dev instrument
        assert page is not None
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        # Well past URL_SETTLE_TIMEOUT_MS: if a redirect were coming, it has come.
        await page.wait_for_timeout(settle_ms)
        lang = await page.evaluate("document.documentElement.lang")
        return str(page.url), str(lang)


async def _run(profile: str, segment: str, settle_ms: int) -> bool:
    pdir = resolve_profile_dir(profile)
    original = read_account_locale(pdir)
    print(f"\n=== {profile} (cache before: {original!r}) ===")
    print(_ROW.format("arm", "requested", "landed", "lang"))
    try:
        bare = routes.EDITOR_BOOTSTRAP_URL
        landed_bare, lang_bare = await _land_on(pdir, bare, settle_ms)
        print(_ROW.format("control", bare, landed_bare, lang_bare))

        poisoned = f"https://labs.google/fx/{segment}/tools/flow?hl=en"
        landed_bad, lang_bad = await _land_on(pdir, poisoned, settle_ms)
        print(_ROW.format("poison", poisoned, landed_bad, lang_bad))

        corrected = routes.locale_segment_from_url(landed_bad) != segment
        print(
            f"\nFlow corrected the wrong segment: {corrected}"
            f"   (expected False — this is why the navigation stays bare)"
        )

        # Shipped recovery: a poisoned CACHE heals on the next ordinary run.
        write_account_locale(pdir, segment)
        async with FlowApiClient(profile_dir=pdir) as client:
            used = client._account_locale  # noqa: SLF001
        healed = read_account_locale(pdir)
        print(f"cache poisoned with {segment!r} -> run used {used!r} -> cache now {healed!r}")
        return not corrected and healed != segment
    finally:
        write_account_locale(pdir, original or None)
        print(f"restored cache to {read_account_locale(pdir)!r}")


async def _main(profiles: list[str], segment: str, settle_ms: int) -> int:
    results = [await _run(p, segment, settle_ms) for p in profiles]
    ok = all(results)
    print("\nRESULT:", "as expected" if ok else "UNEXPECTED — re-read the rows above")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("profiles", nargs="+")
    ap.add_argument("--segment", default="de", help="wrong-but-valid segment (default: de)")
    ap.add_argument("--settle-ms", type=int, default=6000)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.profiles, args.segment, args.settle_ms)))
