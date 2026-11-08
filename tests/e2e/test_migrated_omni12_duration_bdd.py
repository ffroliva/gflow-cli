"""Free live settings regression; no prompt, upload or Send.

GFLOW_CLI_E2E_PROFILE and GFLOW_CLI_E2E_PROJECT select the existing account/project.
Run with pytest -m e2e_auth tests/e2e/test_migrated_omni12_duration_bdd.py.
Wire evidence, which is separate from this settings test, lives in the dated spike.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports.migrated_composer import MigratedComposer, _duration
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel
from gflow_cli.config import Settings
from gflow_cli.errors import ConfigurationError

scenarios("../features/migrated_omni12_duration.feature")


@pytest.fixture
def duration_client_factory(e2e_profile_dir: Path) -> Callable[[], Any]:
    def factory() -> FlowApiClient:
        return FlowApiClient(
            profile_dir=e2e_profile_dir,
            settings=Settings(flow_host="flow.google.com", ui_mode="classic"),
            transport="ui_automation",
        )

    return factory


@given("an existing migrated Flow project for the duration probe", target_fixture="duration_world")
def project() -> dict[str, Any]:
    value = os.environ.get("GFLOW_CLI_E2E_PROJECT", "")
    if not value:
        pytest.skip("GFLOW_CLI_E2E_PROJECT must name an existing project")
    return {"project": value}


@when(parsers.parse("Omni 1.2 ingredients settings request {seconds:d} seconds"))
def apply(
    duration_world: dict[str, Any], seconds: int, duration_client_factory: Callable[[], Any]
) -> None:
    async def probe() -> None:
        async with duration_client_factory() as client:
            page = client._page  # noqa: SLF001 - live settings readback
            composer = MigratedComposer()
            await composer.ensure_editor(page, duration_world["project"])
            request = GenerateVideoRequest(
                prompt="unused: never submitted",
                mode=Mode.R2V,
                model=VideoModel.OMNI_1_2_FLASH,
                aspect=Aspect.PORTRAIT,
                duration=seconds,
                ref_names=("unused: settings only",),
            )
            try:
                try:
                    await composer.apply_video_settings(page, request)
                except ConfigurationError:
                    # A free account's slider stops at 10s; `checked` decides if that's it.
                    duration_world["refused"] = True
                pane = await composer._open_pane(page)  # noqa: SLF001
                try:
                    component = pane.locator("flow-duration-slider")
                    slider = component.locator('input[type="range"]')
                    duration_world["max"] = await slider.get_attribute("max")
                    duration_world["index"] = await slider.input_value()
                    duration_world["label"] = await component.locator(
                        ".duration-slider-value"
                    ).inner_text()
                finally:
                    await composer._close_pane(page, strict=True)  # noqa: SLF001
            finally:
                # Restore the safe base duration without requiring retired Omni 1.1.
                await composer.apply_video_settings(page, replace(request, duration=8))

    asyncio.run(probe())


@then(parsers.parse("the live duration slider is {seconds:d} seconds"))
def checked(duration_world: dict[str, Any], seconds: int) -> None:
    if duration_world.get("refused"):
        # Only a refusal past a free account's 10s maximum (slider max index 7) is correct.
        assert duration_world["max"] == "7"
        assert seconds > 10
        return
    assert duration_world["index"] == str(seconds - 3)
    assert _duration(f"{seconds}s").search(duration_world["label"])
