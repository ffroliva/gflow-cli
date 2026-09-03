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
        lang: str | None = "",
        lang_after_hydration: str | None = None,
    ) -> None:
        self.url = url
        self._redirects_to = redirects_to
        self._lang = lang
        #: #651: Flow serves an `en` shell and rewrites `lang` at hydration. When
        #: set, the FIRST read returns `lang` and every read after the settle-wait
        #: returns this — the shape a single early read gets wrong.
        self._lang_after = lang_after_hydration
        self._hydrated = False
        self.probed = False
        self.lang_probed = False
        self.lang_waited = False
        self.goto = AsyncMock(side_effect=self._goto)

    async def _goto(self, url: str, **_k: Any) -> None:
        self.url = url

    async def wait_for_url(self, *_a: Any, **_k: Any) -> None:
        self.probed = True
        if self._redirects_to is None:
            raise TimeoutError("no localised URL ever appeared")
        self.url = self._redirects_to

    async def evaluate(self, *_a: Any, **_k: Any) -> str:
        """`document.documentElement.lang` (#643). ``lang=None`` models a probe
        that raises — a closed page, or evaluate blocked — which must never break
        the bootstrap."""
        self.lang_probed = True
        if self._lang is None:
            msg = "Execution context was destroyed"
            raise RuntimeError(msg)
        if self._hydrated and self._lang_after is not None:
            return self._lang_after
        return self._lang

    async def wait_for_function(self, *_a: Any, **_k: Any) -> None:
        """The #651 settle-wait. Hydration flips `lang` only if this page models
        an account whose locale differs from the `en` shell; otherwise it times
        out, which is a legitimate ANSWER (the shell value was already right)."""
        self.lang_waited = True
        if self._lang_after is None:
            msg = "Timeout waiting for function"
            raise TimeoutError(msg)
        self._hydrated = True


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


async def test_cached_no_redirect_skips_the_settle_but_still_reads_the_lang_attr(
    tmp_path: Path,
) -> None:
    """The 4 s settle is the timeout #587 is about — it must not be spent twice.

    But #639 showed the early return was skipping too much: it returned before
    ``_resolve_account_locale``, which is the only site of the ``<html lang>``
    recovery, so the state was ABSORBING — a latched profile could never learn a
    locale again. ``NOT_REDIRECTED`` means "skip the settle", not "skip
    everything", which is exactly what its own docstring says.
    """
    write_account_locale(tmp_path, NOT_REDIRECTED)
    page = _FakePage()

    client = await _bootstrap(tmp_path, page)

    assert page.probed is False, "the settle must still be skipped (#587)"
    assert page.lang_probed is True, "the lang attribute must still be read (#639/#643)"
    assert client._account_locale is None


# --- #639: NOT_REDIRECTED must not be an absorbing state ----------------------


async def test_a_latched_profile_recovers_its_locale_from_lang(tmp_path: Path) -> None:
    """Measured on a real pt-BR migrated load: the URL carries no segment, but
    Flow still renders ``lang="pt"``. Before this, the profile stayed None forever.

    The locale is recovered IN PROCESS and deliberately NOT written back: see
    :func:`test_recovering_the_locale_must_not_switch_the_settle_back_on`.
    """
    write_account_locale(tmp_path, NOT_REDIRECTED)
    page = _FakePage(lang="pt")

    client = await _bootstrap(tmp_path, page)

    assert client._account_locale == "pt"
    assert read_account_locale(tmp_path) == NOT_REDIRECTED


async def test_recovering_the_locale_must_not_switch_the_settle_back_on(
    tmp_path: Path,
) -> None:
    """The regression the first cut of #639's fix actually shipped.

    Folding the ``<html lang>`` observation into the cache made it a locale
    segment, so the NEXT run saw ``cached != NOT_REDIRECTED``, turned the settle
    back on, and paid the full 4 s ``URL_SETTLE_TIMEOUT_MS`` on every bootstrap of
    an account that never redirects — the exact cost #587 exists to remove.
    Measured live on `ffroliva`: two `transport.url_settle_gave_up` timeouts and a
    7.41 s warm bootstrap, which `scripts/dev/measure_locale_probe.py` flagged as
    "warm arm slower than cold".

    Asserting only the first bootstrap cannot catch this. Run two.
    """
    write_account_locale(tmp_path, NOT_REDIRECTED)

    for run in range(2):
        page = _FakePage(lang="en-GB")
        client = await _bootstrap(tmp_path, page)
        assert page.probed is False, f"run {run}: the settle came back on"
        assert client._account_locale == "en", f"run {run}: the locale was not recovered"
        assert read_account_locale(tmp_path) == NOT_REDIRECTED, (
            f"run {run}: the cached settle decision was overwritten by a locale"
        )


async def test_a_latched_profile_still_skips_the_settle(tmp_path: Path) -> None:
    """The #587 anti-regression, and the reason deleting the early return was rejected.

    Measured 2026-09-03 on `ffroliva`: 60/60 bootstrap loads served the bare URL
    and never redirected. The cached ``NOT_REDIRECTED`` is a TRUE observation for
    that account — only the locale read was wrongly disabled with it.
    """
    write_account_locale(tmp_path, NOT_REDIRECTED)
    page = _FakePage(lang="en-GB")

    client = await _bootstrap(tmp_path, page)

    assert page.probed is False, "no settle may be awaited on a known non-redirecting account"
    assert client._account_locale == "en"


async def test_a_latched_profile_with_no_lang_stays_latched(tmp_path: Path) -> None:
    write_account_locale(tmp_path, NOT_REDIRECTED)
    page = _FakePage(lang="")

    client = await _bootstrap(tmp_path, page)

    assert client._account_locale is None
    assert read_account_locale(tmp_path) == NOT_REDIRECTED


async def test_a_lang_probe_failure_never_breaks_bootstrap(tmp_path: Path) -> None:
    write_account_locale(tmp_path, NOT_REDIRECTED)
    page = _FakePage(lang=None)  # evaluate raises

    client = await _bootstrap(tmp_path, page)

    assert client._account_locale is None
    assert read_account_locale(tmp_path) == NOT_REDIRECTED


@pytest.mark.parametrize(
    "lang",
    ["../../etc/passwd", "pt/../en", "e n", "toolongtag", "1234"],
    ids=["traversal", "separator", "space", "too-long", "digits"],
)
async def test_a_malformed_lang_is_rejected_before_it_is_written(tmp_path: Path, lang: str) -> None:
    """``write_account_locale`` writes VERBATIM and the value is interpolated into
    a URL path, so the sanitising must happen before the write, not after."""
    write_account_locale(tmp_path, NOT_REDIRECTED)
    page = _FakePage(lang=lang)

    client = await _bootstrap(tmp_path, page)

    assert client._account_locale is None
    assert read_account_locale(tmp_path) == NOT_REDIRECTED


async def test_zh_hans_reduces_to_zh_without_crashing(tmp_path: Path) -> None:
    """Known-wrong and already flagged in the shipped docstring: Flow's URL
    segments carry no region, so ``zh-Hans``/``zh-Hant`` both reduce to ``zh``.
    Not fixed here — pinned so it cannot silently regress into a crash."""
    write_account_locale(tmp_path, NOT_REDIRECTED)
    page = _FakePage(lang="zh-Hans")

    client = await _bootstrap(tmp_path, page)

    assert client._account_locale == "zh"


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


async def test_a_lang_only_locale_is_not_evidence_that_flow_redirects(tmp_path: Path) -> None:
    """The v0.66.1 defect this branch also repairs — present on `develop`, not
    introduced here.

    #643's ``<html lang>`` fallback made `_resolve_account_locale` return a
    segment for an account Flow serves BARE and never redirects. `next_locale_state`
    then recorded that segment as the cached state, so every later run saw
    `cached != NOT_REDIRECTED`, settled, and burned the full 4 s
    ``URL_SETTLE_TIMEOUT_MS`` waiting for a redirect that never comes. Measured on
    `ffroliva`: `transport.url_settle_gave_up` twice per run, on a fresh profile.

    Only a locale read from the URL proves a redirect happened. A `lang` attribute
    proves nothing — every account has one.
    """
    page = _FakePage(redirects_to=None, lang="en-GB")  # never redirects, declares en-GB

    client = await _bootstrap(tmp_path, page)

    assert client._account_locale == "en", "the locale is still recovered for URL building"
    assert read_account_locale(tmp_path) == PROVISIONAL, (
        "a lang-only locale must not be recorded as evidence of a redirect"
    )

    # Second run: the no-redirect observation is corroborated and commits, which is
    # what finally switches the settle off for good.
    page2 = _FakePage(redirects_to=None, lang="en-GB")
    client2 = await _bootstrap(tmp_path, page2)
    assert read_account_locale(tmp_path) == NOT_REDIRECTED
    assert client2._account_locale == "en"

    # Third run: settle skipped, locale still recovered.
    page3 = _FakePage(redirects_to=None, lang="en-GB")
    client3 = await _bootstrap(tmp_path, page3)
    assert page3.probed is False, "the settle must be off once NOT_REDIRECTED commits"
    assert client3._account_locale == "en"


# --- #651: <html lang> starts as the `en` shell and flips at hydration ---------


async def test_a_lang_read_before_hydration_must_not_win(tmp_path: Path) -> None:
    """The defect, measured on a real pt account on the OLD host: `<html lang>`
    reads `en` until ~1.9 s and `pt` from ~2.3 s, so a single early read returned
    `en` for EVERY account whose URL could not answer.

    `readyState` cannot rescue this — it reaches "complete" ~1 s before the flip
    (see `scripts/dev/measure_html_lang_settle.py`), so the fix has to observe the
    change itself.
    """
    write_account_locale(tmp_path, NOT_REDIRECTED)
    page = _FakePage(lang="en", lang_after_hydration="pt")

    client = await _bootstrap(tmp_path, page)

    assert page.lang_waited is True, "the settle-wait must run"
    assert client._account_locale == "pt", "the post-hydration locale must win"


async def test_a_locale_equal_to_the_shell_default_still_resolves(tmp_path: Path) -> None:
    """An `en` account never changes the attribute, so the wait times out. That is
    an ANSWER, not a failure: the first read was already right."""
    write_account_locale(tmp_path, NOT_REDIRECTED)
    page = _FakePage(lang="en")  # no hydration flip

    client = await _bootstrap(tmp_path, page)

    assert page.lang_waited is True
    assert client._account_locale == "en"


async def test_the_settle_wait_is_skipped_when_the_url_already_answered(
    tmp_path: Path,
) -> None:
    """No regression for redirecting accounts: the URL is authoritative and cheap,
    so the `<html lang>` path — and its wait — must never be reached."""
    page = _FakePage("https://labs.google/fx/pt/tools/flow", lang="en", lang_after_hydration="de")

    client = await _bootstrap(tmp_path, page)

    assert client._account_locale == "pt"
    assert page.lang_probed is False, "the URL answered; do not touch <html lang>"
    assert page.lang_waited is False, "the URL answered; do not pay the settle-wait"
