"""#1333: an ``@Name`` that names no character is a typed refusal before submit.

Measured (live, 2026-11-18): after an empty search the picker's asset-list overlay stays
open, and Backspacing the typed query does not close it, so the retry's click on the
composer timed out under the overlay (``Locator.click: Timeout 5000ms exceeded``, exit 1).
Escape closes the picker (docs/superpowers/spikes/2026-11-04-external-prs-1142-1145.md).
The fake models both, plus the prompt text, so a retry cannot leave ``@name`` behind.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.errors import ReferenceNotFoundError

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
        self.page.text += text
        if text == "@":
            self.page.picker_open = True

    async def press(self, key: str) -> None:
        page = self.page
        if key == "Backspace":
            page.text = page.text[:-1]  # the overlay stays open: the #1333 wedge
        elif key == "Escape":
            page.picker_open = False
            if page.drop_on_escape and page.chips:
                page.chips.pop()
        elif key == "Enter":
            page.enters += 1
            name = page.text.rsplit("@", 1)[-1]
            page.text = page.text[: -len(name) - 1]
            page.chips.append({"text": name, "entity_id": "", "reference_type": "entity"})
            page.picker_open = False


class _PickerPage:
    """The picker offers the character only from search ``offer_on`` on (0: never)."""

    def __init__(self, *, offer_on: int = 0, drop_on_escape: bool = False) -> None:
        self.offer_on = offer_on
        self.drop_on_escape = drop_on_escape
        self.searches = 0
        self.picker_open = False
        self.enters = 0
        self.text = ""
        self.chips: list[dict[str, str]] = []
        self.keyboard = _Keyboard(self)

    def locator(self, _css: str) -> _Loc:
        return _Loc(self)

    async def wait_for_timeout(self, _ms: float) -> None:
        return None

    async def evaluate(self, script: str, _arg: Any = None) -> Any:
        if "asset-title" in script:
            self.searches += 1
            offered = self.offer_on and self.searches >= self.offer_on
            return [self.text.rsplit("@", 1)[-1]] if offered else []
        if "collapse(false)" in script:
            return None
        return [dict(c) for c in self.chips]


async def _attach(page: _PickerPage) -> tuple[str, ...]:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    return await MigratedComposer().attach_character_entities(
        page,  # pyright: ignore[reportArgumentType]
        entity_ids=(),
        names=("NoSuchName",),
        clear=False,
    )


async def test_a_missing_character_is_a_typed_refusal_naming_it() -> None:
    page = _PickerPage()
    with pytest.raises(ReferenceNotFoundError) as caught:
        await _attach(page)
    err = caught.value
    assert "NoSuchName" in err.detail
    assert err.charged == "no"
    # Not the media-picker class default: the user is told how to list characters.
    assert "gflow character list" in err.remediation_hint
    assert page.enters == 0  # with no picker option, Enter would submit the generation
    assert not page.picker_open


async def test_a_retry_that_finds_the_character_leaves_no_query_text() -> None:
    page = _PickerPage(offer_on=2)
    await _attach(page)
    assert [c["text"] for c in page.chips] == ["NoSuchName"]
    assert page.text == ""


async def test_a_retry_that_drops_an_attached_reference_is_refused() -> None:
    page = _PickerPage(offer_on=2, drop_on_escape=True)
    page.chips.append({"text": "ref.png", "entity_id": "", "reference_type": "media"})
    with pytest.raises(ReferenceNotFoundError, match="removed an already-attached") as caught:
        await _attach(page)
    assert "gflow data list images" not in caught.value.remediation_hint
    assert page.enters == 0
