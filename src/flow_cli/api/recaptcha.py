"""reCAPTCHA Enterprise token minting via Playwright page.evaluate.

The site key is discovered from the page source (loaded by the persistent
context's bootstrap navigation in `FlowApiClient.__aenter__`). Tokens are
single-use, ~2 min expiry — minted per `generate_video()` call.

`TokenMinter` caches the discovered site key for the lifetime of one
FlowApiClient session.
"""

from __future__ import annotations

from typing import Any, Protocol


class _PageLike(Protocol):
    """Minimal subset of playwright.async_api.Page we need.

    Defined as a Protocol so tests can pass mocks without importing Playwright.
    """

    async def evaluate(self, expression: str, arg: Any = None) -> Any: ...


class RecaptchaError(RuntimeError):
    """Raised when reCAPTCHA token minting fails (script missing,
    Google refused to mint, etc.)."""


_DISCOVER_SITE_KEY_JS = """
() => {
    const scripts = document.querySelectorAll('script[src*="recaptcha/enterprise.js"]');
    for (const s of scripts) {
        const m = (s.getAttribute('src') || '').match(/[?&]render=([^&]+)/);
        if (m) return m[1];
    }
    return null;
}
"""

_EXECUTE_JS = """
async ([siteKey, action]) => {
    return await new Promise((resolve, reject) => {
        if (typeof grecaptcha === 'undefined' || !grecaptcha.enterprise) {
            return reject(new Error('grecaptcha.enterprise not loaded'));
        }
        grecaptcha.enterprise.ready(() => {
            grecaptcha.enterprise
                .execute(siteKey, { action })
                .then(resolve)
                .catch(reject);
        });
    });
}
"""


async def discover_site_key(page: _PageLike) -> str:
    """Read the reCAPTCHA Enterprise site key from the loaded page.

    Raises `RecaptchaError` if the recaptcha/enterprise.js script tag is
    missing or doesn't carry a `render=<key>` query param.
    """
    key = await page.evaluate(_DISCOVER_SITE_KEY_JS)
    if not isinstance(key, str) or not key:
        raise RecaptchaError(
            "Could not discover reCAPTCHA site key from the page. "
            "The Flow editor page may have failed to load, or the reCAPTCHA "
            "script tag layout has changed."
        )
    return key


class TokenMinter:
    """Mint reCAPTCHA tokens. Caches the site key for the session."""

    def __init__(self, page: _PageLike):
        self._page = page
        self._site_key: str | None = None

    async def site_key(self) -> str:
        if self._site_key is None:
            self._site_key = await discover_site_key(self._page)
        return self._site_key

    async def mint(self, action: str) -> str:
        """Mint a fresh reCAPTCHA Enterprise token for the given action.

        Tokens are single-use and expire in ~2 minutes — call this immediately
        before the API request that consumes the token.
        """
        site_key = await self.site_key()
        try:
            token = await self._page.evaluate(_EXECUTE_JS, [site_key, action])
        except Exception as exc:
            raise RecaptchaError(
                f"reCAPTCHA evaluate failed for action={action!r}: {exc}. "
                "Likely causes: grecaptcha not loaded, page navigated away, "
                "or Playwright timeout. Try FLOW_CLI_HEADLESS=false."
            ) from exc
        if not isinstance(token, str) or not token:
            raise RecaptchaError(
                f"reCAPTCHA returned an empty token for action={action!r}. "
                "Likely causes: headless detection by Google, or the page "
                "navigated away before mint. Try FLOW_CLI_HEADLESS=false."
            )
        return token
