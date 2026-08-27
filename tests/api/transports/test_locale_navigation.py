"""Editor navigation uses the ACCOUNT's locale, never a guess (issue #580).

The defect: `_enter_editor` built its URL from a hardcoded `locale="en-US"` that
no caller ever overrode. On a pt-BR account that produced `/fx/en/...`, which Flow
redirects to `/fx/pt/...` AFTER `page.goto` has already returned — so the very next
DOM action ran against a page about to be navigated away.

These tests pin the contract at the seam. The timing property itself is not
unit-testable; it is the e2e gate.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from gflow_cli.api.transports import ui_automation as uia_mod
from gflow_cli.api.transports._common import await_url_settled
from gflow_cli.api.transports.base import TransportSetup
from gflow_cli.api.transports.ui_automation import UiAutomationTransport

PID = "2ddc3a33-97db-41a0-a0d3-7f9488b0d5a9"


class _FakePage:
    """Records goto() targets and satisfies the settle wait.

    ``wait_for_url`` MUST exist. Without it ``await_url_settled`` raises
    ``AttributeError``, swallows it, returns ``None`` — and the navigation tests
    pass without ever exercising the settle path. They would then pass against a
    stub implementation, which is no test at all.
    """

    def __init__(self, url: str = "https://labs.google/fx/pt/tools/flow") -> None:
        self.url = url
        self.goto = AsyncMock()
        self.wait_for_url = AsyncMock()


def _transport(account_locale: str | None) -> UiAutomationTransport:
    t = UiAutomationTransport()
    t.apply_setup(TransportSetup(account_locale=account_locale))
    return t


def test_apply_setup_stores_the_injected_locale() -> None:
    assert _transport("pt")._account_locale == "pt"


def test_transport_defaults_to_no_locale_not_en() -> None:
    """A fresh transport must not carry a guessed locale.

    The old code defaulted to "en-US"; that default WAS the bug.
    """
    assert UiAutomationTransport()._account_locale is None


@pytest.mark.asyncio
async def test_navigation_uses_the_account_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _transport("pt")
    page = _FakePage()
    monkeypatch.setattr(t, "_dismiss_blocking_overlays", AsyncMock())

    await t._enter_editor(page, None, project_id=PID)

    (url,), _ = page.goto.call_args
    assert url == f"https://labs.google/fx/pt/tools/flow/project/{PID}"
    assert "/fx/en/" not in url


@pytest.mark.asyncio
async def test_unresolved_locale_omits_the_segment(monkeypatch: pytest.MonkeyPatch) -> None:
    """No locale => bare URL. Never a guessed `en`.

    A bare URL still gets normalised by Flow, but it never sends the browser to a
    locale we invented and then have to be bounced back from.
    """
    t = _transport(None)
    page = _FakePage()
    monkeypatch.setattr(t, "_dismiss_blocking_overlays", AsyncMock())

    await t._enter_editor(page, None, project_id=PID)

    (url,), _ = page.goto.call_args
    assert url == f"https://labs.google/fx/tools/flow/project/{PID}"
    assert "/fx/en/" not in url


@pytest.mark.asyncio
async def test_settle_wait_runs_before_any_dom_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """The settle wait must precede overlay dismissal, not follow it.

    Ordering is the whole point: dismissing overlays on a page that is about to
    navigate is exactly the bug.
    """
    t = _transport("pt")
    page = _FakePage()
    order: list[str] = []

    async def _settle(_page: Any) -> str | None:
        order.append("settle")
        return None

    async def _dismiss(*_a: Any, **_k: Any) -> None:
        order.append("dismiss")

    monkeypatch.setattr(uia_mod, "await_url_settled", _settle)
    monkeypatch.setattr(t, "_dismiss_blocking_overlays", _dismiss)

    await t._enter_editor(page, None, project_id=PID)

    assert order == ["settle", "dismiss"]


@pytest.mark.asyncio
async def test_settle_wait_is_non_fatal() -> None:
    """A page that raises on .url must not break navigation.

    Best-effort by construction — a settle we cannot confirm must never turn a
    cosmetic defect into an outage.
    """

    class _Exploding:
        """Fails inside the wait itself — the realistic failure (closed target)."""

        async def wait_for_url(self, *_a: Any, **_k: Any) -> None:
            raise RuntimeError("target closed")

        @property
        def url(self) -> str:
            raise RuntimeError("target closed")

    assert await await_url_settled(_Exploding()) is None  # must not raise


@pytest.mark.asyncio
async def test_settle_wait_returns_the_settled_url() -> None:
    """The happy path must actually be exercised, not skipped via AttributeError."""

    class _Settling:
        url = "https://labs.google/fx/pt/tools/flow/project/x"

        async def wait_for_url(self, *_a: Any, **_k: Any) -> None:
            return None

    assert await await_url_settled(_Settling()) == "https://labs.google/fx/pt/tools/flow/project/x"


# --- #587: the settle must be skipped on accounts Flow does not redirect ----


class _CountingPage:
    """Counts settle waits so "was it skipped?" is observable.

    Deliberately NOT a `_FakePage` subclass: that fake assigns `wait_for_url` as
    an *instance* attribute, which shadows any subclass method — the counter
    stayed 0 forever and the skip assertion passed vacuously.
    """

    def __init__(self) -> None:
        self.url = "https://labs.google/fx/tools/flow"
        self.goto = AsyncMock()
        self.waits = 0

    async def wait_for_url(self, *_a: Any, **_k: Any) -> None:
        self.waits += 1
        raise TimeoutError("a bare URL never becomes localised")


@pytest.mark.asyncio
async def test_settle_is_skipped_when_the_account_is_not_redirected() -> None:
    """No resolved locale => nothing to wait for => no 4 s timeout.

    Guarding only `_enter_editor` left three other `await_url_settled` calls
    burning the full `URL_SETTLE_TIMEOUT_MS` on every navigation — the very cost
    #587 exists to remove.
    """
    t = _transport(None)
    page = _CountingPage()

    assert await t._settle_if_redirecting(page) is None
    assert page.waits == 0


@pytest.mark.asyncio
async def test_settle_still_runs_when_the_account_is_redirected() -> None:
    t = _transport("pt")
    page = _CountingPage()

    await t._settle_if_redirecting(page)

    assert page.waits == 1
