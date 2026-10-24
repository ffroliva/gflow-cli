"""Contract tests for the #780 image submit gates.

- ``migrated_images_prefer`` decides the migrated route per request: servable
  t2i/i2i with a named project from a pre-navigation page go migrated (the
  #692 race); anything unported, project-less, or already inside an editor
  keeps the served host.
- The mint skip is ANDed with servability in ``_drive_images_generation`` so
  a page-URL-level True can never strand a labs run without its token.
- ``MigratedComposer.ensure_editor`` must clear the glue cookie banner
  before waiting for the settings trigger (incident 8dc6c020).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture()
def wip_src(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Import gflow_cli from THIS checkout, not the installed venv copy."""
    src = Path(__file__).resolve().parents[1] / "src"
    monkeypatch.syspath_prepend(str(src))
    return src


@pytest.mark.asyncio()
async def test_ensure_editor_dismisses_cookie_bar_before_ready_wait(wip_src):
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = MagicMock()
    page.url = "https://flow.google.com/project/p-1"
    page.goto = AsyncMock()

    composer = MigratedComposer()
    call_order: list[str] = []

    async def fake_cookie(page_: object) -> None:
        call_order.append("cookie")

    async def fake_trigger_wait(*a: object, **kw: object) -> None:
        call_order.append("trigger")

    trigger = MagicMock()
    trigger.wait_for = AsyncMock(side_effect=fake_trigger_wait)

    def factory(selector: str):
        loc = MagicMock()
        loc.first = trigger if selector == ".settings-trigger-button" else loc
        loc.wait_for = trigger.wait_for
        return loc

    page.locator = MagicMock(side_effect=factory)
    composer._dismiss_dialog = AsyncMock()  # type: ignore[method-assign]
    composer._dismiss_cookie_bar = fake_cookie  # type: ignore[method-assign]

    await composer.ensure_editor(page, "p-1", timeout_s=1)
    assert call_order == ["cookie", "trigger"]


@pytest.mark.asyncio()
async def test_cookie_reject_button_dismisses_the_bar(wip_src):
    from playwright.async_api import async_playwright

    from gflow_cli.api.transports.migrated_composer import (
        COOKIE_BAR,
        COOKIE_BAR_REJECT,
        MigratedComposer,
    )

    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - depends on local browser install
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        try:
            page = await browser.new_page()
            await page.set_content(
                """
                <div class="glue-cookie-notification-bar">
                  <button class="glue-cookie-notification-bar__reject"
                          onclick="this.parentElement.remove()">No thanks</button>
                </div>
                """
            )
            assert await page.locator(COOKIE_BAR).count() == 1
            assert await page.locator(COOKIE_BAR_REJECT).count() == 1

            await MigratedComposer()._dismiss_cookie_bar(page)  # noqa: SLF001

            assert await page.locator(COOKIE_BAR).count() == 0
        finally:
            await browser.close()


class _SelectorDriftImageTransport:
    def __init__(self) -> None:
        self.calls = 0

    def uses_page_owned_image_recaptcha(self) -> bool:
        return True

    async def generate_images(self, **_: Any) -> list[Any]:
        self.calls += 1
        from gflow_cli.errors import UiSelectorDriftError

        raise UiSelectorDriftError(detail="migrated host: deterministic selector drift")


async def test_client_does_not_retry_typed_selector_drift():
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.errors import UiSelectorDriftError

    transport = _SelectorDriftImageTransport()
    client = FlowApiClient.__new__(FlowApiClient)
    client.transport = transport  # type: ignore[assignment]

    with pytest.raises(UiSelectorDriftError):
        await client._drive_images_generation(  # noqa: SLF001
            project_id="p-1",
            req=GenerateImageRequest(prompt="a blue cup"),
            recaptcha_action="imageGeneration",
        )

    assert transport.calls == 1


@pytest.mark.asyncio()
async def test_pre_submit_gate_rejects_an_unexpected_pointer_blocker(wip_src):
    from playwright.async_api import async_playwright

    from gflow_cli.api.transports.migrated_composer import MigratedComposer
    from gflow_cli.errors import UiSelectorDriftError

    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - depends on local browser install
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        try:
            page = await browser.new_page()
            await page.set_content(
                """
                <style>
                  #submit { position: absolute; left: 40px; top: 40px; width: 140px; height: 48px; }
                  #blocker { position: absolute; left: 40px; top: 40px; width: 140px; height: 48px;
                            z-index: 10; background: rgba(0, 0, 0, 0.1); }
                </style>
                <button id="submit"><mat-icon>arrow_forward</mat-icon></button>
                <div id="blocker"></div>
                """
            )

            with pytest.raises(UiSelectorDriftError, match="pointer|blocker"):
                await MigratedComposer()._pre_submit_gate(page)  # noqa: SLF001
        finally:
            await browser.close()


def _plain_request():
    from gflow_cli.api.image import GenerateImageRequest

    return GenerateImageRequest(prompt="a blue cup")


def test_migrated_images_prefer_servable_request_from_bootstrap(wip_src):
    from gflow_cli.api.transports.migrated_composer import migrated_images_prefer

    assert (
        migrated_images_prefer(
            _plain_request(),
            page_url="https://labs.google/fx/tools/flow?hl=en",
            project_id="p-1",
        )
        is True
    )


def test_migrated_images_prefer_refuses_unported_forms(wip_src):
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.transports.migrated_composer import migrated_images_prefer

    base = "https://labs.google/fx/tools/flow?hl=en"
    assert (
        migrated_images_prefer(
            GenerateImageRequest(prompt="x", reference_entities=("e1",)),
            page_url=base,
            project_id="p-1",
        )
        is False
    )
    assert (
        migrated_images_prefer(
            GenerateImageRequest(prompt="x", instructions=None)
            if False
            else GenerateImageRequest(prompt="x", reference_entities=("e2",)),
            page_url=base,
            project_id="p-1",
        )
        is False
    )


def test_migrated_images_prefer_keeps_project_less_runs_on_labs(wip_src):
    from gflow_cli.api.transports.migrated_composer import migrated_images_prefer

    # Pins the predicate's totality only. No production caller passes None:
    # `generate_image` creates the project BEFORE the transport routes, so a
    # project-less CLI run reaches the router with a real id and is served by
    # the migrated composer (#891 point 2 — the 0.78.0 changelog claim that such
    # runs "keep the served host" was never true for the path users take).
    assert (
        migrated_images_prefer(
            _plain_request(),
            page_url="https://labs.google/fx/tools/flow?hl=en",
            project_id=None,
        )
        is False
    )


def test_migrated_images_prefer_follows_the_served_editor(wip_src):
    from gflow_cli.api.transports.migrated_composer import migrated_images_prefer

    # Already inside an editor the served host rules — assuming migrated here
    # would skip the mint a labs editor genuinely needs.
    assert (
        migrated_images_prefer(
            _plain_request(),
            page_url="https://labs.google/fx/en/tools/flow/project/p-1",
            project_id="p-1",
        )
        is False
    )


class _SelectorDriftImageTransport:
    def __init__(self) -> None:
        self.calls = 0

    def uses_page_owned_image_recaptcha(self) -> bool:
        return True

    async def generate_images(self, **_: Any) -> list[Any]:
        self.calls += 1
        from gflow_cli.errors import UiSelectorDriftError

        raise UiSelectorDriftError(detail="migrated host: deterministic selector drift")


def _drive_client(transport: Any):
    from gflow_cli.api.client import FlowApiClient

    client = FlowApiClient.__new__(FlowApiClient)
    client.transport = transport  # type: ignore[assignment]
    return client


async def test_drive_never_mints_for_a_page_owning_transport():
    """#891: the UI transport never reads a client-minted token (Flow's page mints
    its own on click), so the client must not mint for it -- even for a form the
    migrated composer refuses. The old pre-mint ran on whatever page the pool
    handed out; after a successful migrated run that is `about:blank`, where the
    mint raises a misleading RecaptchaError instead of the transport's exit 36."""
    from unittest.mock import AsyncMock

    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.errors import UiSelectorDriftError

    transport = _SelectorDriftImageTransport()
    client = _drive_client(transport)
    client._mint_recaptcha_token = AsyncMock(return_value="tok")  # type: ignore[method-assign]

    with pytest.raises(UiSelectorDriftError):
        await client._drive_images_generation(  # noqa: SLF001
            project_id="p-1",
            req=GenerateImageRequest(prompt="a blue cup", reference_entities=("e1",)),
            recaptcha_action="imageGeneration",
        )

    client._mint_recaptcha_token.assert_not_awaited()  # type: ignore[attr-defined]
    assert transport.calls == 1


async def test_drive_skips_mint_for_servable_request():
    from unittest.mock import AsyncMock

    from gflow_cli.errors import UiSelectorDriftError

    transport = _SelectorDriftImageTransport()
    client = _drive_client(transport)
    client._mint_recaptcha_token = AsyncMock(return_value="tok")  # type: ignore[method-assign]

    with pytest.raises(UiSelectorDriftError):
        await client._drive_images_generation(  # noqa: SLF001
            project_id="p-1",
            req=_plain_request(),
            recaptcha_action="imageGeneration",
        )

    client._mint_recaptcha_token.assert_not_awaited()  # type: ignore[attr-defined]
    assert transport.calls == 1
