from __future__ import annotations

from gflow_cli.services.mentions import parse_mentions


def test_parse_simple_mention() -> None:
    tokens = parse_mentions("Hello @Zoro how are you")
    assert len(tokens) == 1
    assert tokens[0].start_idx == 6
    assert tokens[0].end_idx == 23
    assert tokens[0].candidate_text == "Zoro how are you"


def test_parse_multiple_mentions() -> None:
    tokens = parse_mentions("@Zoro hands @Mika the sword")
    assert len(tokens) == 2

    assert tokens[0].start_idx == 0
    assert tokens[0].end_idx == 12
    assert tokens[0].candidate_text == "Zoro hands "

    assert tokens[1].start_idx == 12
    assert tokens[1].end_idx == 27
    assert tokens[1].candidate_text == "Mika the sword"


def test_parse_word_boundary_guard() -> None:
    # A word character preceding @ makes it not a mention
    tokens = parse_mentions("user@example.com")
    assert len(tokens) == 0

    tokens = parse_mentions("hello@Zoro")
    assert len(tokens) == 0


def test_parse_escape_sequence() -> None:
    # @@Zoro should emit literal @Zoro and not count as a mention
    tokens = parse_mentions("This is @@Zoro and @@Mika")
    assert len(tokens) == 0

    # Mixing escape and mention
    tokens = parse_mentions("This is @@Zoro and @Mika")
    assert len(tokens) == 1
    assert tokens[0].start_idx == 19
    assert tokens[0].candidate_text == "Mika"


def test_parse_punctuation_boundaries() -> None:
    # A candidate span stops at next mention or . ! ? , ; : or newline
    tokens = parse_mentions("Hello @Zoro. How are you?")
    assert len(tokens) == 1
    assert tokens[0].start_idx == 6
    assert tokens[0].end_idx == 11
    assert tokens[0].candidate_text == "Zoro"

    tokens = parse_mentions("Hello @Zoro! and @Mika?")
    assert len(tokens) == 2
    assert tokens[0].start_idx == 6
    assert tokens[0].end_idx == 11
    assert tokens[0].candidate_text == "Zoro"
    assert tokens[1].start_idx == 17
    assert tokens[1].end_idx == 22
    assert tokens[1].candidate_text == "Mika"

    tokens = parse_mentions("Line one @Zoro\nLine two @Mika")
    assert len(tokens) == 2
    assert tokens[0].start_idx == 9
    assert tokens[0].end_idx == 14
    assert tokens[0].candidate_text == "Zoro"
    assert tokens[1].start_idx == 24
    assert tokens[1].end_idx == 29
    assert tokens[1].candidate_text == "Mika"


def test_parse_unicode_names() -> None:
    tokens = parse_mentions("Hello @Capit\u00e3oZoro!")
    assert len(tokens) == 1
    assert tokens[0].candidate_text == "Capit\u00e3oZoro"


def test_parse_odd_escape_run_starts_mention() -> None:
    # ``@@`` is consumed as a literal-@ escape; the trailing third @ starts a
    # real mention. Locks the finditer escape-pairing against a regex regression.
    tokens = parse_mentions("@@@Zoro")
    assert len(tokens) == 1
    assert tokens[0].start_idx == 2
    assert tokens[0].candidate_text == "Zoro"


def test_parse_no_mentions_returns_empty() -> None:
    assert parse_mentions("just some plain text") == []
    assert parse_mentions("") == []
