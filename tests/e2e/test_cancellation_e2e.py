"""Live E2E: the D4 cancellation-safe browser teardown against a real browser.

Hits the **real Google Flow API** and therefore:
  - Is NOT collected by default ``pytest`` runs (deselected by the repo's
    ``-m 'not e2e ...'`` addopts).
  - Opt-in: ``GFLOW_CLI_E2E_PROFILE=<profile_name> pytest -m e2e``
  - Requires a logged-in Chrome profile (Pro/Ultra account).
  - **Zero credits.** The generation task is cancelled DURING
    ``FlowApiClient.__aenter__`` — deterministically, right after the real
    persistent Chrome context is launched — which is structurally BEFORE
    ``generate_image()`` is ever called: Python never runs an ``async with``
    body when ``__aenter__`` itself raises. No submit gesture is reachable,
    so no ``submit_attempted`` checkpoint and no credit is possible.

## Behavior under test (D4 cancellation-safe teardown)

When a generation is cancelled (``asyncio.CancelledError``) during browser
launch — before the credit-spending submit gesture — ``FlowApiClient``'s
teardown (``_close_browser_resources``, invoked from ``__aenter__``'s
partial-setup guard) must: close the Playwright context/browser, stop the
driver, and release the ``ProfileLease`` — leaving the profile immediately
re-leasable and no gflow-owned Chrome process behind. The deterministic
fakes in ``tests/api/test_concurrency.py``
(``test_aenter_partial_failure_tears_down_browser``,
``test_close_hang_times_out_and_force_closes``,
``test_force_close_runs_before_driver_stop``, et al.) assert this same
teardown contract with mocked Playwright; this test exercises it against a
REAL browser under a REAL cancellation.

## Design (credit-free by construction)

1. Build a ``FlowApiClient`` for the e2e profile and monkeypatch its
   ``_launch_persistent_context`` to set an ``asyncio.Event`` right after the
   REAL persistent context launch returns — a deterministic "mid-launch"
   signal instead of a guessed sleep duration.
2. Run ``async with client: await client.generate_image(...)`` as an asyncio
   task. Image t2i is used as the (unreachable) target because it is
   credit-free even in the hypothetical case it *did* run.
3. Wait for the event, then cancel the task immediately. The cancellation
   lands while ``__aenter__`` is still executing (inside ``_enter_setup``,
   somewhere between the context launch and the bootstrap page navigation /
   transport setup) — i.e. before ``__aenter__`` has returned, which means
   the ``async with`` body (the ``generate_image`` call) is provably never
   entered. There is no scheduling race that could land the cancellation
   post-submit: nothing resumes ``generate_image`` execution during a
   Playwright I/O suspension in a *different* coroutine frame.
4. Assert the awaited task raises ``CancelledError``, and that a FRESH
   ``ProfileLease`` on the same profile dir is immediately acquirable — the
   load-bearing proof the OS-level lease was released by teardown. A
   best-effort check for a leftover gflow-owned Chrome process runs when
   ``psutil`` is importable (it is not a project dependency, so this is
   skipped, not failed, when absent — the lease-reacquire assertion is the
   primary proof either way).

Does NOT cover post-submit cancellation (mid-generation, after
``submit_attempted``) — that would spend a real credit; it is exercised by
the deterministic fakes in ``tests/api/test_concurrency.py`` instead.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import BrowserContext

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import GenerateImageRequest
from gflow_cli.profile_lease import ProfileLease

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]

_PROMPT = "a calm forest at dawn, cinematic"

# Real headed Chrome launch + navigation on a cold profile is slow; generous
# so this never flakes on machine load — the signal itself is deterministic,
# only its wall-clock arrival time is uncertain.
_LAUNCH_SIGNAL_TIMEOUT_S = 120.0


@pytest.mark.asyncio
async def test_cancel_during_launch_releases_lease_and_closes_browser(
    e2e_profile_dir: Path, tmp_path: Path
) -> None:
    """D4: cancelling pre-submit tears down the real browser and frees the lease."""
    client = FlowApiClient(profile_dir=e2e_profile_dir, out_dir=tmp_path)

    # Deterministic "context launched" signal — fires the instant the REAL
    # persistent context exists, so the cancel below always lands mid-launch
    # rather than racing a guessed sleep against real browser startup time.
    launched = asyncio.Event()
    original_launch = client._launch_persistent_context

    async def _launch_and_signal(kwargs: dict[str, Any]) -> BrowserContext:
        context = await original_launch(kwargs)
        launched.set()
        return context

    client._launch_persistent_context = _launch_and_signal  # type: ignore[method-assign]

    async def _run() -> None:
        async with client:
            # Unreachable: the cancel below always lands inside __aenter__
            # (see module docstring, point 3), so this line never executes.
            # Kept to document/exercise the intended real call shape — t2i
            # is credit-free even in the hypothetical case it did run.
            req = GenerateImageRequest(prompt=_PROMPT)
            await client.generate_image(req=req)

    task = asyncio.create_task(_run())
    try:
        await asyncio.wait_for(launched.wait(), timeout=_LAUNCH_SIGNAL_TIMEOUT_S)
    finally:
        task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled(), "the generation task must end cancelled, not merely erroring"

    # Load-bearing proof: the OS-level ProfileLease was released by teardown.
    # A stuck lease would make try_acquire() return False (or, with acquire(),
    # raise ProfileLockedError) here.
    fresh_lease = ProfileLease(e2e_profile_dir)
    assert fresh_lease.try_acquire(), (
        "profile lease was not released by cancellation teardown — a leftover "
        "gflow-owned Chrome process is likely still holding it"
    )
    fresh_lease.release()

    # Secondary/best-effort: no leftover gflow-owned Chrome process for this
    # profile. psutil is NOT a project dependency (see profile_lease.py's own
    # "no psutil dependency" comment), so this check is skipped — not
    # failed — when it isn't installed. The lease-reacquire assertion above
    # already proves the OS lock released, which an unrelated process could
    # not do.
    try:
        import psutil
    except ImportError:
        return  # psutil unavailable; lease-reacquire proof above stands alone.

    profile_str = str(e2e_profile_dir)
    leftover = [
        proc.info["pid"]
        for proc in psutil.process_iter(["pid", "name", "cmdline"])
        if proc.info.get("name", "").lower().startswith("chrome")
        and any(profile_str in (arg or "") for arg in (proc.info.get("cmdline") or []))
    ]
    assert not leftover, (
        f"gflow-owned Chrome process(es) still running for this profile: {leftover}"
    )
