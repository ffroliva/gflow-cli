"""The model-specific slider must bind exact seconds or refuse before Send."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.errors import ConfigurationError, UiSelectorDriftError


class Slider:
    def __init__(self, maximum: str = "12", enabled: bool = True, stuck: bool = False):
        self.maximum, self.enabled, self.stuck = maximum, enabled, stuck
        self.value = 5
        self.presses: list[str] = []
        self.first = self

    async def count(self):
        return 1

    async def wait_for(self, **kwargs):
        pass

    async def get_attribute(self, name):
        return {"min": "0", "max": self.maximum, "step": "1"}[name]

    async def is_enabled(self):
        return self.enabled

    async def input_value(self):
        return str(self.value)

    async def inner_text(self):
        return f"{self.value + 3} 秒"

    async def press(self, key):
        self.presses.append(key)
        if not self.stuck:
            self.value = 0 if key == "Home" else self.value + 1


async def select(slider, seconds):
    component = SimpleNamespace(locator=lambda _: slider)
    pane = SimpleNamespace(locator=lambda _: component)
    menu = SimpleNamespace(first=SimpleNamespace(wait_for=AsyncMock()))
    page = SimpleNamespace(locator=lambda _: menu)
    await MigratedComposer()._select_omni12_duration(page, pane, seconds)


@pytest.mark.parametrize("seconds", range(3, 16))
async def test_exact_seconds(seconds: int):
    slider = Slider()
    await select(slider, seconds)
    assert slider.value == seconds - 3


@pytest.mark.parametrize("maximum,enabled", [("7", True), ("12", False)])
async def test_not_offered_never_changes_control(maximum, enabled):
    slider = Slider(maximum, enabled)
    with pytest.raises(ConfigurationError):
        await select(slider, 15)
    assert slider.presses == []


async def test_changed_scale_is_not_guessed():
    with pytest.raises(UiSelectorDriftError):
        await select(Slider("15"), 15)


async def test_unbound_control_is_not_accepted():
    with pytest.raises(UiSelectorDriftError):
        await select(Slider(stuck=True), 3)
