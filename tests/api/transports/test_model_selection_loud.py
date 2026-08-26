"""A model that cannot be selected must fail loudly, before spending anything.

`_select_image_model` swallowed every failure and logged a WARNING, then let the
generation proceed on Flow's UI-default model. So a stale selector meant the user
asked for one model, silently received another, **and was billed for it**.

That is not hypothetical. Read live from Flow's image picker on 2026-08-26:

    Nano Banana Pro  /  Nano Banana 2  /  Nano Banana 2 Lite

`Model.IMAGEN_3_5`'s selector is `has-text('Imagen 4')` — an entry that no longer
exists. Under the old behaviour, `--model imagen-4` generated on whatever Flow
defaulted to and charged for it, with only a warning in a log nobody reads.

The AMBIGUOUS case is worse than a MISS: `has-text('Nano Banana 2')` matches BOTH
`Nano Banana 2` and `Nano Banana 2 Lite`. `.first` picks by DOM order, so it works
until Flow reorders and then silently selects the cheaper tier.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from gflow_cli.api.image import Model
from gflow_cli.api.transports.ui_automation import (
    IMAGE_MODEL_OPTION_SELECTORS,
    UiAutomationTransport,
)
from gflow_cli.errors import UiSelectorDriftError


class _Loc:
    def __init__(self, count: int, *, visible: list[bool] | None = None) -> None:
        self._count = count
        # per-index visibility, so a mounted-but-hidden duplicate can be modelled
        self._visible = visible if visible is not None else [True] * count
        self.click = AsyncMock()
        self.wait_for = AsyncMock()

    def nth(self, i: int) -> _Loc:
        one = _Loc(1, visible=[self._visible[i] if i < len(self._visible) else True])
        return one

    @property
    def first(self) -> _Loc:
        return self

    async def count(self) -> int:
        return self._count

    async def is_visible(self, **_: Any) -> bool:
        return bool(self._visible and self._visible[0])


class _Page:
    """Trigger resolves; option locators resolve per `counts`."""

    def __init__(
        self,
        counts: dict[str, int],
        offered: list[str] | None = None,
        visible: dict[str, list[bool]] | None = None,
    ) -> None:
        self._counts = counts
        self._visible = visible or {}
        self._offered = offered or []
        self.keyboard = AsyncMock()
        self.wait_for_timeout = AsyncMock()

    def locator(self, sel: str) -> _Loc:
        if "arrow_drop_down" in sel:  # the picker trigger
            return _Loc(1)
        return _Loc(self._counts.get(sel, 0), visible=self._visible.get(sel))

    async def evaluate(self, *_a: Any, **_k: Any) -> list[str]:
        return self._offered


@pytest.mark.asyncio
async def test_missing_model_raises_instead_of_silently_downgrading() -> None:
    """The Imagen 4 case: selector matches nothing.

    Old behaviour logged a warning and generated on Flow's default — billing the
    user for a model they did not ask for.
    """
    page = _Page(counts={}, offered=["Nano Banana Pro", "Nano Banana 2", "Nano Banana 2 Lite"])

    with pytest.raises(UiSelectorDriftError) as exc:
        await UiAutomationTransport._select_image_model(page, Model.IMAGEN_3_5)

    msg = str(exc.value)
    assert "imagen" in msg.lower(), msg
    # the message must name what Flow DID offer, or the operator cannot act on it
    assert "Nano Banana 2 Lite" in msg, msg


@pytest.mark.asyncio
async def test_ambiguous_model_raises_rather_than_guessing_with_first() -> None:
    """The Nano Banana 2 / 2 Lite case.

    `.first` resolves by DOM order, so an ambiguous selector silently selects
    whichever Flow happens to render first. That is a wrong-model generation the
    user pays for, and it changes without any code change on our side.
    """
    # Keyed off the REGISTRY, not a hardcoded string: this test previously
    # pinned a selector literal and silently stopped exercising ambiguity the
    # moment the selector was disambiguated.
    sel = IMAGE_MODEL_OPTION_SELECTORS[Model.NARWHAL][0]
    page = _Page(counts={sel: 2}, offered=["Nano Banana 2", "Nano Banana 2 Lite"])

    with pytest.raises(UiSelectorDriftError) as exc:
        await UiAutomationTransport._select_image_model(page, Model.NARWHAL)

    assert "ambiguous" in str(exc.value).lower(), str(exc.value)


@pytest.mark.asyncio
async def test_unique_match_still_selects_normally() -> None:
    """The guard must not break the working path."""
    sel = IMAGE_MODEL_OPTION_SELECTORS[Model.GEM_PIX_2][0]
    page = _Page(counts={sel: 1}, offered=["Nano Banana Pro"])

    await UiAutomationTransport._select_image_model(page, Model.GEM_PIX_2)  # must not raise


@pytest.mark.asyncio
async def test_panel_miss_warns_loudly_and_names_the_unapplied_model() -> None:
    """S2 — the bypass, and why it is a WARNING rather than a raise.

    `_configure_generation_settings` returned early when
    `_open_gen_settings_panel` was False, so the model was never applied AND
    never checked, and the submit proceeded on the project's residual picker
    state — the exact silent wrong-model path the guard exists to close.

    An earlier version of this fix RAISED here. The existing batch tests showed
    that turning one transient panel miss into a whole-batch abort, because the
    transport cannot tell "user passed --model X" from "X is the default" —
    `request.model` is populated either way.

    So the contract is: warn loudly, name the model that was NOT applied, and let
    `parse_media_attribution` (server truth, arm-agnostic, free) do the detecting.
    """
    page = _Page(counts={})
    with (
        patch.object(
            UiAutomationTransport, "_open_gen_settings_panel", AsyncMock(return_value=False)
        ),
        patch.object(UiAutomationTransport, "_capture_diag_screenshot", AsyncMock()),
        patch("gflow_cli.api.transports.ui_automation.log") as mock_log,
    ):
        await UiAutomationTransport._configure_generation_settings(
            page, None, None, model=Model.GEM_PIX_2
        )

    events = [c[0][0] for c in mock_log.warning.call_args_list]
    assert "ui_automation.image_model_not_applied" in events, events
    kwargs = next(
        c[1]
        for c in mock_log.warning.call_args_list
        if c[0][0] == "ui_automation.image_model_not_applied"
    )
    assert kwargs["model"] == Model.GEM_PIX_2.value


@pytest.mark.asyncio
async def test_panel_miss_without_a_requested_model_stays_non_fatal() -> None:
    """Aspect/count remain best-effort — their absence degrades output, it does
    not misattribute it. Only an unhonourable MODEL request is fatal."""
    page = _Page(counts={})
    with (
        patch.object(
            UiAutomationTransport, "_open_gen_settings_panel", AsyncMock(return_value=False)
        ),
        patch.object(UiAutomationTransport, "_capture_diag_screenshot", AsyncMock()),
    ):
        await UiAutomationTransport._configure_generation_settings(page, "16:9", 2, model=None)


@pytest.mark.asyncio
async def test_hidden_duplicate_does_not_force_a_false_ambiguous() -> None:
    """S4 — `count()` alone counts mounted-but-hidden nodes.

    Radix keeps menus mounted, so a stale menu inflates the match count. Without
    a visibility gate this refuses a picker a human would drive fine.
    """
    sel = IMAGE_MODEL_OPTION_SELECTORS[Model.GEM_PIX_2][0]
    page = _Page(counts={sel: 2}, visible={sel: [True, False]})  # one real, one stale

    await UiAutomationTransport._select_image_model(page, Model.GEM_PIX_2)  # must not raise
