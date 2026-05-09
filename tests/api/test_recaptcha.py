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


class TestTokenMinter:
    async def test_mints_token_using_cached_site_key(self) -> None:
        page = AsyncMock()
        # First call discovers site key, second call mints token.
        page.evaluate.side_effect = ["site-key-X", "token-ABC"]
        minter = TokenMinter(page)
        token = await minter.mint("videoGen")
        assert token == "token-ABC"
        # Now call mint() again — site key should be cached, so only 1 more
        # evaluate call (mint), not 2 (discover + mint).
        page.evaluate.side_effect = ["token-DEF"]
        token2 = await minter.mint("videoGen")
        assert token2 == "token-DEF"
        assert page.evaluate.await_count == 3  # 1 discover + 2 mint

    async def test_mint_raises_when_evaluate_returns_empty(self) -> None:
        page = AsyncMock()
        page.evaluate.side_effect = ["site-key", ""]
        minter = TokenMinter(page)
        with pytest.raises(RecaptchaError, match="empty"):
            await minter.mint("videoGen")

    async def test_mint_wraps_evaluate_exception_as_recaptcha_error(self) -> None:
        page = AsyncMock()
        page.evaluate.side_effect = ["site-key", RuntimeError("grecaptcha not loaded")]
        minter = TokenMinter(page)
        with pytest.raises(RecaptchaError):
            await minter.mint("videoGen")
