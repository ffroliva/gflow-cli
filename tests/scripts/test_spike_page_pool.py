"""Every dev script that checks a Page out of the pool must check it back in.

`FlowApiClient._checkout_page` is documented as blocking forever
(`api/client.py`: *"Waits indefinitely if the pool is exhausted (no Pages
available). Callers that need a deadline must wrap the call themselves"*), and
the pool is an `asyncio.Queue(maxsize=n)` that defaults to one page. So a script
that takes the only page and never returns it has not leaked a resource — it has
armed a deadlock that fires the moment anything else needs a page.

Nothing else usually does, which is why this sat latent in seven scripts. Every
`client.<verb>()` that talks to Flow goes through `_post_json` / `_patch_json`,
and those check a page out themselves. On 2026-09-07 `spike_character_prompt_format.py`
added a `client.delete_characters(...)` cleanup call in its `finally` — obeying
`skills/spike/SKILL.md`'s "delete anything the spike created" — and the script
stopped exiting. It had completed all of its work: the capture and all three
screenshots were on disk. It hung in teardown, holding the `denon82` profile
lease, with no error and no traceback, because `contextlib.suppress(Exception)`
cannot rescue a block.

That failure mode is worse than it sounds. A wedged process holding a profile
lease is exactly what makes an operator reach for the process list — the
sequence that cost nine browsers belonging to a running e2e suite the same day,
and that `test_spike_profile_lease.py` exists to prevent the other half of.

The 17 scripts that already pair the calls are the reference; `_post_json`'s own
`attempt()` in `api/client.py` is the canonical shape:

    page = await self._checkout_page()
    try:
        ...
    finally:
        self._checkin_page(page)

Offline and text-level on purpose: milliseconds, no browser, fires before a
spike is ever run. Watched failing against all seven scripts before the fix.
"""

from __future__ import annotations

from pathlib import Path

_DEV_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "dev"

_CHECKOUT = "_checkout_page"
_CHECKIN = "_checkin_page"

# Enough to prove the scan still resolves. Without it, renaming the pool API
# silently empties the offender set and the guard passes forever — the
# enumeration gap `test_workflow_hardening.py` records as its own near-miss, and
# which `test_spike_profile_lease.py` guards the same way.
_MIN_EXPECTED_BORROWERS = 15


def _borrower_scripts() -> list[Path]:
    return sorted(
        path for path in _DEV_SCRIPTS.glob("*.py") if _CHECKOUT in path.read_text(encoding="utf-8")
    )


def test_the_scan_still_finds_the_page_borrowers() -> None:
    """A guard that matches nothing passes for the wrong reason."""
    found = _borrower_scripts()
    assert len(found) >= _MIN_EXPECTED_BORROWERS, (
        f"only {len(found)} script(s) call {_CHECKOUT}() under {_DEV_SCRIPTS} — "
        "the page-pool API was probably renamed, which would empty this guard "
        "silently. Update _CHECKOUT/_CHECKIN."
    )


def test_every_page_borrower_returns_the_page() -> None:
    offenders = [
        path.name
        for path in _borrower_scripts()
        if _CHECKIN not in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"these scripts take a Page from the pool and never return it: {offenders}. "
        "The pool defaults to ONE page and `_checkout_page()` waits forever, so the "
        "next call that needs a page — including any `client.<verb>()`, which check "
        "one out internally — deadlocks in silence while holding the profile lease. "
        "Pair it: `page = await client._checkout_page()` / "
        "`finally: client._checkin_page(page)`."
    )
