"""#673 — the labs client must not mint a reCAPTCHA token on the migrated host.

On a moved account the pool's bootstrap page is ``flow.google.com/`` (the project
grid), which carries no ``recaptcha/enterprise.js``. ``_mint_recaptcha_token`` ran
before the transport's migration guards could, so ``discover_site_key`` raised a
bare ``RecaptchaError`` (exit 1, "unexpected") instead of the distinct exit 36.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs

from gflow_cli.api import client as client_mod
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.recaptcha import RecaptchaError
from gflow_cli.errors import FlowHostMigratedError


def _client_on(url: str) -> FlowApiClient:
    c = FlowApiClient.__new__(FlowApiClient)
    c._page_queue = None  # test affordance: _checkout_page returns _page directly
    c._page = SimpleNamespace(url=url)  # type: ignore[assignment]
    return c


class _NeverMint:
    def __init__(self, *a: Any, **k: Any) -> None:
        pytest.fail("TokenMinter must not be constructed on the migrated host")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    # `/project/<id>` DOES serve enterprise.js, so bailing there is the conservative
    # choice, not a necessity: no minting path is ported to the migrated host today.
    # This case is the tripwire for the day one is.
    "url",
    ["https://flow.google.com/", "https://flow.google.com/project/abc-123?pli=1"],
)
async def test_mint_on_migrated_host_exits_36_before_touching_recaptcha(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setattr(client_mod, "TokenMinter", _NeverMint)
    c = _client_on(url)
    # The bail raises inside the checkout/checkin bracket; the pool page must not leak.
    returned: list[Any] = []
    monkeypatch.setattr(c, "_checkin_page", returned.append)
    with pytest.raises(FlowHostMigratedError) as info:
        await c._mint_recaptcha_token("IMAGE_GENERATION")
    assert "flow.google.com" in info.value.detail
    assert returned == [c._page]


@pytest.mark.asyncio
async def test_mint_on_labs_host_still_mints(monkeypatch: pytest.MonkeyPatch) -> None:
    mint = AsyncMock(return_value="tok")
    monkeypatch.setattr(client_mod, "TokenMinter", lambda page, **_: SimpleNamespace(mint=mint))
    c = _client_on("https://labs.google/fx/en/tools/flow")
    assert await c._mint_recaptcha_token("IMAGE_GENERATION") == "tok"
    mint.assert_awaited_once_with("IMAGE_GENERATION")  # the action is threaded through


class _HopsDuringMint:
    """Flow's client-side handoff lands WHILE the token is being minted.

    #692: ``page.goto`` returns before the hop, and the bootstrap's
    ``await_url_settled`` is SKIPPED for a profile latched at ``NOT_REDIRECTED``
    (``client.py`` ``settle = cached != NOT_REDIRECTED``). So the guard can read a
    still-labs URL, wave the mint through, and only then does the page land on
    ``flow.google.com`` — where there is no ``recaptcha/enterprise.js``.
    """

    def __init__(self, page: Any, **_: Any) -> None:
        self._page = page

    async def mint(self, _action: str) -> str:
        self._page.url = "https://flow.google.com/"
        raise RecaptchaError("recaptcha/enterprise.js not found")


@pytest.mark.asyncio
async def test_mint_reclassifies_when_the_migrated_hop_lands_mid_mint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#692: a mint that fails because the page hopped mid-flight is exit 36, not exit 1.

    The reporter ran v0.69.0 — which HAS the #678 guard — and still got a bare
    ``RecaptchaError``, because that guard is a point-in-time read racing a
    client-side navigation. Re-checking on the failure path costs nothing when
    the mint succeeds and is robust regardless of when the hop lands.
    """
    monkeypatch.setattr(client_mod, "TokenMinter", _HopsDuringMint)
    c = _client_on("https://labs.google/fx/en/tools/flow")
    returned: list[Any] = []
    monkeypatch.setattr(c, "_checkin_page", returned.append)

    with pytest.raises(FlowHostMigratedError) as info:
        await c._mint_recaptcha_token("IMAGE_GENERATION")

    assert "flow.google.com" in info.value.detail
    assert returned == [c._page], "the pool page must not leak on the reclassified path"


class _FailsOnLabs:
    """A genuine labs-side reCAPTCHA failure — the page never hops."""

    def __init__(self, _page: Any, **_: Any) -> None: ...

    async def mint(self, _action: str) -> str:
        raise RecaptchaError("site key not discoverable")


@pytest.mark.asyncio
async def test_mint_failure_on_labs_still_raises_recaptcha_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reclassification must NOT swallow real reCAPTCHA failures.

    Turning every mint failure into exit 36 would hide the labs-side breakage
    this error exists to report.
    """
    monkeypatch.setattr(client_mod, "TokenMinter", _FailsOnLabs)
    c = _client_on("https://labs.google/fx/en/tools/flow")
    monkeypatch.setattr(c, "_checkin_page", lambda _p: None)

    with pytest.raises(RecaptchaError):
        await c._mint_recaptcha_token("IMAGE_GENERATION")


@pytest.mark.asyncio
async def test_mint_failure_off_migrated_host_records_where_the_page_was(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#692: when the re-check does NOT fire, log the URL that defeated it.

    The reporter's failure could not be reproduced locally — the hop wins the
    race on this machine and the guard classifies correctly. So the mint failing
    on a page that reads as *labs* is the discriminating observation, and without
    it every future report costs a round trip to the reporter. Emitting the URL
    here turns the next incident bundle into evidence on its own.
    """
    monkeypatch.setattr(client_mod, "TokenMinter", _FailsOnLabs)
    c = _client_on("https://labs.google/fx/en/tools/flow")
    monkeypatch.setattr(c, "_checkin_page", lambda _p: None)

    with capture_logs() as logs, pytest.raises(RecaptchaError):
        await c._mint_recaptcha_token("IMAGE_GENERATION")

    recorded = [e for e in logs if e.get("event") == "recaptcha_mint_failed_off_migrated_host"]
    assert len(recorded) == 1, f"expected the diagnostic, got {logs}"
    assert recorded[0]["url"] == "https://labs.google/fx/en/tools/flow"


class _ContextDestroyedDuringMint:
    """The mid-mint navigation destroys the execution context.

    #692 review finding: ``TokenMinter.mint`` guards only its SECOND evaluate.
    ``site_key()`` -> ``discover_site_key`` runs an unguarded
    ``page.evaluate`` (``recaptcha.py``), and ``TokenMinter`` is rebuilt per
    call so ``_site_key`` is always ``None`` — meaning the unguarded call runs
    every time. A hop mid-mint therefore surfaces as a RAW Playwright error,
    not ``RecaptchaError``, which is the likeliest shape of the reporter's
    failure and the one a ``except RecaptchaError`` net misses entirely.
    """

    def __init__(self, page: Any, **_: Any) -> None:
        self._page = page

    async def mint(self, _action: str) -> str:
        self._page.url = "https://flow.google.com/"
        raise RuntimeError("Execution context was destroyed, most likely because of a navigation")


@pytest.mark.asyncio
async def test_mint_reclassifies_a_non_recaptcha_failure_after_the_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any mint failure on a page that turns out to be migrated is exit 36."""
    monkeypatch.setattr(client_mod, "TokenMinter", _ContextDestroyedDuringMint)
    c = _client_on("https://labs.google/fx/en/tools/flow")
    returned: list[Any] = []
    monkeypatch.setattr(c, "_checkin_page", returned.append)

    with pytest.raises(FlowHostMigratedError):
        await c._mint_recaptcha_token("IMAGE_GENERATION")

    assert returned == [c._page], "the pool page must not leak on the reclassified path"


class _RaisesRuntimeOnLabs:
    def __init__(self, _page: Any, **_: Any) -> None: ...

    async def mint(self, _action: str) -> str:
        raise RuntimeError("some unrelated playwright failure")


@pytest.mark.asyncio
async def test_non_recaptcha_failure_on_labs_propagates_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Widening the net must not swallow or reshape unrelated failures."""
    monkeypatch.setattr(client_mod, "TokenMinter", _RaisesRuntimeOnLabs)
    c = _client_on("https://labs.google/fx/en/tools/flow")
    monkeypatch.setattr(c, "_checkin_page", lambda _p: None)

    with pytest.raises(RuntimeError, match="some unrelated playwright failure"):
        await c._mint_recaptcha_token("IMAGE_GENERATION")


class _DiesDuringMint:
    """Readable at the pre-mint guard, then the target dies mid-mint.

    A page that is dead from the start fails at the FIRST guard, which is
    pre-existing behaviour. The case this widened handler newly makes reachable
    is a page that classifies fine going in and whose ``url`` read raises on the
    way out.
    """

    def __init__(self) -> None:
        self._reads = 0

    @property
    def url(self) -> str:
        self._reads += 1
        if self._reads == 1:
            return "https://labs.google/fx/en/tools/flow"
        msg = "Target page, context or browser has been closed"
        raise RuntimeError(msg)


@pytest.mark.asyncio
async def test_a_failing_url_probe_never_displaces_the_real_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The migration re-check is a PROBE; it must never mask the original error.

    ``_common.flow_host_kind`` is total by construction for exactly this reason
    ("a probe error must never displace the real failure"), but the re-check
    reads ``page.url`` to get there — and that read can raise on a dead page.
    Widening the handler to ``except Exception`` made that reachable, so the
    probe is guarded and the caller still sees what actually went wrong.
    """
    monkeypatch.setattr(client_mod, "TokenMinter", _RaisesRuntimeOnLabs)
    c = FlowApiClient.__new__(FlowApiClient)
    c._page_queue = None
    c._page = _DiesDuringMint()  # type: ignore[assignment]
    monkeypatch.setattr(c, "_checkin_page", lambda _p: None)

    with pytest.raises(RuntimeError, match="some unrelated playwright failure"):
        await c._mint_recaptcha_token("IMAGE_GENERATION")
