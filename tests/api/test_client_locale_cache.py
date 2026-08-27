"""The locale probe runs once per profile, not once per command (#587).

Two properties: a cache hit must not probe (including the "not redirected"
outcome, which is the account that pays the timeout), and the cache decides
whether to WAIT, never where to GO. See the CHANGELOG entry for #587.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.profile_store import read_account_locale, write_account_locale

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.asyncio


class _FakePage:
    """Bootstrap page modelling Flow's real sequence: land BARE, then redirect.

    Seeding ``url`` with the localised form (the first version of this fake) made
    ``await_url_settled`` short-circuit on line one, so "the settle ran" could not
    be asserted at all. ``goto`` therefore lands on the requested (bare) URL and
    only ``wait_for_url`` — the settle — moves it to ``redirects_to``.

    ``probed`` records whether the settle ran; when ``redirects_to`` is None the
    wait raises, matching an account Flow never redirects.
    """

    def __init__(
        self,
        redirects_to: str | None = None,
        *,
        url: str = "https://labs.google/fx/tools/flow?hl=en",
    ) -> None:
        self.url = url
        self._redirects_to = redirects_to
        self.probed = False
        self.goto = AsyncMock(side_effect=self._goto)

    async def _goto(self, url: str, **_k: Any) -> None:
        self.url = url

    async def wait_for_url(self, *_a: Any, **_k: Any) -> None:
        self.probed = True
        if self._redirects_to is None:
            raise TimeoutError("no localised URL ever appeared")
        self.url = self._redirects_to


def _client(tmp_path: Path) -> FlowApiClient:
    return FlowApiClient(tmp_path)


async def test_first_run_probes_and_persists_the_segment(tmp_path: Path) -> None:
    client = _client(tmp_path)
    page = _FakePage("https://labs.google/fx/pt/tools/flow")
    client._page = page  # type: ignore[assignment]

    await client._bootstrap_and_resolve_locale()

    assert page.probed is True
    assert client._account_locale == "pt"
    assert read_account_locale(tmp_path) == "pt"


async def test_first_run_persists_the_no_redirect_outcome(tmp_path: Path) -> None:
    """The account that pays the 4 s must record *that* answer, not nothing."""
    client = _client(tmp_path)
    page = _FakePage(None)  # Flow never redirects this account
    client._page = page  # type: ignore[assignment]

    await client._bootstrap_and_resolve_locale()

    assert client._account_locale is None
    assert read_account_locale(tmp_path) == ""


async def test_a_cached_segment_still_settles(tmp_path: Path) -> None:
    """A redirecting account keeps asking Flow — the redirect is fast AND true.

    Skipping it here would buy ~3 s at the price of never being able to notice a
    changed locale, because a bare navigation is the only thing that makes Flow
    state the account's own answer.
    """
    write_account_locale(tmp_path, "pt")
    client = _client(tmp_path)
    page = _FakePage("https://labs.google/fx/pt/tools/flow")
    client._page = page  # type: ignore[assignment]

    await client._bootstrap_and_resolve_locale()

    # Asserting the OUTCOME alone is vacuous — mutation testing showed it passed
    # against a build where every cache hit skipped the settle. That the settle
    # RAN is the behaviour under test.
    assert page.probed is True
    assert client._account_locale == "pt"


async def test_a_stale_segment_self_heals_on_the_next_run(tmp_path: Path) -> None:
    """The poisoned-cache case, which the live run proved a localised bootstrap could not fix."""
    write_account_locale(tmp_path, "de")
    client = _client(tmp_path)
    page = _FakePage("https://labs.google/fx/pt/tools/flow")
    client._page = page  # type: ignore[assignment]

    await client._bootstrap_and_resolve_locale()

    assert client._account_locale == "pt"
    assert read_account_locale(tmp_path) == "pt"


async def test_cached_no_redirect_skips_the_probe_entirely(tmp_path: Path) -> None:
    """This is the 4 s the issue is about. It must not be spent twice."""
    write_account_locale(tmp_path, None)
    client = _client(tmp_path)
    page = _FakePage()
    client._page = page  # type: ignore[assignment]

    await client._bootstrap_and_resolve_locale()

    assert client._account_locale is None
    assert page.probed is False


@pytest.mark.parametrize("cached", ["pt", "", None], ids=["segment", "no-redirect", "unprobed"])
async def test_the_bootstrap_navigation_is_always_bare(tmp_path: Path, cached: str | None) -> None:
    """Never send the browser to a locale we chose. Flow would simply obey.

    Live on 2026-08-27, a pt-BR account handed ``/fx/de/`` served German and never
    redirected — the cached value becomes both unverifiable and actively wrong.
    """
    if cached is not None:
        write_account_locale(tmp_path, cached or None)
    client = _client(tmp_path)
    page = _FakePage("https://labs.google/fx/pt/tools/flow")
    client._page = page  # type: ignore[assignment]

    await client._bootstrap_and_resolve_locale()

    (url,), _ = page.goto.call_args
    assert url == "https://labs.google/fx/tools/flow?hl=en"


# --- staleness: correct only on positive evidence ---------------------------


async def test_a_cached_segment_is_left_to_the_bootstrap_settle(tmp_path: Path) -> None:
    """With a segment cached, the correction must stand down.

    That run probes anyway and rewrites the account locale unconditionally, so the
    correction adds nothing — and reading a URL that gflow itself may have chosen
    adds risk. The committed poison spike caught exactly that: a direct `page.goto`
    to `/fx/de/` made teardown log `account_locale_changed now=de was=pt`.
    """
    write_account_locale(tmp_path, "pt")
    client = _client(tmp_path)
    client._account_locale = "pt"
    client._bootstrap_landed = True
    client._page = _FakePage(url="https://labs.google/fx/de/tools/flow/project/x")  # type: ignore[assignment]

    client._persist_locale_correction()

    assert read_account_locale(tmp_path) == "pt"


async def test_a_bare_url_is_not_read_as_stopped_redirecting(tmp_path: Path) -> None:
    """Absence of evidence is not evidence of absence.

    A segment-less last URL can be an auth page, an error page, or a route Flow
    does not localise.
    """
    write_account_locale(tmp_path, None)
    client = _client(tmp_path)
    client._account_locale = None
    client._bootstrap_landed = True
    client._page = _FakePage(url="https://labs.google/fx/tools/flow")  # type: ignore[assignment]

    client._persist_locale_correction()

    assert read_account_locale(tmp_path) == ""


async def test_correction_never_raises_on_a_dead_page(tmp_path: Path) -> None:
    """Teardown runs while the browser is closing; `.url` can throw."""

    class _Dead:
        @property
        def url(self) -> str:
            raise RuntimeError("target closed")

    client = _client(tmp_path)
    # MUST be None with the bootstrap landed, or the guards return before `.url`
    # is ever read and the try/except under test is unreachable — mutation showed
    # deleting it left the suite green.
    client._account_locale = None
    client._bootstrap_landed = True
    client._page = _Dead()  # type: ignore[assignment]

    client._persist_locale_correction()  # must not raise


# --- regressions the council reproduced ------------------------------------


async def test_a_caller_supplied_locale_never_becomes_the_account_locale(
    tmp_path: Path,
) -> None:
    """`gflow character create --locale de` must not cache `de` as the account locale.

    Reproduced by the council against real code: the character editor navigates
    the SHARED client page to a caller-chosen `/fx/de/...`, and teardown then read
    that URL as if Flow had chosen it. `project list` afterwards emitted
    `/fx/de/...` — exactly the guessed segment this PR exists to remove.
    """
    write_account_locale(tmp_path, None)  # account is NOT redirected
    client = _client(tmp_path)
    client._account_locale = None

    # what `generate_character_image` does when the caller passes --locale
    client._caller_locale_navigated = True
    client._bootstrap_landed = True
    client._page = _FakePage(url="https://labs.google/fx/de/tools/flow/project/x")  # type: ignore[assignment]
    client._persist_locale_correction()

    assert read_account_locale(tmp_path) == "", "a caller's locale was cached as the account's"


async def test_correction_is_wired_into_teardown(tmp_path: Path) -> None:
    """Cover the CALL, not just the method — deleting the call site broke no test."""
    client = _client(tmp_path)
    client._account_locale = None
    client._bootstrap_landed = True
    client._page = _FakePage(url="https://labs.google/fx/pt/tools/flow/project/x")  # type: ignore[assignment]

    await client._close_browser_resources()

    assert read_account_locale(tmp_path) == "pt"


async def test_a_pwa_restored_url_is_not_read_as_the_account_locale(tmp_path: Path) -> None:
    """A failed bootstrap leaves Chrome's RESTORED url on the page — not evidence.

    `_page` is assigned before `_bootstrap_and_resolve_locale` runs, and
    `__aenter__`'s failure guard tears down through `_close_browser_resources`.
    Chrome's PWA restores the last-visited project URL, so a PREVIOUS
    `gflow character create --locale de` run leaves `/fx/de/...` sitting there for
    a fresh process to mistake for Flow's answer.
    """
    write_account_locale(tmp_path, None)
    client = _client(tmp_path)
    client._account_locale = None
    client._bootstrap_landed = False  # the goto never completed
    client._page = _FakePage(url="https://labs.google/fx/de/tools/flow/project/x")  # type: ignore[assignment]

    client._persist_locale_correction()

    assert read_account_locale(tmp_path) == "", "a restored URL was cached as the account locale"
