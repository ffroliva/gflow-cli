"""Tripwire for the /about-landing skip guard (#888).

``tests/e2e/conftest.py`` reports an e2e failure as a SKIP when it carries
``ABOUT_LANDING_MARKER``. That guard is a substring match against a message
raised in ``src/``, so it has one failure mode worth more than the guard itself:
if the raise site is reworded, the marker stops matching, the guard silently
stops guarding, and the canary goes back to nine unexplained reds — with nobody
told that the safety net was removed.

This asserts the marker against the REAL raise site. It is offline and needs no
profile: `raise_if_known_landing` reads only ``page.url``, so a stub with that
one attribute exercises the true code path.

Deliberately lives in ``tests/`` and NOT in ``tests/e2e/``. The root conftest
auto-marks everything under ``tests/e2e/`` as ``e2e``, which the default addopts
deselect — so a tripwire placed next to the guard it protects would never run in
ordinary CI, which is exactly when it needs to fire. The thing under test is the
guard, not Flow: no profile, no network, no browser.
"""

from __future__ import annotations

import pytest

from gflow_cli.api.transports._common import raise_if_known_landing
from gflow_cli.errors import FlowAppError
from tests.e2e.conftest import ABOUT_LANDING_MARKER


class _PageAt:
    """The one attribute `raise_if_known_landing` reads."""

    def __init__(self, url: str) -> None:
        self.url = url


async def test_the_real_raise_site_still_produces_the_marker_the_guard_matches() -> None:
    """If this fails, the guard in conftest has stopped guarding — fix the marker."""
    with pytest.raises(FlowAppError) as exc_info:
        await raise_if_known_landing(
            _PageAt("https://flow.google.com/about"),
            requested="https://flow.google.com/",
            at="test",
        )

    assert ABOUT_LANDING_MARKER in str(exc_info.value), (
        f"conftest.ABOUT_LANDING_MARKER ({ABOUT_LANDING_MARKER!r}) no longer appears in the "
        f"/about failure: {exc_info.value}. The #888 skip guard is now inert and the canary "
        f"will report nine unexplained reds. Update the marker to match the new wording."
    )


async def test_an_ordinary_page_is_not_swallowed_by_the_guard() -> None:
    """The guard must not fire on a healthy navigation — it narrows, not silences."""
    await raise_if_known_landing(
        _PageAt("https://flow.google.com/project/2373d074-a61f-4a3c-846e-fb375c558d32"),
        requested="https://flow.google.com/project/2373d074-a61f-4a3c-846e-fb375c558d32",
        at="test",
    )
