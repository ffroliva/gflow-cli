"""The locale probe runs once per profile, not once per command (#587).

``_resolve_account_locale`` (#580) settles the bootstrap navigation to learn the
account's locale. On an account Flow does **not** redirect there is nothing to
settle, so ``wait_for_url`` runs to the full 4 s timeout
(``_common.py:153``) — every command, forever. Measured live 2026-08-27: 7.0 s
setup on the ``en`` account against a 3.5 s floor once cached.

The fix caches the probe's outcome in the profile dir. Two properties matter and
both are pinned here:

1. **A cache hit must not probe.** Including the "no redirect" outcome — that is
   the account paying the cost.
2. **The cache decides whether to WAIT, never where to GO.** Measured live on
   2026-08-27: Flow serves whatever locale segment it is asked for. A pt-BR
   account sent to ``/fx/de/tools/flow`` stayed there and rendered
   ``html lang=de`` — no redirect, so no correction signal, and a wrong-language
   UI for as long as the stale value lived. That is #580's defect in a new hat.
   The bootstrap navigation is therefore always bare, and only the
   "not redirected" state skips the settle.
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
    """Bootstrap page. ``wait_for_url`` NEVER resolves — a probe here is a failure.

    A probe that runs is the bug under test, so the fake makes it loud rather
    than slow: any call raises instead of burning the real 4 s.
    """

    def __init__(self, url: str = "https://labs.google/fx/tools/flow?hl=en") -> None:
        self.url = url
        self.goto = AsyncMock()
        self.probed = False

    async def wait_for_url(self, *_a: Any, **_k: Any) -> None:
        self.probed = True
        raise AssertionError("the locale probe ran on a cache hit")


def _client(tmp_path: Path) -> FlowApiClient:
    return FlowApiClient(tmp_path)


async def test_first_run_probes_and_persists_the_segment(tmp_path: Path) -> None:
    client = _client(tmp_path)
    page = _FakePage(url="https://labs.google/fx/pt/tools/flow")
    page.wait_for_url = AsyncMock()  # type: ignore[method-assign]
    client._page = page  # type: ignore[assignment]

    await client._bootstrap_and_resolve_locale()

    assert client._account_locale == "pt"
    assert read_account_locale(tmp_path) == "pt"


async def test_first_run_persists_the_no_redirect_outcome(tmp_path: Path) -> None:
    """The account that pays the 4 s must record *that* answer, not nothing."""
    client = _client(tmp_path)
    page = _FakePage(url="https://labs.google/fx/tools/flow?hl=en")

    async def _timeout(*_a: Any, **_k: Any) -> None:
        raise TimeoutError("no localised URL ever appeared")

    page.wait_for_url = _timeout  # type: ignore[method-assign]
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
    page = _FakePage(url="https://labs.google/fx/pt/tools/flow")
    page.wait_for_url = AsyncMock()  # type: ignore[method-assign]
    client._page = page  # type: ignore[assignment]

    await client._bootstrap_and_resolve_locale()

    assert client._account_locale == "pt"


async def test_a_stale_segment_self_heals_on_the_next_run(tmp_path: Path) -> None:
    """The poisoned-cache case, which the live run proved a localised bootstrap could not fix."""
    write_account_locale(tmp_path, "de")
    client = _client(tmp_path)
    page = _FakePage(url="https://labs.google/fx/pt/tools/flow")
    page.wait_for_url = AsyncMock()  # type: ignore[method-assign]
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
    page = _FakePage()
    page.wait_for_url = AsyncMock()  # type: ignore[method-assign]
    client._page = page  # type: ignore[assignment]

    await client._bootstrap_and_resolve_locale()

    (url,), _ = page.goto.call_args
    assert url == "https://labs.google/fx/tools/flow?hl=en"


# --- staleness: correct only on positive evidence ---------------------------


async def test_a_changed_locale_is_re_cached_at_teardown(tmp_path: Path) -> None:
    write_account_locale(tmp_path, "pt")
    client = _client(tmp_path)
    client._account_locale = "pt"
    client._page = _FakePage(url="https://labs.google/fx/de/tools/flow/project/x")  # type: ignore[assignment]

    client._persist_locale_correction()

    assert read_account_locale(tmp_path) == "de"


async def test_the_same_locale_is_not_rewritten(tmp_path: Path) -> None:
    write_account_locale(tmp_path, "pt")
    client = _client(tmp_path)
    client._account_locale = "pt"
    client._page = _FakePage(url="https://labs.google/fx/pt/tools/flow/project/x")  # type: ignore[assignment]

    client._persist_locale_correction()

    assert read_account_locale(tmp_path) == "pt"


async def test_a_bare_url_does_not_erase_a_known_locale(tmp_path: Path) -> None:
    """Absence of evidence is not evidence of absence.

    A last URL without a locale segment can be an auth page, an error page, or a
    route Flow simply does not localise. Treating it as "this account stopped
    redirecting" would throw away a good value and reintroduce the probe.
    """
    write_account_locale(tmp_path, "pt")
    client = _client(tmp_path)
    client._account_locale = "pt"
    client._page = _FakePage(url="https://labs.google/fx/tools/flow")  # type: ignore[assignment]

    client._persist_locale_correction()

    assert read_account_locale(tmp_path) == "pt"


async def test_correction_never_raises_on_a_dead_page(tmp_path: Path) -> None:
    """Teardown runs while the browser is closing; `.url` can throw."""

    class _Dead:
        @property
        def url(self) -> str:
            raise RuntimeError("target closed")

    client = _client(tmp_path)
    client._account_locale = "pt"
    client._page = _Dead()  # type: ignore[assignment]

    client._persist_locale_correction()  # must not raise
