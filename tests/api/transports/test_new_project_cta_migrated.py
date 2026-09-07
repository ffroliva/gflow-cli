"""The migrated gallery has no "+ New project" CTA — say that, don't blame the selector.

`NEW_PROJECT_SELECTORS` anchors on the ``add_2`` Material Symbols ligature. The migrated
``flow.google.com`` frontend renders **`add`**, and renders ``add_2`` nowhere at all —
measured 2026-09-07 on `denon82`, both surfaces, with per-surface controls
(``docs/superpowers/spikes/2026-09-07-ligature-carrier-and-name-drift.md``). So on that host
the sweep walks every selector, matches nothing, and raises:

    RuntimeError: Could not find 'New project' CTA on Flow gallery. URL: https://flow.google.com/...

That message is wrong in the way this repo keeps having to fix. It reports a **selector**
problem — the remediation for which is "check for a newer gflow-cli release, then file a
frontend bug" — when the truth is that this host does not render that control in any form.
It is the same misdiagnosis shape as a credit shortfall reported as frontend drift (#726):
a real, unfixable-by-the-reader error message pointing at the wrong culprit.

No production path reaches this today: `migrated_can_serve` refuses without a `project_id`,
`ensure_editor` navigates straight to the project URL, and `character create` requires
`--project`. Those guards are what make the `add_2` name drift harmless *right now* — and
they are exactly what a future port relaxes. A spike hit this on 2026-09-07 and got the
misleading message; the next person to un-guard a path would get it too.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.transports.ui_automation import NEW_PROJECT_SELECTORS, UiAutomationTransport
from gflow_cli.errors import FlowHostMigratedError

_MIGRATED_GALLERY = "https://flow.google.com/?hl=en"
_LABS_GALLERY = "https://labs.google/fx/en/tools/flow"


def _gallery_page(url: str) -> MagicMock:
    """A gallery page where NOTHING matches — the state the migrated host produces."""
    page = MagicMock()
    page.url = url
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.wait_for_url = AsyncMock()
    page.screenshot = AsyncMock(return_value=b"")

    def _locator(_sel: str) -> Any:
        loc = MagicMock()
        loc.first = loc
        loc.wait_for = AsyncMock(side_effect=Exception("not visible"))
        loc.click = AsyncMock()
        loc.count = AsyncMock(return_value=0)
        return loc

    page.locator = MagicMock(side_effect=_locator)
    return page


def _transport(page: MagicMock) -> UiAutomationTransport:
    t = UiAutomationTransport()
    t._setup_done = True  # type: ignore[attr-defined]
    t._page = page  # type: ignore[attr-defined]
    # The gallery preamble is not what this test is about.
    t._settle_if_redirecting = AsyncMock()  # type: ignore[attr-defined]
    t._bypass_onboarding = AsyncMock()  # type: ignore[attr-defined]
    t._dismiss_blocking_overlays = AsyncMock(return_value=False)  # type: ignore[attr-defined]
    t._require_unblocked = AsyncMock()  # type: ignore[attr-defined]
    return t


class TestNewProjectCtaOnMigratedHost:
    @pytest.mark.asyncio
    async def test_migrated_gallery_names_the_host_not_the_selector(self) -> None:
        page = _gallery_page(_MIGRATED_GALLERY)
        t = _transport(page)

        with pytest.raises(FlowHostMigratedError) as excinfo:
            await t._enter_editor(page)  # type: ignore[attr-defined]

        detail = str(excinfo.value)
        assert "flow.google.com" in detail
        assert "--project" in detail, "the error must name the way forward, not just the wall"

    @pytest.mark.asyncio
    async def test_labs_gallery_still_reports_a_missing_cta(self) -> None:
        """On labs the CTA genuinely should be there, so its absence IS selector drift.

        The migrated branch must not swallow the real failure mode it was carved out of.
        """
        page = _gallery_page(_LABS_GALLERY)
        t = _transport(page)

        with pytest.raises(RuntimeError, match="Could not find 'New project' CTA") as excinfo:
            await t._enter_editor(page)  # type: ignore[attr-defined]

        assert not isinstance(excinfo.value, FlowHostMigratedError)

    def test_the_cta_cascade_still_anchors_on_add_2(self) -> None:
        """Pins WHY the migrated branch exists, so the two cannot drift apart.

        If someone re-anchors this cascade on `add` (the ligature the migrated host
        actually renders), this test fails and the branch above should be revisited
        rather than silently kept.
        """
        assert any("add_2" in s for s in NEW_PROJECT_SELECTORS), (
            "the migrated-host branch exists because this cascade anchors on `add_2`, "
            "which flow.google.com does not render — re-anchoring it changes that premise"
        )
