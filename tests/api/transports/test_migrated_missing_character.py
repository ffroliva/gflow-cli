"""#1333: an ``@Name`` that names no character is a typed refusal before submit.

Measured (live, 2026-11-18): after an empty search the picker's asset-list overlay stays
open, and Backspacing the typed query does not close it, so the retry's click on the
composer timed out under the overlay (``Locator.click: Timeout 5000ms exceeded``, exit 1).
Escape closes the picker and removes the ``@`` query
(docs/superpowers/spikes/2026-11-04-external-prs-1142-1145.md). The fake models both.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.errors import EXIT_CODE_MAP, ReferenceNotFoundError

pytestmark = pytest.mark.anyio


class _Loc:
    def __init__(self, page: _PickerPage) -> None:
        self.page = page

    @property
    def first(self) -> _Loc:
        return self

    async def click(self, **_: Any) -> None:
        if self.page.picker_open:
            raise PlaywrightTimeoutError("Locator.click: Timeout 5000ms exceeded.")


class _Keyboard:
    def __init__(self, page: _PickerPage) -> None:
        self.page = page

    async def type(self, text: str, **_: Any) -> None:
        if text == "@":
            self.page.picker_open = True

    async def press(self, key: str) -> None:
        if key == "Escape":
            self.page.picker_open = False
        if key == "Enter":
            self.page.enters += 1


class _PickerPage:
    """A project with no character of the typed name: every search offers nothing."""

    def __init__(self) -> None:
        self.picker_open = False
        self.enters = 0
        self.keyboard = _Keyboard(self)

    def locator(self, _css: str) -> _Loc:
        return _Loc(self)

    async def wait_for_timeout(self, _ms: float) -> None:
        return None

    async def evaluate(self, _script: str, _arg: Any = None) -> list[Any]:
        return []  # no option titles, no chips


async def test_a_missing_character_is_a_typed_refusal_naming_it() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = _PickerPage()
    with pytest.raises(ReferenceNotFoundError) as caught:
        await MigratedComposer().attach_character_entities(
            page,  # pyright: ignore[reportArgumentType]
            entity_ids=(),
            names=("NoSuchName",),
            clear=False,
        )
    err = caught.value
    assert "NoSuchName" in err.detail
    assert err.charged == "no"
    assert EXIT_CODE_MAP[ReferenceNotFoundError] == 32
    # Not the media-picker class default: the user is told how to list characters.
    assert "gflow character list" in err.remediation_hint
    assert page.enters == 0  # with no picker option, Enter would submit the generation
    assert not page.picker_open
