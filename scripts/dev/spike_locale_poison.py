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

    arm      requested              landed                 lang
    control  /fx/tools/flow?hl=en   /fx/pt/tools/flow      pt     <- Flow chose pt
    poison   /fx/de/tools/flow      /fx/de/tools/flow      de     <- served as asked

The conclusion rests on the LANDED column, not on ``lang``: both arms carry
``?hl=en``, which the path segment overrides, so ``lang`` has been observed both
``en`` and ``pt`` on the control across runs.

Flow serves whatever segment it is asked for. No redirect, so no correction
signal ever arrives, and the UI renders in a language the account never chose —
for as long as the stale value lives. Hence the shipped design: the cache decides
whether to WAIT, never where to GO, and only a bare navigation is evidence.

The second half asserts the two shipped recoveries: a cache poisoned with a
SEGMENT heals via the bootstrap settle, and one wrongly reading NOT_REDIRECTED --
the state a single transient timeout produces -- heals from the landing URL at
teardown. The latter is best-effort: it needs the session to outlive Flow's
redirect, which any real command does.

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
from gflow_cli.profile_store import (  # noqa: E402
    LOCALE_FILE,
    NOT_REDIRECTED,
    read_account_locale,
    write_account_locale,
)

from _spike_common import resolve_profile_dir  # noqa: E402, isort: skip

_ROW = "{:<8} {:<44} {:<44} {}"


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

        # Recovery A -- a cache poisoned with a SEGMENT heals via the bootstrap
        # settle, which rewrites the account locale on every such run.
        write_account_locale(pdir, segment)
        async with FlowApiClient(profile_dir=pdir) as client:
            used = client._account_locale  # noqa: SLF001
        healed_a = read_account_locale(pdir)
        print(f"cache={segment!r} (segment) -> run used {used!r} -> cache now {healed_a!r}")

        # Recovery B -- a cache wrongly reading NOT_REDIRECTED, the state ONE
        # transient settle timeout produces. Nothing upstream repairs it: the
        # settle is skipped, so only `_persist_locale_correction` can, and this is
        # the only arm that reaches its write branch. Recovery A does NOT exercise
        # it (a cached segment makes the correction stand down by design).
        write_account_locale(pdir, None)
        async with FlowApiClient(profile_dir=pdir) as client:
            used_b = client._account_locale  # noqa: SLF001
            # The bare bootstrap redirect lands AFTER goto returns, and this arm
            # skips the settle by design (that is the 4 s it saves). A real command
            # then does seconds of work, so the redirect has long landed by the
            # time teardown reads page.url. An open-and-close has not -- without
            # this wait the arm measures the harness, not the fix.
            await client._page.wait_for_timeout(settle_ms)  # noqa: SLF001
        healed_b = read_account_locale(pdir)
        print(
            f"cache={NOT_REDIRECTED!r} (transient-timeout state) -> run used {used_b!r} "
            f"-> cache now {healed_b!r}"
        )

        return not corrected and healed_a != segment and healed_b != NOT_REDIRECTED
    finally:
        # `write_account_locale(pdir, original or None)` would turn a NEVER-PROBED
        # cache (None) into NOT_REDIRECTED (""), leaving a real profile marked
        # "skip the settle" -- this instrument re-creating the very bug it studies.
        if original is None:
            (pdir / LOCALE_FILE).unlink(missing_ok=True)
        else:
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
