"""The two duration sliders and the two credit-quote readers, pinned one by one (#1499).

``migrated_extend`` (the Extend popover) and ``_mc_settings_pane`` (Omni 1.2 in the
generation settings) drive the same ``flow-duration-slider`` and read the same
``flow-credit-cost-label``, but not identically: the Omni scale is an index offset by
3 s with a fixed range, the Extend scale is seconds with whatever range Flow serves; the
generation pane reads the first label, the Extend popover the last one with a fallback.
These tests fix each of those differences so that sharing the code cannot blur them.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import Error as PlaywrightError

from gflow_cli.api.transports import _mc_settings_pane
from gflow_cli.api.transports import migrated_extend as mx
from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.errors import ConfigurationError, CreditCapExceededError, UiSelectorDriftError

# --- fakes ---------------------------------------------------------------------------


class _Slider:
    """An ``input[type=range]``; ``value`` is the raw slider value, ``label`` what Flow shows."""

    def __init__(
        self,
        *,
        value: int,
        minimum: str,
        maximum: str,
        step: str = "1",
        count: int = 1,
        enabled: bool = True,
        label: Any = None,
        wait_error: BaseException | None = None,
    ) -> None:
        self.value = value
        self.attrs = {"min": minimum, "max": maximum, "step": step}
        self._count = count
        self.enabled = enabled
        self.label = label
        self.wait_error = wait_error
        self.keys: list[str] = []
        self.first = self

    async def wait_for(self, **_kw: Any) -> None:
        if self.wait_error is not None:
            raise self.wait_error

    async def count(self) -> int:
        return self._count

    async def get_attribute(self, name: str) -> str | None:
        return self.attrs[name]

    async def is_enabled(self) -> bool:
        return self.enabled

    async def input_value(self) -> str:
        return str(self.value)

    async def press(self, key: str) -> None:
        self.keys.append(key)
        if key == "Home":
            self.value = int(self.attrs["min"])
        elif key == "ArrowRight":
            self.value = min(int(self.attrs["max"]), self.value + 1)


class _Text:
    """Lazy, like a Playwright locator: the text is read when asked for, not when located."""

    def __init__(self, read: Any) -> None:
        self.read = read

    async def inner_text(self, **_kw: Any) -> str:
        return self.read()


class _Component:
    """``flow-duration-slider``: the range input plus the text Flow renders beside it."""

    def __init__(self, slider: _Slider, *, text: Any, value_text: Any = None) -> None:
        self.slider = slider
        self._text = text
        self._value_text = value_text
        self.asked: list[str] = []

    def locator(self, selector: str) -> Any:
        self.asked.append(selector)
        if selector == 'input[type="range"]':
            return self.slider
        if selector == ".duration-slider-value":
            return _Text(lambda: self._value_text(self.slider))
        raise AssertionError(selector)

    async def inner_text(self) -> str:
        return self._text(self.slider)


def _pane(component: _Component) -> Any:
    def locator(selector: str) -> Any:
        assert selector == "flow-duration-slider", selector
        return component

    return SimpleNamespace(locator=locator)


# --- the Extend slider -----------------------------------------------------------------


def _extend_component(slider: _Slider, text: Any = None) -> _Component:
    return _Component(slider, text=text or (lambda s: f"Duration\n{s.value} s"))


async def _set_extend(slider: _Slider, duration: int, text: Any = None) -> bool:
    return await mx._set_extend_duration_slider(_pane(_extend_component(slider, text)), duration)


@pytest.mark.asyncio
async def test_extend_slider_moves_from_home_in_seconds_and_reports_it_moved() -> None:
    slider = _Slider(value=4, minimum="3", maximum="10")
    assert await _set_extend(slider, 7) is True
    assert slider.value == 7
    assert slider.keys == ["Home", *["ArrowRight"] * 4]


@pytest.mark.asyncio
async def test_extend_slider_already_at_the_duration_is_left_alone() -> None:
    slider = _Slider(value=6, minimum="3", maximum="10")
    assert await _set_extend(slider, 6) is False
    assert slider.keys == []


@pytest.mark.asyncio
async def test_extend_slider_takes_the_range_flow_serves() -> None:
    slider = _Slider(value=2, minimum="2", maximum="15")
    assert await _set_extend(slider, 15) is True
    assert slider.value == 15


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,duration", [(True, 11), (True, 2), (False, 5)])
async def test_extend_slider_refuses_an_unoffered_duration_without_touching_it(
    enabled: bool, duration: int
) -> None:
    slider = _Slider(value=4, minimum="3", maximum="10", enabled=enabled)
    with pytest.raises(ConfigurationError) as caught:
        await _set_extend(slider, duration)
    assert caught.value.detail == (
        f"extend duration {duration}s is not offered by this account (3-10s)"
    )
    assert caught.value.remediation_hint == "Pass --duration between 3 and 10, or omit it."
    assert slider.keys == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attrs",
    [
        {"step": "2"},
        {"min": "x"},
        {"max": None},
        {"min": "-1"},
    ],
)
async def test_extend_slider_with_a_changed_scale_is_drift(attrs: dict[str, Any]) -> None:
    slider = _Slider(value=4, minimum="3", maximum="10")
    slider.attrs.update(attrs)
    with pytest.raises(UiSelectorDriftError) as caught:
        await _set_extend(slider, 5)
    a = slider.attrs
    assert caught.value.detail == (
        "migrated host: extend duration slider range changed "
        f"(min={a['min']!r}, max={a['max']!r}, step={a['step']!r})"
    )
    assert slider.keys == []


@pytest.mark.asyncio
async def test_extend_slider_that_is_not_unique_is_drift() -> None:
    slider = _Slider(value=4, minimum="3", maximum="10", count=2)
    with pytest.raises(UiSelectorDriftError) as caught:
        await _set_extend(slider, 5)
    assert caught.value.detail == "migrated host: extend duration slider ambiguous"
    assert slider.keys == []


@pytest.mark.asyncio
async def test_extend_slider_that_never_shows_is_drift() -> None:
    slider = _Slider(value=4, minimum="3", maximum="10", wait_error=PlaywrightError("gone"))
    with pytest.raises(UiSelectorDriftError) as caught:
        await _set_extend(slider, 5)
    assert caught.value.detail == (
        "migrated host: extend duration slider (flow-duration-slider) did not appear "
        "(host=migrated)"
    )


@pytest.mark.asyncio
async def test_extend_slider_wait_lets_a_non_playwright_error_through() -> None:
    slider = _Slider(value=4, minimum="3", maximum="10", wait_error=RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        await _set_extend(slider, 5)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        lambda _s: "Duration\n4 s",  # the label did not follow the value
        lambda s: f"Duration\n{s.value}5 s",  # a longer number that contains it is no match
    ],
)
async def test_extend_slider_label_must_show_the_duration(text: Any) -> None:
    slider = _Slider(value=4, minimum="3", maximum="10")
    with pytest.raises(UiSelectorDriftError) as caught:
        await _set_extend(slider, 6, text)
    assert caught.value.detail.startswith(
        "migrated host: extend duration slider did not bind 6s (value='6', label="
    )


@pytest.mark.asyncio
async def test_extend_slider_value_must_follow_the_keys() -> None:
    slider = _Slider(value=4, minimum="3", maximum="10")
    slider.attrs["max"] = "10"

    async def stuck(key: str) -> None:
        slider.keys.append(key)

    slider.press = stuck  # type: ignore[method-assign]
    with pytest.raises(UiSelectorDriftError) as caught:
        await _set_extend(slider, 6)
    assert caught.value.detail == (
        "migrated host: extend duration slider did not bind 6s (value='4', label='Duration\\n4 s')"
    )


# --- the Omni 1.2 slider ---------------------------------------------------------------


async def _select_omni(slider: _Slider, seconds: int, *, value_text: Any = None) -> Any:
    component = _Component(
        slider,
        text=lambda _s: pytest.fail("the Omni label is read from .duration-slider-value"),
        value_text=value_text or (lambda s: f"{s.value + 3}s"),
    )
    menu = SimpleNamespace(first=SimpleNamespace(wait_for=AsyncMock()))
    asked: list[str] = []

    def page_locator(selector: str) -> Any:
        asked.append(selector)
        return menu

    page = SimpleNamespace(locator=page_locator)
    await MigratedComposer()._select_omni12_duration(page, _pane(component), seconds)  # type: ignore[arg-type]
    return SimpleNamespace(menu=menu, asked=asked, component=component)


def _omni(**kw: Any) -> _Slider:
    kw.setdefault("value", 5)
    kw.setdefault("minimum", "0")
    kw.setdefault("maximum", "12")
    return _Slider(**kw)


@pytest.mark.asyncio
async def test_omni_slider_waits_for_the_model_menu_to_close_first() -> None:
    seen = await _select_omni(_omni(), 8)
    assert seen.asked == ["[role='menuitem']"]
    seen.menu.first.wait_for.assert_awaited_once_with(state="hidden", timeout=4000)


@pytest.mark.asyncio
async def test_omni_slider_is_an_index_three_seconds_below_the_length() -> None:
    slider = _omni()
    await _select_omni(slider, 8)
    assert slider.value == 5
    assert slider.keys == ["Home", *["ArrowRight"] * 5]


@pytest.mark.asyncio
async def test_omni_slider_is_pressed_even_when_it_already_shows_the_length() -> None:
    slider = _omni(value=5)
    await _select_omni(slider, 8)
    assert slider.keys == ["Home", *["ArrowRight"] * 5]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("boom"), TimeoutError(), PlaywrightError("x")])
async def test_omni_slider_that_never_shows_is_drift_whatever_the_error(
    error: BaseException,
) -> None:
    slider = _omni(wait_error=error)
    with pytest.raises(UiSelectorDriftError) as caught:
        await _select_omni(slider, 8)
    assert caught.value.detail == (
        "Omni 1.2 duration slider missing (model menu open or slider unmounted)"
    )


@pytest.mark.asyncio
async def test_omni_slider_that_is_not_unique_is_drift() -> None:
    slider = _omni(count=2)
    with pytest.raises(UiSelectorDriftError) as caught:
        await _select_omni(slider, 8)
    assert caught.value.detail == "Omni 1.2 duration slider ambiguous"
    assert slider.keys == []


@pytest.mark.asyncio
@pytest.mark.parametrize("attrs", [{"min": "1"}, {"max": "10"}, {"step": "2"}, {"min": None}])
async def test_omni_slider_accepts_only_the_measured_scale(attrs: dict[str, Any]) -> None:
    slider = _omni()
    slider.attrs.update(attrs)
    with pytest.raises(UiSelectorDriftError) as caught:
        await _select_omni(slider, 8)
    assert caught.value.detail == "Omni 1.2 duration slider range changed"
    assert slider.keys == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "maximum,enabled,seconds", [("7", True, 11), ("12", False, 8), ("12", True, 2)]
)
async def test_omni_slider_refuses_an_unoffered_length_without_touching_it(
    maximum: str, enabled: bool, seconds: int
) -> None:
    slider = _omni(maximum=maximum, enabled=enabled)
    with pytest.raises(ConfigurationError) as caught:
        await _select_omni(slider, seconds)
    assert caught.value.detail == f"Omni 1.2 duration {seconds}s is not offered by this account"
    assert caught.value.remediation_hint == (
        "Choose an offered duration; 11–15s requires a Google AI plan."
    )
    assert slider.keys == []


@pytest.mark.asyncio
async def test_omni_slider_label_must_show_the_length() -> None:
    slider = _omni()
    with pytest.raises(UiSelectorDriftError) as caught:
        await _select_omni(slider, 8, value_text=lambda _s: "9s")
    assert caught.value.detail == ("Omni 1.2 duration slider did not bind 8s: index=5, label='9s'")


# --- the credit-quote readers ----------------------------------------------------------


class _Labels:
    """A locator over several ``flow-credit-cost-label`` nodes; ``first``/``last`` differ."""

    def __init__(self, first: Any, last: Any) -> None:
        self.first = _Label(first)
        self.last = _Label(last)


class _Label:
    def __init__(self, text: Any) -> None:
        self.text = text
        self.timeouts: list[Any] = []

    async def inner_text(self, *, timeout: Any = None) -> str:
        self.timeouts.append(timeout)
        if isinstance(self.text, BaseException):
            raise self.text
        return self.text


class _QuotePane:
    def __init__(self, by_selector: dict[str, _Labels]) -> None:
        self.by_selector = by_selector
        self.asked: list[str] = []

    def locator(self, selector: str) -> _Labels:
        self.asked.append(selector)
        return self.by_selector[selector]


_LABEL = "flow-credit-cost-label.settings-credit-cost"


@pytest.mark.asyncio
async def test_extend_quote_is_the_last_label() -> None:
    labels = _Labels("Generating will use 99 credits", "Generating will use 12 credits")
    pane = _QuotePane({mx.EXTEND_CREDIT_QUOTE: labels})
    assert await mx._read_extend_credit_quote(pane) == 12
    assert labels.last.timeouts == [2000]


@pytest.mark.asyncio
@pytest.mark.parametrize("primary", [PlaywrightError("gone"), "no number here", "1 or 2"])
async def test_extend_quote_falls_back_to_any_cost_label(primary: Any) -> None:
    pane = _QuotePane(
        {
            mx.EXTEND_CREDIT_QUOTE: _Labels("unused", primary),
            "flow-credit-cost-label": _Labels("unused", "18 credits"),
        }
    )
    assert await mx._read_extend_credit_quote(pane) == 18
    assert pane.asked == [mx.EXTEND_CREDIT_QUOTE, "flow-credit-cost-label"]


@pytest.mark.asyncio
async def test_extend_quote_that_no_label_gives_is_none() -> None:
    pane = _QuotePane(
        {
            mx.EXTEND_CREDIT_QUOTE: _Labels("unused", PlaywrightError("gone")),
            "flow-credit-cost-label": _Labels("unused", ""),
        }
    )
    assert await mx._read_extend_credit_quote(pane) is None


@pytest.mark.asyncio
async def test_extend_quote_lets_a_non_playwright_error_through() -> None:
    pane = _QuotePane({mx.EXTEND_CREDIT_QUOTE: _Labels("unused", RuntimeError("boom"))})
    with pytest.raises(RuntimeError, match="boom"):
        await mx._read_extend_credit_quote(pane)


def _settings_page(labels: _Labels) -> tuple[Any, list[str]]:
    asked: list[str] = []

    def page_locator(selector: str) -> Any:
        asked.append(selector)
        return _QuotePane({_LABEL: labels})

    return SimpleNamespace(locator=page_locator), asked


@pytest.mark.asyncio
async def test_settings_quote_without_a_cap_reads_nothing() -> None:
    page = SimpleNamespace(locator=lambda _s: pytest.fail("no cap, no read"))
    assert await _mc_settings_pane._read_credit_quote(page, None) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_settings_quote_is_the_first_label_of_the_open_pane() -> None:
    labels = _Labels("Generating will use 12 credits", "Generating will use 99 credits")
    page, asked = _settings_page(labels)
    assert await _mc_settings_pane._read_credit_quote(page, 20) == 12  # type: ignore[arg-type]
    assert asked == [".cdk-overlay-pane flow-prompt-box-settings"]
    assert labels.first.timeouts == [2000]


@pytest.mark.asyncio
@pytest.mark.parametrize("first", [PlaywrightError("gone"), "no number here"])
async def test_settings_quote_that_cannot_be_read_fails_the_cap_closed(first: Any) -> None:
    page, _ = _settings_page(_Labels(first, "Generating will use 1 credits"))
    with pytest.raises(CreditCapExceededError) as caught:
        await _mc_settings_pane._read_credit_quote(page, 20)  # type: ignore[arg-type]
    assert caught.value.quoted is None
    assert caught.value.route == "flow.google.com settings pane"


@pytest.mark.asyncio
async def test_settings_quote_over_the_cap_is_refused() -> None:
    page, _ = _settings_page(_Labels("Generating will use 30 credits", "x"))
    with pytest.raises(CreditCapExceededError) as caught:
        await _mc_settings_pane._read_credit_quote(page, 20)  # type: ignore[arg-type]
    assert caught.value.quoted == 30


# --- edge cases of both sliders and both quote readers ---------------------------------


@pytest.mark.asyncio
async def test_settings_quote_honours_a_zero_credit_cap() -> None:
    page, _ = _settings_page(_Labels("0 credits", "80 credits"))
    assert await _mc_settings_pane._read_credit_quote(page, 0) == 0  # type: ignore[arg-type]

    page, _ = _settings_page(_Labels("1 credit", "0 credits"))
    with pytest.raises(CreditCapExceededError) as caught:
        await _mc_settings_pane._read_credit_quote(page, 0)  # type: ignore[arg-type]
    assert caught.value.quoted == 1
    assert caught.value.max_credits == 0


@pytest.mark.asyncio
async def test_extend_quote_prefers_the_settings_label_over_any_cost_label() -> None:
    pane = _QuotePane(
        {
            "flow-credit-cost-label.settings-credit-cost": _Labels("unused", "18 credits"),
            "flow-credit-cost-label": _Labels("unused", "120 credits"),
        }
    )
    assert await mx._read_extend_credit_quote(pane) == 18
    assert pane.asked == ["flow-credit-cost-label.settings-credit-cost"]


@pytest.mark.asyncio
async def test_extend_quote_fallback_gets_the_full_read_timeout() -> None:
    primary = _Labels("unused", PlaywrightError("missing"))
    fallback = _Labels("unused", "12 credits")
    pane = _QuotePane({_LABEL: primary, "flow-credit-cost-label": fallback})
    assert await mx._read_extend_credit_quote(pane) == 12
    assert primary.last.timeouts == [2000]
    assert fallback.last.timeouts == [2000]


@pytest.mark.asyncio
async def test_extend_slider_that_unmounts_after_the_wait_is_ambiguous() -> None:
    slider = _Slider(value=4, minimum="3", maximum="10", count=0)
    slider.get_attribute = AsyncMock()  # type: ignore[method-assign]
    with pytest.raises(UiSelectorDriftError, match="extend duration slider ambiguous"):
        await _set_extend(slider, 5)
    slider.get_attribute.assert_not_awaited()
    assert slider.keys == []


def _settle_watch(monkeypatch: pytest.MonkeyPatch, slider: _Slider, keys: list[str]) -> AsyncMock:
    """Patch ``asyncio.sleep``: the 0.2 s settle must come after every key, before any read."""

    async def settle(delay: float) -> None:
        assert delay == 0.2
        assert slider.keys == keys

    sleep = AsyncMock(side_effect=settle)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    real_value = slider.input_value

    async def value() -> str:
        if slider.keys:
            sleep.assert_awaited_once_with(0.2)
        return await real_value()

    slider.input_value = value  # type: ignore[method-assign]
    return sleep


@pytest.mark.asyncio
async def test_extend_slider_settles_after_the_keys_before_either_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slider = _Slider(value=4, minimum="3", maximum="10")
    sleep = _settle_watch(monkeypatch, slider, ["Home", *["ArrowRight"] * 3])

    def text(s: _Slider) -> str:
        sleep.assert_awaited_once_with(0.2)
        return f"Duration\n{s.value} s"

    assert await _set_extend(slider, 6, text) is True


@pytest.mark.asyncio
async def test_omni_slider_settles_after_the_keys_before_either_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slider = _omni()
    sleep = _settle_watch(monkeypatch, slider, ["Home", *["ArrowRight"] * 5])

    def text(s: _Slider) -> str:
        sleep.assert_awaited_once_with(0.2)
        return f"{s.value + 3}s"

    await _select_omni(slider, 8, value_text=text)


@pytest.mark.asyncio
async def test_omni_waits_for_the_menu_to_hide_before_the_slider_shows() -> None:
    seen: list[tuple[str, str, int]] = []

    async def menu_wait(*, state: str, timeout: int) -> None:
        seen.append(("menu", state, timeout))

    async def slider_wait(*, state: str, timeout: int) -> None:
        seen.append(("slider", state, timeout))

    slider = _omni()
    slider.wait_for = slider_wait  # type: ignore[method-assign]
    component = _Component(slider, text=lambda _s: "", value_text=lambda s: f"{s.value + 3}s")
    page = SimpleNamespace(
        locator=lambda _selector: SimpleNamespace(first=SimpleNamespace(wait_for=menu_wait))
    )
    await MigratedComposer()._select_omni12_duration(page, _pane(component), 8)  # type: ignore[arg-type]
    assert seen == [("menu", "hidden", 4000), ("slider", "visible", 4000)]


@pytest.mark.asyncio
async def test_omni_slider_wait_lets_a_cancellation_through() -> None:
    slider = _omni(wait_error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await _select_omni(slider, 8)
    assert slider.keys == []


@pytest.mark.asyncio
async def test_extend_slider_waits_up_to_four_seconds_for_the_input() -> None:
    slider = _Slider(value=4, minimum="3", maximum="10")
    slider.wait_for = AsyncMock()  # type: ignore[method-assign]
    await _set_extend(slider, 6)
    slider.wait_for.assert_awaited_once_with(state="visible", timeout=4000)


@pytest.mark.asyncio
async def test_extend_slider_disabled_at_the_requested_duration_is_still_refused() -> None:
    slider = _Slider(value=6, minimum="3", maximum="10", enabled=False)
    with pytest.raises(ConfigurationError, match="6s is not offered"):
        await _set_extend(slider, 6)
    assert slider.keys == []


@pytest.mark.asyncio
async def test_omni_slider_label_right_but_index_stale_is_drift() -> None:
    slider = _omni(value=4)
    slider.press = AsyncMock()  # type: ignore[method-assign]
    with pytest.raises(UiSelectorDriftError, match="did not bind 8s: index=4"):
        await _select_omni(slider, 8, value_text=lambda _s: "8s")


@pytest.mark.asyncio
async def test_extend_slider_label_right_but_value_stale_is_drift() -> None:
    slider = _Slider(value=4, minimum="3", maximum="10")
    slider.press = AsyncMock()  # type: ignore[method-assign]
    with pytest.raises(UiSelectorDriftError, match="did not bind 6s"):
        await _set_extend(slider, 6, lambda _s: "6 s")


@pytest.mark.asyncio
async def test_extend_slider_label_with_a_longer_number_ending_in_it_is_drift() -> None:
    slider = _Slider(value=4, minimum="3", maximum="10")
    with pytest.raises(UiSelectorDriftError, match="did not bind 6s"):
        await _set_extend(slider, 6, lambda _s: "Duration\n16 s")


@pytest.mark.asyncio
async def test_extend_slider_recheck_maps_only_playwright_errors_to_a_mismatch() -> None:
    slider = _Slider(value=6, minimum="3", maximum="10")
    slider.input_value = AsyncMock(side_effect=PlaywrightError("detached"))  # type: ignore[method-assign]
    pane = _pane(_extend_component(slider))
    assert await mx._slider_shows(pane, 6) is False

    slider.input_value.side_effect = RuntimeError("unexpected")
    with pytest.raises(RuntimeError, match="unexpected"):
        await mx._slider_shows(pane, 6)


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["page", "pane"])
async def test_settings_quote_whose_locator_raises_fails_the_cap_closed(where: str) -> None:
    def broken(_selector: str) -> Any:
        raise PlaywrightError("target closed")

    pane = SimpleNamespace(locator=broken)
    page = SimpleNamespace(locator=broken if where == "page" else (lambda _s: pane))
    with pytest.raises(CreditCapExceededError) as caught:
        await _mc_settings_pane._read_credit_quote(page, 20)  # type: ignore[arg-type]
    assert caught.value.quoted is None
    assert caught.value.route == "flow.google.com settings pane"


@pytest.mark.asyncio
async def test_extend_quote_whose_locator_raises_falls_back() -> None:
    fallback = _QuotePane({"flow-credit-cost-label": _Labels("unused", "18 credits")})

    def locator(selector: str) -> Any:
        if selector == mx.EXTEND_CREDIT_QUOTE:
            raise PlaywrightError("target closed")
        return fallback.locator(selector)

    assert await mx._read_extend_credit_quote(SimpleNamespace(locator=locator)) == 18
