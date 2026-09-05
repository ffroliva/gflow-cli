"""#673 — the labs client must not mint a reCAPTCHA token on the migrated host.

On a moved account the pool's bootstrap page is ``flow.google.com/`` (the project
grid), which carries no ``recaptcha/enterprise.js``. ``_mint_recaptcha_token`` ran
before the transport's migration guards could, so ``discover_site_key`` raised a
bare ``RecaptchaError`` (exit 1, "unexpected") instead of the distinct exit 36.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from gflow_cli.api import client as client_mod
from gflow_cli.api.client import FlowApiClient
from gflow_cli.errors import FlowHostMigratedError


def _client_on(url: str) -> FlowApiClient:
    c = FlowApiClient.__new__(FlowApiClient)
    c._page_queue = None  # test affordance: _checkout_page returns _page directly
    c._page = SimpleNamespace(url=url)  # type: ignore[assignment]
    return c


class _NeverMint:
    def __init__(self, *a: Any, **k: Any) -> None:
        pytest.fail("TokenMinter must not be constructed on the migrated host")


class _FakeMinter:
    def __init__(self, page: Any, **_: Any) -> None:
        self.page = page

    async def mint(self, action: str) -> str:
        return f"tok-{action}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    ["https://flow.google.com/", "https://flow.google.com/project/abc-123?pli=1"],
)
async def test_mint_on_migrated_host_exits_36_before_touching_recaptcha(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setattr(client_mod, "TokenMinter", _NeverMint)
    c = _client_on(url)
    with pytest.raises(FlowHostMigratedError) as info:
        await c._mint_recaptcha_token("IMAGE_GENERATION")
    assert "flow.google.com" in info.value.detail


@pytest.mark.asyncio
async def test_mint_on_labs_host_still_mints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod, "TokenMinter", _FakeMinter)
    c = _client_on("https://labs.google/fx/en/tools/flow")
    assert await c._mint_recaptcha_token("IMAGE_GENERATION") == "tok-IMAGE_GENERATION"


@pytest.mark.asyncio
async def test_mint_checks_the_page_back_in_after_the_bail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bail raises inside the checkout/checkin bracket; the page must not leak."""
    monkeypatch.setattr(client_mod, "TokenMinter", _NeverMint)
    c = _client_on("https://flow.google.com/")
    returned: list[Any] = []
    monkeypatch.setattr(c, "_checkin_page", lambda page: returned.append(page))
    with pytest.raises(FlowHostMigratedError):
        await c._mint_recaptcha_token("IMAGE_GENERATION")
    assert returned == [c._page]
