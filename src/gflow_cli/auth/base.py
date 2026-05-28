"""AuthStrategy Protocol — the abstraction every authentication strategy implements.

See docs/superpowers/specs/2026-05-15-auth-login-real-chrome-design.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path


class AuthStrategy(Protocol):
    """Protocol for authentication strategies.

    Implementations provide the mechanics for logging into Google Flow
    (e.g. via real Chrome, internal Chromium, etc).
    """

    name: str

    async def login(self, profile_dir: Path, headless: bool) -> None:
        """Execute the login flow.

        Args:
            profile_dir: The directory where the browser profile/session state
                         should be stored.
            headless: Whether to run the browser in headless mode. Note that
                      some strategies may ignore this if interaction is required.
        """
        ...
