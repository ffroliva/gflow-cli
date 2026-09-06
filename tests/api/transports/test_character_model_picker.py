"""The character model picker is DETERMINISTIC: it applies the tier, or it fails.

It used to be "best effort" — every failure path logged a warning and let the
generation proceed on whatever tier the editor happened to be showing. That is
the worst possible behaviour for a CLI: `--model nano2` would quietly produce a
Nano Banana Pro image, with nothing in the output to say so. A run on 2026-09-06
did exactly that on the migrated host, and a later run failed to click the menu
item and generated on the default anyway.

Failing here is **free**. The picker runs before the prompt is submitted, so an
abort costs no quota and no credits, while proceeding produces a paid artifact
the user did not ask for. So every unhappy path now raises a typed error.

Three defects fixed alongside, all found live on flow.google.com:

1. It never clicked when `--model nano2` was requested, assuming Nano Banana 2
   was the editor default. The migrated editor opens on **Nano Banana Pro**.
2. `Nano Banana 2` is a prefix of `Nano Banana 2 Lite`, and the menu offers
   three tiers, not the documented two — the #539 ambiguity.
3. The option selector `:has-text('…')` was unanchored, so `.first` matched
   `<html>`.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.errors import ConfigurationError, UiSelectorDriftError


def _picker_page(
    *,
    current: str,
    options: list[str],
    trigger_found: bool = True,
    menu_opens: bool = True,
    click_applies: bool = True,
) -> MagicMock:
    """A fake editor whose chip reads ``current`` and whose menu lists ``options``.

    ``click_applies`` models the real failure we saw: the item click lands (or
    times out) but the chip never changes. The picker must notice.
    """
    page = MagicMock()
    page.url = "https://flow.google.com/project/p1/character/e1"
    page.wait_for_timeout = AsyncMock()
    page.screenshot = AsyncMock(return_value=b"")
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()

    state = {"current": current}
    clicked: list[int] = []

    trigger = MagicMock()
    trigger.first = trigger
    trigger.nth = MagicMock(return_value=trigger)
    trigger.count = AsyncMock(return_value=1 if trigger_found else 0)
    trigger.text_content = AsyncMock(side_effect=lambda: state["current"])
    trigger.wait_for = AsyncMock(
        side_effect=None if trigger_found else Exception("trigger never visible")
    )
    trigger.click = AsyncMock()

    items = MagicMock()
    items.first = MagicMock()
    items.first.wait_for = AsyncMock(
        side_effect=None if menu_opens else Exception("menu never opened")
    )
    items.all_text_contents = AsyncMock(return_value=list(options))

    def _nth(i: int) -> MagicMock:
        node = MagicMock()

        async def _click(**_kw: Any) -> None:
            clicked.append(i)
            if click_applies:
                state["current"] = options[i]

        node.click = AsyncMock(side_effect=_click)
        return node

    items.nth = MagicMock(side_effect=_nth)

    page.locator = MagicMock(side_effect=lambda sel: items if "menuitem" in sel else trigger)
    page.clicked = clicked
    page.trigger = trigger
    page.state = state
    return page


def _transport(page: MagicMock) -> UiAutomationTransport:
    t = UiAutomationTransport()
    t._setup_done = True  # type: ignore[attr-defined]
    t._page = page  # type: ignore[attr-defined]
    return t


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nano2_is_applied_even_though_it_is_the_cli_default() -> None:
    """The migrated editor opens on Pro; asking for nano2 must actually switch."""
    page = _picker_page(
        current="Nano Banana Pro",
        options=["Nano Banana Pro", "Nano Banana 2", "Nano Banana 2 Lite"],
    )
    await _transport(page)._select_character_model(page, "nano2")  # noqa: SLF001

    assert page.clicked == [1], f"expected 'Nano Banana 2' (index 1), clicked {page.clicked}"
    assert page.state["current"] == "Nano Banana 2"


@pytest.mark.asyncio
async def test_no_click_when_the_chip_already_reads_the_requested_model() -> None:
    page = _picker_page(
        current="Nano Banana 2",
        options=["Nano Banana Pro", "Nano Banana 2", "Nano Banana 2 Lite"],
    )
    await _transport(page)._select_character_model(page, "nano2")  # noqa: SLF001

    page.trigger.click.assert_not_awaited()
    assert page.clicked == []


@pytest.mark.asyncio
async def test_nano2_does_not_match_the_lite_tier() -> None:
    """`Nano Banana 2` is a prefix of `Nano Banana 2 Lite` — take the exact tier."""
    page = _picker_page(current="Nano Banana Pro", options=["Nano Banana 2 Lite", "Nano Banana 2"])
    await _transport(page)._select_character_model(page, "nano2")  # noqa: SLF001

    assert page.clicked == [1], f"matched the Lite tier: clicked {page.clicked}"


@pytest.mark.asyncio
async def test_nanopro_selects_pro() -> None:
    page = _picker_page(
        current="Nano Banana 2",
        options=["Nano Banana Pro", "Nano Banana 2", "Nano Banana 2 Lite"],
    )
    await _transport(page)._select_character_model(page, "nanopro")  # noqa: SLF001

    assert page.clicked == [0]


# ---------------------------------------------------------------------------
# Every unhappy path RAISES — silence here bills the user for the wrong tier
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_click_that_does_not_change_the_chip_raises() -> None:
    """The observed flake: the item click times out and the tier never applies.

    Previously logged and carried on, so the generation ran on the editor's
    default and nothing said so.
    """
    page = _picker_page(
        current="Nano Banana Pro",
        options=["Nano Banana Pro", "Nano Banana 2"],
        click_applies=False,
    )
    with pytest.raises(UiSelectorDriftError, match="did not apply"):
        await _transport(page)._select_character_model(page, "nano2")  # noqa: SLF001


@pytest.mark.asyncio
async def test_a_model_the_menu_does_not_offer_raises_and_names_the_options() -> None:
    page = _picker_page(current="Nano Banana Pro", options=["Something Else"])
    with pytest.raises(ConfigurationError, match="Something Else"):
        await _transport(page)._select_character_model(page, "nano2")  # noqa: SLF001

    assert page.clicked == []


@pytest.mark.asyncio
async def test_an_ambiguous_match_refuses_rather_than_guessing() -> None:
    """Two entries match: never resolve `.first` — that is how #539 billed a tier."""
    # The chip must NOT already read Pro, or the picker rightly short-circuits
    # before ever opening the menu.
    page = _picker_page(
        current="Nano Banana 2",
        options=["Nano Banana Pro (new)", "Nano Banana Pro"],
    )
    with pytest.raises(ConfigurationError, match="matched 2"):
        await _transport(page)._select_character_model(page, "nanopro")  # noqa: SLF001

    assert page.clicked == []


@pytest.mark.asyncio
async def test_a_missing_trigger_raises() -> None:
    page = _picker_page(current="", options=["Nano Banana 2"], trigger_found=False)
    with pytest.raises(UiSelectorDriftError, match="model picker"):
        await _transport(page)._select_character_model(page, "nano2")  # noqa: SLF001


@pytest.mark.asyncio
async def test_a_menu_that_never_opens_raises() -> None:
    page = _picker_page(
        current="Nano Banana Pro",
        options=["Nano Banana 2"],
        menu_opens=False,
    )
    with pytest.raises(UiSelectorDriftError, match="menu"):
        await _transport(page)._select_character_model(page, "nano2")  # noqa: SLF001


@pytest.mark.asyncio
async def test_an_unknown_alias_raises_before_touching_the_picker() -> None:
    page = _picker_page(current="Nano Banana Pro", options=["Nano Banana 2"])
    with pytest.raises(ConfigurationError, match="not-a-model"):
        await _transport(page)._select_character_model(page, "not-a-model")  # noqa: SLF001

    page.trigger.click.assert_not_awaited()
