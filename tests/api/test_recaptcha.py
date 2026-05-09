"""Tests for reCAPTCHA site-key discovery + token minting (mocked Playwright)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from flow_cli.api.recaptcha import (
    RecaptchaError,
    TokenMinter,  # noqa: F401 — imported to assert it is exported from the module
    discover_site_key,
)


class TestDiscoverSiteKey:
    async def test_extracts_render_param_from_enterprise_script(self) -> None:
        page = AsyncMock()
        page.evaluate.return_value = "fake-site-key-123"
        result = await discover_site_key(page)
        assert result == "fake-site-key-123"
        page.evaluate.assert_awaited_once()

    async def test_raises_when_evaluate_returns_none(self) -> None:
        page = AsyncMock()
        page.evaluate.return_value = None
        with pytest.raises(RecaptchaError, match="site key"):
            await discover_site_key(page)
