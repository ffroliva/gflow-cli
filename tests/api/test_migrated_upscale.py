"""Unit tests for migrated_upscale (flow.google.com)."""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.api.image_upscale import TargetResolution
from gflow_cli.api.transports.migrated_upscale import upscale_image_migrated
from gflow_cli.errors import (
    TransportTimeoutError,
    UiSelectorDriftError,
    UpscaleUnavailableError,
    WireFormatError,
)

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_PNG_B64 = base64.b64encode(_PNG_BYTES).decode("ascii")


def _make_sprcad_body(b64_data: str) -> str:
    inner = json.dumps([["metadata"], b64_data])
    row = json.dumps([["wrb.fr", "SPrCad", inner]])
    return ")]}'\n\n1234\n" + row


def _make_sprcad_error_body() -> str:
    status = [7, None, [["type.googleapis.com/google.rpc.ErrorInfo", ["FAILED"]]]]
    row = json.dumps([["wrb.fr", "SPrCad", None, None, None, status]])
    return ")]}'\n\n1234\n" + row


@pytest.mark.asyncio
async def test_migrated_upscale_2k_happy_path() -> None:
    page = MagicMock()
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.evaluate = AsyncMock()

    tile_img = MagicMock()
    tile_img.click = AsyncMock()

    download_btn = MagicMock()
    download_btn.click = AsyncMock()

    btn_2k = MagicMock()
    btn_2k.is_disabled = AsyncMock(return_value=False)
    btn_2k.get_attribute = AsyncMock(return_value=None)

    btn_4k = MagicMock()
    btn_4k.is_disabled = AsyncMock(return_value=True)
    btn_4k.get_attribute = AsyncMock(return_value="true")

    async def wait_for_selector(sel, **kwargs):
        if "img[" in sel or "flow-image-tile" in sel:
            return tile_img
        if "download" in sel:
            return download_btn
        if "2K" in sel:
            return btn_2k
        if "4K" in sel:
            return btn_4k
        return None

    page.wait_for_selector = AsyncMock(side_effect=wait_for_selector)

    response_handlers = []

    def on_func(event, handler):
        if event == "response":
            response_handlers.append(handler)

    def remove_func(event, handler):
        if event == "response" and handler in response_handlers:
            response_handlers.remove(handler)

    page.on = MagicMock(side_effect=on_func)
    page.remove_listener = MagicMock(side_effect=remove_func)

    async def on_click_2k():
        res = MagicMock()
        res.url = (
            "https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=SPrCad"
        )
        res.text = AsyncMock(return_value=_make_sprcad_body(_PNG_B64))
        for h in list(response_handlers):
            await h(res)

    btn_2k.click = AsyncMock(side_effect=on_click_2k)

    result = await upscale_image_migrated(
        page,
        project_id="263ce917-9e5a-4a07-8206-7e56a63bcdd4",
        media_id="31d7f80f-8cf0-47db-b730-e5e46bb0c316",
        target_resolution=TargetResolution.RES_2K,
        timeout_s=5.0,
    )

    assert result == _PNG_BYTES


@pytest.mark.asyncio
async def test_migrated_upscale_4k_disabled_raises_unavailable() -> None:
    page = MagicMock()
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()

    tile_img = MagicMock()
    tile_img.click = AsyncMock()

    download_btn = MagicMock()
    download_btn.click = AsyncMock()

    btn_2k = MagicMock()
    btn_2k.is_disabled = AsyncMock(return_value=False)
    btn_2k.get_attribute = AsyncMock(return_value=None)

    btn_4k = MagicMock()
    btn_4k.is_disabled = AsyncMock(return_value=True)  # Pro plan -> 4k is disabled
    btn_4k.get_attribute = AsyncMock(return_value="true")

    async def wait_for_selector(sel, **kwargs):
        if "img[" in sel or "flow-image-tile" in sel:
            return tile_img
        if "download" in sel:
            return download_btn
        if "2K" in sel:
            return btn_2k
        if "4K" in sel:
            return btn_4k
        return None

    page.wait_for_selector = AsyncMock(side_effect=wait_for_selector)

    with pytest.raises(UpscaleUnavailableError, match="4K upscale requires a Flow Ultra"):
        await upscale_image_migrated(
            page,
            project_id="263ce917-9e5a-4a07-8206-7e56a63bcdd4",
            media_id="31d7f80f-8cf0-47db-b730-e5e46bb0c316",
            target_resolution=TargetResolution.RES_4K,
            timeout_s=5.0,
        )


@pytest.mark.asyncio
async def test_migrated_upscale_missing_menu_item_raises_drift() -> None:
    page = MagicMock()
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()

    tile_img = MagicMock()
    tile_img.click = AsyncMock()

    download_btn = MagicMock()
    download_btn.click = AsyncMock()

    async def wait_for_selector(sel, **kwargs):
        if "img[" in sel:
            return tile_img
        if "download" in sel:
            return download_btn
        if "4K" in sel:
            raise PlaywrightTimeoutError("timeout")
        return None

    page.wait_for_selector = AsyncMock(side_effect=wait_for_selector)

    with pytest.raises(UiSelectorDriftError, match="menu item for 4K was not found"):
        await upscale_image_migrated(
            page,
            project_id="263ce917-9e5a-4a07-8206-7e56a63bcdd4",
            media_id="31d7f80f-8cf0-47db-b730-e5e46bb0c316",
            target_resolution=TargetResolution.RES_4K,
            timeout_s=5.0,
        )


@pytest.mark.asyncio
async def test_migrated_upscale_missing_tile_raises_wireformat() -> None:
    page = MagicMock()
    page.goto = AsyncMock()
    page.wait_for_selector = AsyncMock(side_effect=PlaywrightTimeoutError("timeout"))

    with pytest.raises(WireFormatError, match="Could not locate image tile"):
        await upscale_image_migrated(
            page,
            project_id="263ce917-9e5a-4a07-8206-7e56a63bcdd4",
            media_id="31d7f80f-8cf0-47db-b730-e5e46bb0c316",
            target_resolution=TargetResolution.RES_2K,
            timeout_s=5.0,
        )


@pytest.mark.asyncio
async def test_migrated_upscale_timeout_raises_transport_timeout() -> None:
    page = MagicMock()
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.evaluate = AsyncMock()
    page.on = MagicMock()
    page.remove_listener = MagicMock()

    tile_img = MagicMock()
    tile_img.click = AsyncMock()
    download_btn = MagicMock()
    download_btn.click = AsyncMock()
    btn_2k = MagicMock()
    btn_2k.is_disabled = AsyncMock(return_value=False)
    btn_2k.get_attribute = AsyncMock(return_value=None)
    btn_2k.click = AsyncMock()

    async def wait_for_selector(sel, **kwargs):
        if "img[" in sel:
            return tile_img
        if "download" in sel:
            return download_btn
        if "2K" in sel:
            return btn_2k
        return None

    page.wait_for_selector = AsyncMock(side_effect=wait_for_selector)

    with pytest.raises(TransportTimeoutError, match="Timed out after 0.01s"):
        await upscale_image_migrated(
            page,
            project_id="263ce917-9e5a-4a07-8206-7e56a63bcdd4",
            media_id="31d7f80f-8cf0-47db-b730-e5e46bb0c316",
            target_resolution=TargetResolution.RES_2K,
            timeout_s=0.01,
        )


@pytest.mark.asyncio
async def test_migrated_upscale_rpc_error_raises_wireformat() -> None:
    page = MagicMock()
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.evaluate = AsyncMock()

    tile_img = MagicMock()
    tile_img.click = AsyncMock()
    download_btn = MagicMock()
    download_btn.click = AsyncMock()
    btn_2k = MagicMock()
    btn_2k.is_disabled = AsyncMock(return_value=False)
    btn_2k.get_attribute = AsyncMock(return_value=None)

    async def wait_for_selector(sel, **kwargs):
        if "img[" in sel:
            return tile_img
        if "download" in sel:
            return download_btn
        if "2K" in sel:
            return btn_2k
        return None

    page.wait_for_selector = AsyncMock(side_effect=wait_for_selector)

    response_handlers = []

    def _on(evt, fn):
        if evt == "response":
            response_handlers.append(fn)

    page.on = MagicMock(side_effect=_on)
    page.remove_listener = MagicMock()

    async def on_click_2k():
        res = MagicMock()
        res.url = (
            "https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=SPrCad"
        )
        res.text = AsyncMock(return_value=_make_sprcad_error_body())
        for h in list(response_handlers):
            await h(res)

    btn_2k.click = AsyncMock(side_effect=on_click_2k)

    with pytest.raises(WireFormatError, match="SPrCad RPC refused"):
        await upscale_image_migrated(
            page,
            project_id="263ce917-9e5a-4a07-8206-7e56a63bcdd4",
            media_id="31d7f80f-8cf0-47db-b730-e5e46bb0c316",
            target_resolution=TargetResolution.RES_2K,
            timeout_s=5.0,
        )
