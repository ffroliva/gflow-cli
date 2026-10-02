"""reCAPTCHA Enterprise token minting via Playwright page.evaluate.

The site key is discovered from the page source (loaded by the persistent
context's bootstrap navigation in `FlowApiClient.__aenter__`). Tokens are
single-use, ~2 min expiry — minted per `generate_image()` call.

`TokenMinter` caches the discovered site key for the lifetime of one
FlowApiClient session.
"""

from __future__ import annotations

from typing import Any, Protocol, cast

from gflow_cli.errors import RecaptchaError

__all__ = ["RecaptchaError", "TokenMinter", "discover_site_key"]


class _PageLike(Protocol):
    """Minimal subset of playwright.async_api.Page we need.

    Defined as a Protocol so tests can pass mocks without importing Playwright.
    """

    async def evaluate(self, expression: str, arg: Any = None) -> Any: ...


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

    Raises `RecaptchaError` (#915) if the read itself fails, typically because a
    navigation destroyed the page's context mid-read (retryable), or if no
    recaptcha/enterprise.js tag carries a `render=<key>` query param. A missing key is
    retryable only on a web page: Flow injects the script after the document reports
    `complete`, so a Flow page read too early has none yet and a re-read finds it
    (spike arms E/F), while a non-web page such as `about:blank` never will.
    """
    try:
        key = await page.evaluate(_DISCOVER_SITE_KEY_JS)
    except Exception as exc:
        msg = f"reading the reCAPTCHA site key failed: {exc}"
        raise RecaptchaError(msg, retryable=True) from exc
    if not isinstance(key, str) or not key:
        # Name the page state, not a cause: "the editor failed to load" sent #891 the
        # wrong way on a pool page parked at about:blank.
        url = getattr(page, "url", None)
        on_web_page = isinstance(url, str) and url.startswith(("https://", "http://"))
        state = (
            "carries no reCAPTCHA Enterprise script yet (Flow injects it after the page loads)"
            if on_web_page
            else "is not a Flow page, so it has no reCAPTCHA Enterprise script"
        )
        msg = f"Could not discover the reCAPTCHA site key: the page the mint ran on {state}."
        raise RecaptchaError(msg, retryable=on_web_page)
    return key


class TokenMinter:
    """Mint reCAPTCHA tokens. Caches the site key for the session."""

    def __init__(self, page: _PageLike, *, mint_evaluate_kwargs: dict[str, Any] | None = None):
        self._page = page
        self._site_key: str | None = None
        # Extra kwargs for the execute-mint ``page.evaluate`` call. Patchright
        # needs ``isolated_context=False`` so the main-world ``grecaptcha`` global
        # is visible; Playwright passes ``{}``. See gflow_cli.api._engine.
        self._mint_evaluate_kwargs = mint_evaluate_kwargs or {}

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
            # Cast to a permissive callable so the optional patchright-only
            # ``isolated_context`` kwarg type-checks — the _PageLike Protocol
            # matches playwright's Page exactly and intentionally omits it.
            evaluate = cast("Any", self._page.evaluate)
            token = await evaluate(_EXECUTE_JS, [site_key, action], **self._mint_evaluate_kwargs)
        except Exception as exc:
            msg = (
                f"reCAPTCHA evaluate failed for action={action!r}: {exc}. "
                "Likely causes: grecaptcha not loaded, page navigated away, "
                "or Playwright timeout. Try GFLOW_CLI_HEADLESS=false."
            )
            # Spike arm D: a mint that lost a race with a navigation succeeds once the
            # page settles (3/3), so this one is worth a retry.
            raise RecaptchaError(msg, retryable=True) from exc
        if not isinstance(token, str) or not token:
            msg = (
                f"reCAPTCHA returned an empty token for action={action!r}. "
                "Likely causes: headless detection by Google, or the page "
                "navigated away before mint. Try GFLOW_CLI_HEADLESS=false."
            )
            raise RecaptchaError(msg)  # class default (not retryable) until it is observed
        return token
