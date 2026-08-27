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
from gflow_cli.profile_store import (
    NOT_REDIRECTED,
    PROVISIONAL,
    read_account_locale,
    write_account_locale,
)

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


async def _bootstrap(tmp_path: Path, page: _FakePage) -> FlowApiClient:
    client = FlowApiClient(tmp_path)
    client._page = page  # type: ignore[assignment]
    await client._bootstrap_and_resolve_locale()
    return client


# --- the probe outcome is cached ---------------------------------------------


async def test_first_run_probes_and_persists_the_segment(tmp_path: Path) -> None:
    page = _FakePage("https://labs.google/fx/pt/tools/flow")

    client = await _bootstrap(tmp_path, page)

    assert page.probed is True
    assert client._account_locale == "pt"
    assert read_account_locale(tmp_path) == "pt"


async def test_a_cached_segment_still_settles(tmp_path: Path) -> None:
    """A redirecting account keeps asking Flow — the redirect is fast AND true.

    Asserting the OUTCOME alone is vacuous: mutation testing showed it passed
    against a build where every cache hit skipped the settle.
    """
    write_account_locale(tmp_path, "pt")
    page = _FakePage("https://labs.google/fx/pt/tools/flow")

    client = await _bootstrap(tmp_path, page)

    assert page.probed is True
    assert client._account_locale == "pt"


async def test_a_stale_segment_self_heals_on_the_next_run(tmp_path: Path) -> None:
    """The poisoned-cache case, which a localised bootstrap could not fix."""
    write_account_locale(tmp_path, "de")
    page = _FakePage("https://labs.google/fx/pt/tools/flow")

    client = await _bootstrap(tmp_path, page)

    assert client._account_locale == "pt"
    assert read_account_locale(tmp_path) == "pt"


async def test_cached_no_redirect_skips_the_probe_entirely(tmp_path: Path) -> None:
    """This is the timeout the issue is about. It must not be spent twice."""
    write_account_locale(tmp_path, NOT_REDIRECTED)
    page = _FakePage()

    client = await _bootstrap(tmp_path, page)

    assert page.probed is False
    assert client._account_locale is None


@pytest.mark.parametrize(
    "cached",
    ["pt", NOT_REDIRECTED, PROVISIONAL, None],
    ids=["segment", "no-redirect", "provisional", "unprobed"],
)
async def test_the_bootstrap_navigation_is_always_bare(tmp_path: Path, cached: str | None) -> None:
    """Never send the browser to a locale we chose. Flow would simply obey.

    Live on 2026-08-27, a pt-BR account handed ``/fx/de/`` served German and never
    redirected — the cached value becomes both unverifiable and actively wrong.
    """
    if cached is not None:
        write_account_locale(tmp_path, cached)
    page = _FakePage("https://labs.google/fx/pt/tools/flow")

    await _bootstrap(tmp_path, page)

    (url,), _ = page.goto.call_args
    assert url == "https://labs.google/fx/tools/flow?hl=en"


# --- one transient timeout must not disable the settle forever ---------------


async def test_a_first_no_redirect_is_only_provisional(tmp_path: Path) -> None:
    """``await_url_settled`` returns None for BOTH "no redirect" and "timed out".

    Committing to NOT_REDIRECTED on the first observation is what let one slow
    network permanently restore #580's race. Every guard the old teardown
    self-heal carried existed to repair that after the fact; two agreeing
    observations make the bad state unreachable instead.
    """
    page = _FakePage(None)  # the settle finds nothing

    client = await _bootstrap(tmp_path, page)

    assert client._account_locale is None
    assert read_account_locale(tmp_path) == PROVISIONAL


async def test_a_provisional_cache_still_probes(tmp_path: Path) -> None:
    write_account_locale(tmp_path, PROVISIONAL)
    page = _FakePage(None)

    await _bootstrap(tmp_path, page)

    assert page.probed is True


async def test_two_agreeing_no_redirects_commit(tmp_path: Path) -> None:
    """Only the SECOND agreeing observation earns the skip."""
    write_account_locale(tmp_path, PROVISIONAL)
    page = _FakePage(None)

    client = await _bootstrap(tmp_path, page)

    assert client._account_locale is None
    assert read_account_locale(tmp_path) == NOT_REDIRECTED


async def test_a_transient_timeout_on_a_redirecting_account_does_not_commit(
    tmp_path: Path,
) -> None:
    """THE regression this design exists for.

    A cached segment plus one failed settle must not become "not redirected":
    that state skips the settle forever, and nothing downstream can tell it from
    a genuine answer. It falls back to PROVISIONAL, so the next run re-probes.
    """
    write_account_locale(tmp_path, "pt")
    page = _FakePage(None)  # slow network, Flow hiccup — the settle times out

    await _bootstrap(tmp_path, page)

    assert read_account_locale(tmp_path) == PROVISIONAL
    assert read_account_locale(tmp_path) != NOT_REDIRECTED


async def test_a_segment_after_a_provisional_is_taken_at_face_value(tmp_path: Path) -> None:
    """Flow stating a locale is not ambiguous the way silence is."""
    write_account_locale(tmp_path, PROVISIONAL)
    page = _FakePage("https://labs.google/fx/pt/tools/flow")

    await _bootstrap(tmp_path, page)

    assert read_account_locale(tmp_path) == "pt"
