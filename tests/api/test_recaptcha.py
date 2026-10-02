"""Tests for reCAPTCHA site-key discovery + token minting (mocked Playwright)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from gflow_cli.api.recaptcha import (
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


class TestTyped:
    """#915: a mint failure is a GFlowError, retryable only where the spike measured it."""

    async def test_is_a_domain_error_with_its_own_type_and_remediation(self) -> None:
        from gflow_cli.errors import GFlowError

        page = AsyncMock()
        page.evaluate.return_value = None
        with pytest.raises(GFlowError) as info:
            await discover_site_key(page)
        assert isinstance(info.value, RecaptchaError)
        assert info.value.problem_type == "https://gflow-cli.dev/errors/recaptcha-mint"
        assert info.value.remediation_hint

    async def test_missing_site_key_off_the_web_is_not_retryable(self) -> None:
        from gflow_cli.errors import is_retryable

        page = AsyncMock()
        page.url = "about:blank"
        page.evaluate.return_value = None
        with pytest.raises(RecaptchaError) as info:
            await discover_site_key(page)
        # Spike arm A: 3/3 identical on about:blank -- the page state, not timing (#891).
        assert is_retryable(info.value) is False
        assert "script tag layout" not in str(info.value)
        assert "not a Flow page" in str(info.value)

    async def test_missing_site_key_on_a_web_page_is_retryable(self) -> None:
        from gflow_cli.errors import is_retryable

        page = AsyncMock()
        page.url = "https://flow.google.com/project/p"
        page.evaluate.return_value = None
        with pytest.raises(RecaptchaError) as info:
            await discover_site_key(page)
        # Spike arms E/F: read 0.1-0.3 s into a load the key is absent (readyState may
        # already be "complete"); a re-read on the settled page found it, 4/4.
        assert is_retryable(info.value) is True
        assert "yet" in str(info.value)

    async def test_a_page_without_a_url_string_is_not_claimed_retryable(self) -> None:
        from gflow_cli.errors import is_retryable

        page = AsyncMock()  # a mock url attribute is not a str
        page.evaluate.return_value = None
        with pytest.raises(RecaptchaError) as info:
            await discover_site_key(page)
        assert is_retryable(info.value) is False

    async def test_a_site_key_evaluate_that_raises_is_typed_and_retryable(self) -> None:
        from gflow_cli.errors import is_retryable

        page = AsyncMock()
        page.evaluate.side_effect = RuntimeError("Execution context was destroyed")
        with pytest.raises(RecaptchaError) as info:
            await discover_site_key(page)
        assert is_retryable(info.value) is True
        assert isinstance(info.value.__cause__, RuntimeError)

    async def test_an_execute_failure_is_retryable(self) -> None:
        from gflow_cli.errors import is_retryable

        page = AsyncMock()
        page.evaluate.side_effect = ["site-key", RuntimeError("Execution context was destroyed")]
        with pytest.raises(RecaptchaError) as info:
            await TokenMinter(page).mint("videoGen")
        # Spike arm D: 3/3 raced mints failed, 3/3 re-mints on the settled page succeeded.
        assert is_retryable(info.value) is True

    async def test_an_empty_token_keeps_the_class_default(self) -> None:
        from gflow_cli.errors import is_retryable

        page = AsyncMock()
        page.evaluate.side_effect = ["site-key", ""]
        with pytest.raises(RecaptchaError) as info:
            await TokenMinter(page).mint("videoGen")
        assert is_retryable(info.value) is False  # unmeasured: no claim
