"""BDD for #639: the migrated-host gate, and the locale latch it exposed.

Both features model the *field* preconditions rather than the convenient ones.
The URL flips mid-run instead of starting migrated, and the locale cache starts
latched instead of empty — those are the two shapes v0.66.1's green tests did not
have, which is why it shipped a fast path that never fired.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports.drivers.factory import detect_ui_mode
from gflow_cli.errors import EXIT_CODE_MAP, FlowHostMigratedError, is_retryable
from gflow_cli.profile_store import NOT_REDIRECTED, read_account_locale, write_account_locale

if TYPE_CHECKING:
    from pathlib import Path

scenarios("migrated_host_gate.feature")
scenarios("latched_locale_recovery.feature")

_LABS = "https://labs.google/fx/tools/flow/project/abc-123"
_MIGRATED = "https://flow.google.com/project/abc-123"
_CROP = "i.google-symbols:text-is('crop_16_9')"


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def gate_state() -> dict[str, Any]:
    return {"page": None, "error": None, "mode": None}


@pytest.fixture
def locale_state() -> dict[str, Any]:
    return {"page": None, "client": None}


class _GatePage:
    """A Playwright-shaped page whose host may flip once the run touches the DOM."""

    def __init__(self, *, present: set[str], flip: bool) -> None:
        self.url = _LABS
        self._present = present
        self._flip = flip
        self.extra_work = False

    def locator(self, selector: str) -> Any:
        if self._flip:
            self.url = _MIGRATED
        loc = MagicMock()
        loc.count = AsyncMock(return_value=1 if selector in self._present else 0)
        return loc

    async def goto(self, *_a: Any, **_k: Any) -> None:
        self.extra_work = True

    async def wait_for_url(self, *_a: Any, **_k: Any) -> None:
        self.extra_work = True


class _BootstrapPage:
    """Bootstrap page for a profile Flow does not redirect: the settle would time
    out, and the locale lives only in ``<html lang>``."""

    def __init__(self, lang: str) -> None:
        self.url = "https://labs.google/fx/tools/flow?hl=en"
        self._lang = lang
        self.settled = False
        self.goto = AsyncMock(side_effect=self._goto)

    async def _goto(self, url: str, **_k: Any) -> None:
        self.url = url

    async def wait_for_url(self, *_a: Any, **_k: Any) -> None:
        self.settled = True
        raise TimeoutError("no localised URL ever appeared")

    async def evaluate(self, *_a: Any, **_k: Any) -> str:
        return self._lang


# ---------------------------------------------------------------------------
# Feature: the migrated origin is detected before the run pays for it
# ---------------------------------------------------------------------------


@given("a project navigation that returns on labs.google")
def _returns_on_labs(gate_state: dict[str, Any]) -> None:
    gate_state["page"] = _GatePage(present=set(), flip=False)


@given("Flow redirects the page to flow.google.com before the first blocking wait")
def _redirects_mid_run(gate_state: dict[str, Any]) -> None:
    gate_state["page"] = _GatePage(present=set(), flip=True)


@given("a project navigation that lands on labs.google and never redirects")
def _stays_on_labs(gate_state: dict[str, Any]) -> None:
    gate_state["page"] = _GatePage(present={_CROP}, flip=False)


@given("a page whose url cannot be read as a string")
def _unreadable_url(gate_state: dict[str, Any]) -> None:
    page = MagicMock()  # .url is a MagicMock, never assigned
    page.locator = MagicMock(
        side_effect=lambda sel: MagicMock(count=AsyncMock(return_value=1 if sel == _CROP else 0))
    )
    gate_state["page"] = page


@when("gflow probes the UI cohort")
def _probe_cohort(gate_state: dict[str, Any]) -> None:
    # pytest-bdd calls step functions synchronously — an `async def` step is never
    # awaited and the scenario passes vacuously. Drive the coroutine here instead.
    async def _run() -> None:
        try:
            gate_state["mode"] = await detect_ui_mode(
                gate_state["page"], timeout_s=1.0, poll_interval_s=0.01
            )
        except FlowHostMigratedError as exc:
            gate_state["error"] = exc

    asyncio.run(_run())


@then("it fails with FlowHostMigratedError and exit code 36")
def _fails_with_36(gate_state: dict[str, Any]) -> None:
    assert isinstance(gate_state["error"], FlowHostMigratedError)
    assert EXIT_CODE_MAP[FlowHostMigratedError] == 36


@then("the error is retryable")
def _is_retryable(gate_state: dict[str, Any]) -> None:
    assert is_retryable(gate_state["error"])


@then("the error names flow.google.com rather than selector drift")
def _names_the_host(gate_state: dict[str, Any]) -> None:
    detail = str(gate_state["error"])
    assert "flow.google.com" in detail
    assert "selector drift" not in detail.lower().replace("this is not selector drift", "")


@then("the classic cohort is bound")
def _classic_bound(gate_state: dict[str, Any]) -> None:
    assert gate_state["error"] is None, f"unexpected abort: {gate_state['error']}"
    assert gate_state["mode"] == "classic"


@then("no additional navigation or wait was performed")
def _no_extra_work(gate_state: dict[str, Any]) -> None:
    assert gate_state["page"].extra_work is False


# ---------------------------------------------------------------------------
# Feature: a latched profile can still learn its locale
# ---------------------------------------------------------------------------


@given("a profile cached as NOT_REDIRECTED")
def _latched(tmp_path: Path) -> None:
    write_account_locale(tmp_path, NOT_REDIRECTED)


@given(parsers.parse('Flow renders the document with lang "{lang}"'))
def _document_lang(locale_state: dict[str, Any], lang: str) -> None:
    locale_state["page"] = _BootstrapPage(lang)


@given("Flow renders the document with no lang attribute")
def _document_no_lang(locale_state: dict[str, Any]) -> None:
    locale_state["page"] = _BootstrapPage("")


@when("gflow bootstraps")
def _bootstrap(locale_state: dict[str, Any], tmp_path: Path) -> None:
    client = FlowApiClient(tmp_path)
    client._page = locale_state["page"]  # type: ignore[assignment]
    asyncio.run(client._bootstrap_and_resolve_locale())
    locale_state["client"] = client


@then(parsers.parse('the account locale resolves to "{expected}"'))
def _locale_is(locale_state: dict[str, Any], expected: str) -> None:
    assert locale_state["client"]._account_locale == expected


@then("no URL settle is awaited")
def _no_settle(locale_state: dict[str, Any]) -> None:
    assert locale_state["page"].settled is False


@then("the account locale stays unresolved")
def _locale_unresolved(locale_state: dict[str, Any]) -> None:
    assert locale_state["client"]._account_locale is None


@then("the profile stays cached as NOT_REDIRECTED")
def _stays_latched(tmp_path: Path) -> None:
    assert read_account_locale(tmp_path) == NOT_REDIRECTED
