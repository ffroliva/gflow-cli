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


def test_no_interior_double_comma_after_adjacent_banned_terms() -> None:
    """Regression: 3+ adjacent banned terms must not leave ',,' in the output.

    Before the fix, ``re.sub(r',\\s*,', ',', ...)`` only collapsed one pair at
    a time, so stripping 8k+hyperrealistic+masterpiece left a stray interior
    double-comma.
    """
    text = "cinematic, 8k, hyperrealistic, masterpiece, golden hour"
    cleaned, removed = strip_banned_keywords(text)
    assert ",," not in cleaned, f"double-comma found in {cleaned!r}"
    assert "8k" not in cleaned.lower()
    assert "hyperrealistic" not in cleaned.lower()
    assert "masterpiece" not in cleaned.lower()
    assert "cinematic" in cleaned.lower()
    assert "golden hour" in cleaned.lower()


def test_custom_keywords_overrides_default_list() -> None:
    """strip_banned_keywords with explicit keywords only strips those terms."""
    cleaned, removed = strip_banned_keywords("an 8k photorealism shot", ("photorealism",))
    assert "photorealism" not in cleaned.lower()
    assert "photorealism" in removed
    # "8k" is NOT in the custom keyword list — must survive
    assert "8k" in cleaned.lower()
