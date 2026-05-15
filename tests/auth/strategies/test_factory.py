from __future__ import annotations

from unittest.mock import patch

import pytest

from gflow_cli.errors import ConfigurationError

# These don't exist yet, but we define the contract here.
# Implementation will happen in Task 1.2.
try:
    from gflow_cli.auth import AuthStrategyFactory, InternalChromiumStrategy, RealChromeStrategy
except ImportError:
    # We define placeholders for static analysis / pytest to load the file
    # even if the implementation is missing (RED phase).
    class AuthStrategyFactory:
        def create(self, mode: str) -> object:
            raise NotImplementedError("AuthStrategyFactory.create not implemented")

    class RealChromeStrategy:
        pass

    class InternalChromiumStrategy:
        pass


class TestAuthStrategyFactory:
    def test_returns_real_chrome_when_requested_and_available(self) -> None:
        factory = AuthStrategyFactory()
        with patch("gflow_cli.browser_manager.is_chrome_available", return_value=True, create=True):
            strategy = factory.create("chrome")
            assert isinstance(strategy, RealChromeStrategy)

    def test_falls_back_to_internal_chromium_in_auto_mode_when_chrome_missing(self) -> None:
        factory = AuthStrategyFactory()
        with patch(
            "gflow_cli.browser_manager.is_chrome_available", return_value=False, create=True
        ):
            strategy = factory.create("auto")
            assert isinstance(strategy, InternalChromiumStrategy)

    def test_returns_real_chrome_in_auto_mode_when_available(self) -> None:
        factory = AuthStrategyFactory()
        with patch("gflow_cli.browser_manager.is_chrome_available", return_value=True, create=True):
            strategy = factory.create("auto")
            assert isinstance(strategy, RealChromeStrategy)

    def test_raises_configuration_error_when_chrome_requested_but_missing(self) -> None:
        factory = AuthStrategyFactory()
        with patch(
            "gflow_cli.browser_manager.is_chrome_available", return_value=False, create=True
        ):
            with pytest.raises(ConfigurationError) as excinfo:
                factory.create("chrome")
            assert "Chrome binary not found" in str(excinfo.value)

    def test_returns_internal_chromium_when_explicitly_requested(self) -> None:
        factory = AuthStrategyFactory()
        strategy = factory.create("internal")
        assert isinstance(strategy, InternalChromiumStrategy)
