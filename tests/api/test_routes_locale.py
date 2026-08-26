"""Locale-segment extraction from a settled Flow URL (issue #580).

gflow hardcoded `locale="en-US"` for every account, so a pt-BR account was sent to
`/fx/en/...` and Flow redirected it to `/fx/pt/...` AFTER `page.goto` had already
returned — leaving the next DOM action operating on a page about to be navigated
away. The only trustworthy source of the account locale is where Flow itself lands
(`auth/session` carries no locale, and `navigator.language` reports the value gflow
sets at launch). This parses that landing URL.
"""

from __future__ import annotations

import pytest

from gflow_cli.api.routes import locale_segment_from_url

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
