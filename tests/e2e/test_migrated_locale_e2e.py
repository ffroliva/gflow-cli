"""$0 live proof that the migrated settings pane binds a duration in a translated UI (#963).

A Vietnamese pane labels the durations ``4 giây … 10 giây``, and the driver used to match
the English ``4s`` exactly, so every ``--duration`` run stopped at exit 11. The same pass
is served a notice-only consent bar with no reject button, which used to leave the bar
over the settings trigger (exit 23). This runs the shipped ``apply_video_settings`` under
``hl=vi`` and reads the bound radio back. Nothing is typed and nothing is submitted.

Evidence: docs/superpowers/spikes/2026-10-10-duration-locale-and-notice-only-cookie-bar.md

    GFLOW_CLI_E2E_PROFILE=<profile> GFLOW_CLI_E2E_PROJECT=<project-uuid> \\
        uv run pytest -m e2e_auth tests/e2e/test_migrated_locale_e2e.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gflow_cli.api.transports.migrated_composer import RADIO, MigratedComposer
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]


@pytest.mark.asyncio
async def test_e2e_duration_binds_in_a_vietnamese_pane(e2e_profile_dir: Path) -> None:
    project = os.environ.get("GFLOW_CLI_E2E_PROJECT", "").strip()
    if not project:
        pytest.skip("GFLOW_CLI_E2E_PROJECT must name an existing Flow project id")
    composer = MigratedComposer()
    transport = UiAutomationTransport()
    try:
        await transport.setup(e2e_profile_dir)
        page = transport._page  # noqa: SLF001 - the e2e drives the live page
        assert page is not None
        await page.set_extra_http_headers({"Accept-Language": "vi"})
        await composer.ensure_editor(page, project, timeout_s=45.0)
        vi_url = f"{page.url.split('?')[0]}?hl=vi"

        async def fresh_editor() -> None:
            # One pane open per document, as production does: re-opening the pane on the
            # same page load resolves `_open_pane` to a detached overlay with no groups.
            await page.goto(vi_url, wait_until="domcontentloaded")
            await composer.ensure_editor(page, project, timeout_s=45.0)

        # 4 then 8: proves a change of radio, and leaves the project on 8s, its default.
        for seconds in (4, 8):
            await fresh_editor()
            # Without this the test proves nothing about #963: an English pane passes.
            assert await page.evaluate("document.documentElement.lang") == "vi"
            request = GenerateVideoRequest(
                prompt="unused - nothing is submitted",
                mode=Mode.T2V,
                aspect=Aspect.LANDSCAPE,
                model=VideoModel.OMNI_FLASH,
                duration=seconds,
            )
            await composer.apply_video_settings(page, request)

            await fresh_editor()  # read back from a new document: the choice persisted
            pane = await composer._open_pane(page)  # noqa: SLF001 - read-back
            checked = [
                text.strip()
                for text in await pane.locator(f"{RADIO}[aria-checked='true']").all_text_contents()
            ]
            await composer._close_pane(page)  # noqa: SLF001
            assert f"{seconds} giây" in checked, checked
    finally:
        await transport.teardown()
