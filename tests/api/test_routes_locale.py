"""Locale-segment extraction from a settled Flow URL (issue #580).

gflow hardcoded `locale="en-US"` for every account, so a pt-BR account was sent to
`/fx/en/...` and Flow redirected it to `/fx/pt/...` AFTER `page.goto` had already
returned — leaving the next DOM action operating on a page about to be navigated
away. The only trustworthy source of the account locale is where Flow itself lands
(`auth/session` carries no locale, and `navigator.language` reports the value gflow
sets at launch). This parses that landing URL.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.routes import locale_segment_from_lang_attr, locale_segment_from_url

PID = "2ddc3a33-97db-41a0-a0d3-7f9488b0d5a9"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # measured live on denon82 (pt-BR)
        (f"https://labs.google/fx/pt/tools/flow/project/{PID}", "pt"),
        ("https://labs.google/fx/pt/tools/flow", "pt"),
        ("https://labs.google/fx/pt/tools/flow?hl=en", "pt"),
        ("https://labs.google/fx/en/tools/flow", "en"),
        ("https://labs.google/fx/ja/tools/flow", "ja"),
        # three-letter segments are legal BCP-47 primary tags
        ("https://labs.google/fx/fil/tools/flow", "fil"),
    ],
)
def test_extracts_locale_segment(url: str, expected: str) -> None:
    assert locale_segment_from_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        # no segment at all — Flow serves this and normalises later
        f"https://labs.google/fx/tools/flow/project/{PID}",
        "https://labs.google/fx/tools/flow",
        # `tools` is not a locale: the guard must not mistake the next path
        # component for one just because it sits in the segment position.
        "https://labs.google/fx/tools",
        # junk in the segment slot
        "https://labs.google/fx/PROJECT/tools/flow",
        "https://labs.google/fx/toolong/tools/flow",
        "https://labs.google/fx/1/tools/flow",
        "https://labs.google/fx/p-t/tools/flow",
        # not a Flow URL
        "https://example.invalid/fx/pt/tools/flow",
        "https://labs.google/other/pt/tools/flow",
        "",
    ],
)
def test_returns_none_when_no_trustworthy_segment(url: str) -> None:
    """No segment is strictly better than a guessed one.

    Falling back to the bare URL is never worse than today's behaviour; guessing
    `en` is exactly the bug.
    """
    assert locale_segment_from_url(url) is None


def test_bcp47_tail_is_dropped() -> None:
    """`pt-BR` in the path reduces to the primary tag Flow actually serves."""
    assert locale_segment_from_url("https://labs.google/fx/pt-BR/tools/flow") == "pt"


# ---------------------------------------------------------------------------
# #643: on flow.google.com the locale left the URL but NOT the document
# ---------------------------------------------------------------------------


class TestLocaleSegmentFromLangAttr:
    """The migrated origin serves `/project/<id>` with no locale segment, so
    `locale_segment_from_url` is structurally blind there — but the locale is
    still being served, in `<html lang>`.

    Measured on two profiles, old host vs migrated:

        ffroliva  old: /fx/tools/flow (bare)    lang=en     migrated: lang=en-GB
        denon82   old: /fx/**pt**/tools/flow    lang=pt     migrated: lang=pt

    `html lang` AGREED with the URL segment wherever both existed, which is what
    makes it a trustworthy fallback. Unlike `navigator.language` — which reports
    the value gflow itself sets when it launches the context — this attribute is
    server-rendered by Flow.
    """

    def test_plain_segment(self) -> None:
        assert locale_segment_from_lang_attr("pt") == "pt"

    def test_region_suffix_is_reduced_to_the_segment(self) -> None:
        """Measured: the migrated English account renders `en-GB`; Flow's URL
        segment form is `en`, so the region must be dropped to stay comparable."""
        assert locale_segment_from_lang_attr("en-GB") == "en"
        assert locale_segment_from_lang_attr("pt-BR") == "pt"

    def test_case_is_normalised(self) -> None:
        assert locale_segment_from_lang_attr("PT-br") == "pt"

    def test_three_letter_segment_allowed(self) -> None:
        assert locale_segment_from_lang_attr("fil") == "fil"

    def test_junk_is_none_not_a_guess(self) -> None:
        for bad in ("", "   ", "x", "english", "e n", "1234", "zz-ZZ-ZZ-ZZ"):
            assert locale_segment_from_lang_attr(bad) is None, bad

    def test_none_input(self) -> None:
        assert locale_segment_from_lang_attr(None) is None

    def test_agrees_with_url_derivation_where_both_exist(self) -> None:
        """The property that licenses the fallback: on the OLD host, both
        sources are present and they must not disagree."""
        url = "https://labs.google/fx/pt/tools/flow"
        assert locale_segment_from_url(url) == locale_segment_from_lang_attr("pt")


# ---------------------------------------------------------------------------
# #643: the resolver must USE the fallback, not just have one available
# ---------------------------------------------------------------------------


class TestResolveAccountLocaleFallsBackToHtmlLang:
    """`_resolve_account_locale` derived the locale from the settled URL only.

    On the migrated origin that URL carries no segment, so it resolved `None` —
    and `next_locale_state("pt", None)` then returned PROVISIONAL, DEMOTING a
    correctly-learned locale (measured on `denon82`). The locale was sitting in
    `<html lang>` the whole time.

    Since #639 the method returns ``(locale, from_url)``. The second element is the
    ONLY evidence that Flow redirects this account, and the caller folds just that
    into the cached state — a ``lang`` attribute is not a redirect, and treating it
    as one switched the URL settle back on permanently.
    """

    @staticmethod
    def _page(url: str, lang: str) -> MagicMock:
        page = MagicMock()
        page.url = url
        page.wait_for_url = AsyncMock()
        page.evaluate = AsyncMock(return_value=lang)
        return page

    @pytest.mark.asyncio
    async def test_migrated_host_recovers_locale_from_html_lang(self) -> None:
        from gflow_cli.api.client import FlowApiClient

        page = self._page("https://flow.google.com/project/abc-123", "pt")
        got, from_url = await FlowApiClient._resolve_account_locale(object(), page)  # type: ignore[arg-type]
        assert got == "pt"
        assert from_url is None, "a lang-derived locale is not evidence of a redirect"

    @pytest.mark.asyncio
    async def test_migrated_english_region_tag_reduces_to_segment(self) -> None:
        from gflow_cli.api.client import FlowApiClient

        page = self._page("https://flow.google.com/", "en-GB")
        got, from_url = await FlowApiClient._resolve_account_locale(object(), page)  # type: ignore[arg-type]
        assert got == "en"
        assert from_url is None

    @pytest.mark.asyncio
    async def test_url_segment_still_wins_on_the_old_host(self) -> None:
        """No regression: where Flow states the locale in the URL, that stays
        authoritative — the fallback must not override it."""
        from gflow_cli.api.client import FlowApiClient

        page = self._page("https://labs.google/fx/pt/tools/flow", "de")
        got, from_url = await FlowApiClient._resolve_account_locale(object(), page)  # type: ignore[arg-type]
        assert got == "pt"
        assert from_url == "pt", "a URL segment IS evidence of a redirect"
        page.evaluate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unreadable_lang_stays_none_rather_than_guessing(self) -> None:
        from gflow_cli.api.client import FlowApiClient

        page = self._page("https://flow.google.com/", "")
        assert await FlowApiClient._resolve_account_locale(object(), page) == (None, None)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_evaluate_failure_is_survivable(self) -> None:
        from gflow_cli.api.client import FlowApiClient

        page = self._page("https://flow.google.com/", "pt")
        page.evaluate = AsyncMock(side_effect=RuntimeError("no execution context"))
        assert await FlowApiClient._resolve_account_locale(object(), page) == (None, None)  # type: ignore[arg-type]
