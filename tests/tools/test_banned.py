from __future__ import annotations

from gflow_cli.tools.banned import BANNED_KEYWORDS, strip_banned_keywords


def test_banned_list_is_verbatim() -> None:
    assert "8k" in BANNED_KEYWORDS
    assert "hyperrealistic" in BANNED_KEYWORDS
    assert "award winning" in BANNED_KEYWORDS


def test_strips_case_insensitively_and_reports() -> None:
    cleaned, removed = strip_banned_keywords("A Hyperrealistic, 8K masterpiece of a cat")
    assert "hyperrealistic" not in cleaned.lower()
    assert "8k" not in cleaned.lower()
    assert "masterpiece" not in cleaned.lower()
    assert {"hyperrealistic", "8k", "masterpiece"} <= set(removed)
    # no double spaces or dangling separators left
    assert "  " not in cleaned


def test_multiword_phrase_removed() -> None:
    cleaned, removed = strip_banned_keywords("an award winning portrait")
    assert "award winning" not in cleaned.lower()
    assert "award winning" in removed
    assert "portrait" in cleaned


def test_no_banned_returns_unchanged() -> None:
    cleaned, removed = strip_banned_keywords("a serene mountain lake at dawn")
    assert cleaned == "a serene mountain lake at dawn"
    assert removed == []
