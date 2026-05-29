from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from gflow_cli.config import get_settings
from gflow_cli.errors import ConfigurationError

from .internal_chromium import InternalChromiumStrategy
from .real_chrome import RealChromeStrategy

if TYPE_CHECKING:
    from .base import AuthStrategy

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
        timeout = get_settings().auth_login_timeout
        # T2.4: auto mode probes for Real Chrome; falls back to internal if missing.
        if mode == "auto":
            if self._is_chrome_available():
                return RealChromeStrategy(timeout_seconds=timeout)
            logger.warning(
                "chrome_missing_falling_back_to_internal",
                reason="Google Chrome was not detected on this system.",
            )
            return InternalChromiumStrategy(timeout_seconds=timeout)

        if mode not in self._strategies:
            msg = f"Unknown auth browser mode '{mode}'. Supported: auto, chrome, internal."
            raise ConfigurationError(
                msg,
            )

        if mode == "chrome":
            if not self._is_chrome_available():
                msg = "Chrome binary not found. Install Google Chrome or use '--browser internal'."
                raise ConfigurationError(
                    msg,
                )
            return RealChromeStrategy(timeout_seconds=timeout)

        return InternalChromiumStrategy(timeout_seconds=timeout)

    def _is_chrome_available(self) -> bool:
        """Probe for Real Chrome via browser_manager or Playwright."""
        from gflow_cli.browser_manager import is_chrome_available

        return is_chrome_available()
