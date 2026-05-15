from __future__ import annotations

import structlog

from gflow_cli.errors import ConfigurationError

from .base import AuthStrategy
from .internal_chromium import InternalChromiumStrategy
from .real_chrome import RealChromeStrategy

logger = structlog.get_logger(__name__)


class AuthStrategyFactory:
    """Registry and factory for authentication strategies."""

    def __init__(self) -> None:
        self._strategies: dict[str, type[AuthStrategy]] = {
            "chrome": RealChromeStrategy,
            "internal": InternalChromiumStrategy,
        }

    def create(self, mode: str) -> AuthStrategy:
        """Create an authentication strategy based on the requested mode and system state."""
        # T2.4: auto mode probes for Real Chrome; falls back to internal if missing.
        if mode == "auto":
            if self._is_chrome_available():
                return RealChromeStrategy()
            logger.warning(
                "chrome_missing_falling_back_to_internal",
                reason="Google Chrome was not detected on this system.",
            )
            return InternalChromiumStrategy()

        strategy_cls = self._strategies.get(mode)
        if not strategy_cls:
            raise ConfigurationError(
                f"Unknown auth browser mode '{mode}'. Supported: auto, chrome, internal."
            )

        if mode == "chrome" and not self._is_chrome_available():
            raise ConfigurationError(
                "Chrome binary not found. Install Google Chrome or use '--browser internal'."
            )

        return strategy_cls()

    def _is_chrome_available(self) -> bool:
        """Probe for Real Chrome via browser_manager or Playwright."""
        from gflow_cli.browser_manager import is_chrome_available

        return is_chrome_available()
