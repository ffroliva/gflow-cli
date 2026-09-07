"""Every dev script that launches Chrome on a real profile must hold its lease.

`FlowApiClient` acquires a `ProfileLease` before Chrome starts
(`api/client.py`, "Own the profile BEFORE Chrome launches"), so the 20 scripts
under `scripts/dev/` that drive Flow through the client are already exclusive.
The scripts that call Playwright directly are not, and that gap is not
theoretical:

On 2026-09-07 two Claude sessions worked this repo at once. One ran the e2e
suite on `denon82` — leased. The other ran a spike that launched Chrome on a
profile directly — unleased. The second session hit `ProfileLockedError`, read
the resulting Chrome processes as orphans of its own spike, and killed eighteen
of them in two batches. Nine belonged to the running e2e suite.

Two Chrome instances on one `user_data_dir` is the destructive case this lease
exists to prevent, and the process list is what an operator reaches for when
the lease says "busy". An unleased launcher makes those processes
indistinguishable from orphans, so the lease can be working perfectly and still
lose the profile.

The guard is offline and text-level on purpose: it fires on a developer's
machine in milliseconds, before a spike is ever run, and it needs no browser.
Watched failing against all three scripts before the fix landed.
"""

from __future__ import annotations

from pathlib import Path

_DEV_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "dev"

# The Playwright entry points that start a browser process of our own. A script
# that only ATTACHES to one someone else started (connect_over_cdp) owns
# nothing and is correctly excluded.
_LAUNCH_CALLS = ("launch_persistent_context(", "chromium.launch(")

# Enough to prove the scan still resolves. Without it, renaming the Playwright
# API silently empties the offender set and the guard passes forever — the
# enumeration gap that test_workflow_hardening.py records as its own near-miss.
_MIN_EXPECTED_LAUNCHERS = 3


def _launcher_scripts() -> list[Path]:
    return sorted(
        path
        for path in _DEV_SCRIPTS.glob("*.py")
        if any(call in path.read_text(encoding="utf-8") for call in _LAUNCH_CALLS)
    )


def test_the_scan_still_finds_the_direct_launchers() -> None:
    """A guard that matches nothing passes for the wrong reason."""
    found = _launcher_scripts()
    assert len(found) >= _MIN_EXPECTED_LAUNCHERS, (
        f"only {len(found)} script(s) matched {_LAUNCH_CALLS} under {_DEV_SCRIPTS} — "
        "Playwright's launch API was probably renamed, which would empty this guard "
        "silently. Update _LAUNCH_CALLS."
    )


def test_every_direct_chrome_launcher_holds_the_profile_lease() -> None:
    offenders = [
        path.name
        for path in _launcher_scripts()
        if "ProfileLease" not in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        "these scripts launch Chrome on a real gflow profile without taking its "
        f"lease: {offenders}. Wrap the launch in `async with ProfileLease(profile_dir):` "
        "(it is already an async context manager) so a second session gets "
        "ProfileLockedError instead of a colliding browser."
    )
