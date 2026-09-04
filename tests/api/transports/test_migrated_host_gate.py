"""The migrated-host gate must fire on a URL that FLIPS, not one that starts migrated (#639).

v0.66.1 added a fast-fail to ``get_ui_driver`` and shipped with five green tests —
every one of which hands it a page whose ``.url`` is **already**
``flow.google.com``. That precondition never holds on a real run:
``routes.project_editor_url`` only ever builds a ``labs.google`` URL, and the hop
to the migrated origin is a *post-`goto`* redirect that neither settle path waits
for. So the guard read a pre-redirect URL, declined, and the run paid ~54 s before
failing anyway through the slow selector-probe path.

Reported with a field timeline on three consecutive v0.66.1 runs (57.0 / 57.1 /
58.3 s), in which ``ui_driver.migrated_host_bail`` is absent and
``ui_driver.ui_mode.attempt_exit_agent`` — logged *after* the declined bail — is
present at 3.149 s.

These tests therefore model the flip. The already-migrated case stays covered by
``tests/api/transports/drivers/test_ui_mode.py::TestMigratedOriginFailsFast``; it
is not duplicated here.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

from gflow_cli.errors import EXIT_CODE_MAP, FlowHostMigratedError, is_retryable

pytestmark = pytest.mark.asyncio

_LABS = "https://labs.google/fx/tools/flow/project/abc-123"
_MIGRATED = "https://flow.google.com/project/abc-123"
_CROP = "i.google-symbols:text-is('crop_16_9')"


class _FlippingPage:
    """`labs.google` when the run starts; `flow.google.com` once it touches the DOM.

    The redirect lands while gflow is already probing, which is the whole defect:
    a guard that reads ``page.url`` once, at entry, is reading it too early.
    ``flip=False`` models the old host, which — measured 68/68 on two profiles
    2026-09-03 — never changes URL after ``goto`` at all.
    """

    def __init__(self, *, present: set[str] | None = None, flip: bool = True) -> None:
        self.url = _LABS
        self._present = present or set()
        self._flip = flip
        self.locator_calls: list[str] = []

    def locator(self, selector: str) -> Any:
        self.locator_calls.append(selector)
        if self._flip:
            self.url = _MIGRATED
        loc = MagicMock()
        loc.count = AsyncMock(return_value=1 if selector in self._present else 0)
        loc.first = loc
        loc.wait_for = AsyncMock(side_effect=TimeoutError("not visible"))
        return loc

    async def wait_for_timeout(self, *_a: Any, **_k: Any) -> None:
        """Pacing for `mode_control._wait_until`. Not "added work" — the loop it
        paces is pre-existing; the old-host assertions below guard the three calls
        that WOULD be added work."""

    # A run that reaches for either of these has added work to the old-host path.
    async def goto(self, *_a: Any, **_k: Any) -> None:
        msg = "the guard must not navigate"
        raise AssertionError(msg)

    async def wait_for_url(self, *_a: Any, **_k: Any) -> None:
        msg = "the guard must not wait for a URL"
        raise AssertionError(msg)

    async def evaluate(self, *_a: Any, **_k: Any) -> None:
        msg = "the guard must not evaluate script"
        raise AssertionError(msg)


# --- the field case ----------------------------------------------------------


async def test_a_flip_after_entry_still_raises_exit_36() -> None:
    """The URL is labs.google at entry and flow.google.com by the first probe.

    Before the fix ``get_ui_driver`` RETURNED a classic driver here — the caller
    then died ~54 s later with `UiSelectorDriftError`, telling the user to file a
    selector bug for a host change.
    """
    from gflow_cli.api.transports.drivers.factory import get_ui_driver

    page = _FlippingPage()

    with pytest.raises(FlowHostMigratedError) as exc:
        await get_ui_driver(page, timeout_s=1.0, poll_interval_s=0.01)  # type: ignore[arg-type]

    assert EXIT_CODE_MAP[FlowHostMigratedError] == 36
    # Measured 2026-09-04: the handoff is a server-assigned config boolean that
    # labs.google acts on client-side, 5/5 and 7/7 with no flap. A retry into it
    # cannot succeed, so the machine flag must stop inviting one.
    assert not is_retryable(exc.value)
    assert "flow.google.com" in str(exc.value)
    assert "flap" not in str(exc.value).lower()


async def test_the_flip_is_caught_before_the_agent_dismissal_burns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_exit_agent_mode` finds the media panel absent — the migrated shape — and
    must ask *why* before spending ~10.6 s dismissing Agent affordances that are
    not there. `_media_panel_present` uses `.count()` and does not wait, so this
    check costs the old host nothing."""
    from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin

    dismissed = False

    async def _never(*_a: Any, **_k: Any) -> bool:
        nonlocal dismissed
        dismissed = True
        return False

    monkeypatch.setattr(VideoGenerationMixin, "_dismiss_agent_affordances", _never)
    page = _FlippingPage()  # media panel absent -> crop selectors all miss

    with pytest.raises(FlowHostMigratedError):
        await VideoGenerationMixin._exit_agent_mode(page)  # type: ignore[arg-type]

    assert dismissed is False, "the doomed dismissal must be skipped once the host is known"


# --- the path that still works ----------------------------------------------


async def test_old_host_adds_no_wait_and_no_navigation() -> None:
    """The merge gate in miniature: 100% of today's real loads take this path.

    `goto`, `wait_for_url` and `evaluate` all raise on this fake, so any added
    wait or navigation fails the test rather than merely slowing the suite.
    """
    from gflow_cli.api.transports.drivers.factory import detect_ui_mode

    page = _FlippingPage(present={_CROP}, flip=False)

    assert await detect_ui_mode(page, timeout_s=0.5, poll_interval_s=0.01) == "classic"  # type: ignore[arg-type]


async def test_guard_reads_the_page_it_is_about_to_drive() -> None:
    """Two pooled Pages can be on different hosts in one run — a fresh navigation
    is where an account's server-assigned flag takes effect. A bail on one must
    not abort the other, which rules out any module-level 'this account is
    migrated' flag: the record lives on the page object."""
    from gflow_cli.api.transports.drivers.factory import detect_ui_mode

    migrating = _FlippingPage()
    healthy = _FlippingPage(present={_CROP}, flip=False)

    with pytest.raises(FlowHostMigratedError):
        await detect_ui_mode(migrating, timeout_s=1.0, poll_interval_s=0.01)  # type: ignore[arg-type]

    assert await detect_ui_mode(healthy, timeout_s=0.5, poll_interval_s=0.01) == "classic"  # type: ignore[arg-type]


async def test_unreadable_url_is_never_migrated() -> None:
    """A page whose `.url` is not a usable string must keep probing. Classifying
    it as migrated would turn a transient into a permanent-looking abort."""
    from gflow_cli.api.transports.drivers.factory import detect_ui_mode

    page = MagicMock()  # .url is a MagicMock, never assigned

    def _locator(sel: str) -> Any:
        loc = MagicMock()
        loc.count = AsyncMock(return_value=1 if sel == _CROP else 0)
        return loc

    page.locator = MagicMock(side_effect=_locator)

    assert await detect_ui_mode(page, timeout_s=0.5, poll_interval_s=0.01) == "classic"


# --- observability -----------------------------------------------------------


async def test_bail_emits_a_stable_event_naming_the_host_and_the_site() -> None:
    """This defect survived a release because the fast path was *inferred* from a
    function measured in isolation. A field timeline has to be able to show it."""
    from gflow_cli.api.transports.drivers.factory import get_ui_driver

    page = _FlippingPage()

    with capture_logs() as logs, pytest.raises(FlowHostMigratedError):
        await get_ui_driver(page, timeout_s=1.0, poll_interval_s=0.01)  # type: ignore[arg-type]

    bail = [entry for entry in logs if entry["event"] == "ui_driver.migrated_host_bail"]
    assert bail, f"expected a migrated_host_bail event, got {[e['event'] for e in logs]}"
    assert bail[0]["url"] == _MIGRATED
    assert bail[0]["at"] == "detect_ui_mode", "the event must say WHERE the host became knowable"


# --- the long waits nobody was guarding ------------------------------------


async def test_agentic_arm_does_not_burn_the_composer_poll_on_a_migrated_host() -> None:
    """`--ui-mode agentic` reached `mode_control.ensure_agent_mode`, whose first act
    is an 8 s `_composer_present` poll that on `flow.google.com` can never succeed —
    and which returns normally rather than raising, so `get_ui_driver`'s blanket
    `except Exception` never fired either.

    The AGENTIC arm's own guard cannot cover this: no `await` separates it from the
    entry guard, so it is the same point-in-time snapshot. Only a per-tick re-check
    inside the poll can see a flip that happens *during* it.
    """
    from gflow_cli.api.transports.drivers.factory import get_ui_driver
    from gflow_cli.config import UiMode

    page = _FlippingPage()

    with pytest.raises(FlowHostMigratedError):
        await get_ui_driver(
            page,  # type: ignore[arg-type]
            ui_mode=UiMode.AGENTIC,
            timeout_s=1.0,
            poll_interval_s=0.01,
        )


async def test_mode_control_poll_re_reads_the_host_every_tick() -> None:
    """The same gap on the CLASSIC path: `_exit_agent_mode`'s guard is taken before
    `ensure_media_mode` starts, so a redirect landing inside that poll was unseen
    until the caller's failure classifier ran ~10 s later."""
    from gflow_cli.api.transports.mode_control import _wait_until

    page = _FlippingPage()

    async def _probe(p: Any) -> bool:
        # Mirrors `_composer_present`: it touches the DOM, which is when the
        # pending redirect becomes visible on `page.url`.
        await p.locator("whatever").count()
        return False

    with pytest.raises(FlowHostMigratedError):
        await _wait_until(page, _probe, 8000)  # type: ignore[arg-type]


async def test_mode_control_poll_is_untouched_on_the_old_host() -> None:
    """No regression: the guard must not disturb a probe that simply times out."""
    from gflow_cli.api.transports.mode_control import _wait_until

    page = _FlippingPage(flip=False)

    async def _never(_p: Any) -> bool:
        return False

    assert await _wait_until(page, _never, 50) is False  # type: ignore[arg-type]
