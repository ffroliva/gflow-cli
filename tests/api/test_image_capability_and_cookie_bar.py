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

import json
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
async def test_cookie_selector_is_button_scoped_and_dismisses_both_variants(wip_src):
    from playwright.async_api import async_playwright

    from gflow_cli.api.transports.migrated_composer import (
        COOKIE_BAR,
        COOKIE_BAR_BUTTONS,
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
                <div id="glue-cookie-notification-bar-1">
                  <button onclick="this.parentElement.remove()">Agree</button>
                </div>
                <div class="glue-cookie-notification-bar">
                  <button onclick="this.parentElement.remove()">No thanks</button>
                </div>
                """
            )
            buttons = page.locator(COOKIE_BAR_BUTTONS)
            assert await buttons.count() == 2
            assert await buttons.evaluate_all("els => els.map(el => el.tagName)") == [
                "BUTTON",
                "BUTTON",
            ]
            assert await page.locator(COOKIE_BAR).count() == 2

            await MigratedComposer()._dismiss_cookie_bar(page)  # noqa: SLF001

            assert await page.locator(COOKIE_BAR).count() == 0
        finally:
            await browser.close()


@pytest.mark.asyncio()
async def test_ui_failure_artifacts_redact_query_and_preserve_structural_state(
    wip_src, tmp_path: Path
):
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = MagicMock()
    page.url = "https://flow.google.com/project/p-1?auth=secret"
    page.evaluate = AsyncMock(return_value={"overlays": [], "radiogroups": []})

    async def write_screenshot(path: str, *, full_page: bool) -> None:
        assert full_page is False
        Path(path).write_bytes(b"PNG")

    page.screenshot = AsyncMock(side_effect=write_screenshot)
    paths = await MigratedComposer(out_dir=tmp_path)._capture_ui_failure(  # noqa: SLF001
        page,
        phase="pre_submit_overlay_gate",
        selector=".cdk-overlay-pane:visible",
        error="overlay blocked",
    )

    assert len(paths) == 2
    assert paths[0].suffix == ".png" and paths[0].read_bytes() == b"PNG"
    payload = json.loads(paths[1].read_text(encoding="utf-8"))
    assert payload["url"] == "https://flow.google.com/project/p-1"
    assert "auth=secret" not in paths[1].read_text(encoding="utf-8")
    assert payload["contract"] == "migrated-angular-settings-v1"
    assert payload["snapshot"] == {"overlays": [], "radiogroups": []}


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

    # The labs driver auto-creates the project; the migrated composer needs
    # --project. Preferring migrated here would trade the auto-create path
    # for a ConfigurationError.
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


async def test_drive_mints_when_request_is_not_migrated_servable():
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

    client._mint_recaptcha_token.assert_awaited_once()  # type: ignore[attr-defined]
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
