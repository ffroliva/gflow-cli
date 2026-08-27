"""The account locale is cached per profile — FOUR states, not two (#587).

    None                 -> never probed
    PROVISIONAL ("?")    -> ONE no-redirect observation; probe again
    NOT_REDIRECTED ("")  -> two agreed; skip the settle
    "pt"                 -> the account's locale segment

`await_url_settled` returns None for both "Flow does not redirect this account"
and "the settle timed out this once", so a single observation cannot be trusted
to disable the settle permanently. Storage mirrors the existing `.gflow_account`
dotfile.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.profile_store import (
    LOCALE_FILE,
    NOT_REDIRECTED,
    PROVISIONAL,
    next_locale_state,
    read_account_locale,
    write_account_locale,
)


def test_never_probed_reads_as_none(tmp_path: Path) -> None:
    """No file at all means "ask Flow", not "no locale"."""
    assert read_account_locale(tmp_path) is None


def test_no_redirect_is_recorded_and_is_not_none(tmp_path: Path) -> None:
    """The whole point of the issue.

    An account Flow does not redirect resolves to ``None``. Persisting that as
    "nothing written" would make it re-probe forever — and it is precisely the
    account paying the 4 s. It must come back as ``""``: falsy for the caller
    that wants a locale, distinct from ``None`` for the caller asking whether we
    have ever looked.
    """
    write_account_locale(tmp_path, None)

    cached = read_account_locale(tmp_path)
    assert cached == ""
    assert cached is not None


def test_a_later_probe_overwrites_an_earlier_one(tmp_path: Path) -> None:
    write_account_locale(tmp_path, "pt")
    write_account_locale(tmp_path, "de")
    assert read_account_locale(tmp_path) == "de"


@pytest.mark.parametrize(
    "junk",
    ["../../etc/passwd", "pt-BR", "PT", "en_US", "toolong", "x", "https://evil", "1"],
    ids=["traversal", "full-tag", "upper", "underscore", "too-long", "too-short", "url", "digit"],
)
def test_garbage_reads_as_never_probed(tmp_path: Path, junk: str) -> None:
    """A malformed file must degrade to "probe again", never be used as a URL segment.

    This value is interpolated into a URL path. Anything that is not a bare
    lowercase 2-3 letter segment is rejected on READ, so a hand-edited or
    corrupted file self-heals on the next run instead of building
    ``https://labs.google/fx/../../etc/passwd/tools/flow``.
    """
    (tmp_path / LOCALE_FILE).write_text(junk, encoding="utf-8")
    assert read_account_locale(tmp_path) is None


def test_whitespace_only_is_the_no_redirect_state(tmp_path: Path) -> None:
    """A trailing newline must not turn "no redirect" back into "never probed"."""
    (tmp_path / LOCALE_FILE).write_text("\n", encoding="utf-8")
    assert read_account_locale(tmp_path) == ""


def test_write_never_raises_on_an_unwritable_dir(tmp_path: Path) -> None:
    """Best-effort by construction.

    A cache write failing must never take down a generation run that has already
    done its real work. The cost of failing to persist is one wasted probe.
    """
    missing = tmp_path / "does" / "not" / "exist"
    write_account_locale(missing, "pt")  # must not raise
    assert read_account_locale(missing) is None


def test_undecodable_bytes_read_as_never_probed(tmp_path: Path) -> None:
    """A corrupt file must self-heal, not crash every listing command.

    Reproduced by the council: catching only `OSError` let `UnicodeDecodeError`
    escape `read_account_locale`, killing `project list`, `project show`, the MCP
    listing tool and every browser run — while the docstring promised the file
    "self-heals".
    """
    (tmp_path / LOCALE_FILE).write_bytes(b"\xff\xfe\x00pt")

    assert read_account_locale(tmp_path) is None


# --- the state machine that makes the poisoned state unreachable ------------


@pytest.mark.parametrize(
    ("cached", "observed", "expected"),
    [
        (None, None, PROVISIONAL),
        (PROVISIONAL, None, NOT_REDIRECTED),
        (NOT_REDIRECTED, None, NOT_REDIRECTED),
        ("pt", None, PROVISIONAL),
        (None, "pt", "pt"),
        (PROVISIONAL, "pt", "pt"),
        (NOT_REDIRECTED, "pt", "pt"),
        ("de", "pt", "pt"),
    ],
    ids=[
        "first-silence-is-provisional",
        "second-silence-commits",
        "committed-stays",
        "segment-then-silence-falls-back",
        "first-segment",
        "segment-clears-provisional",
        "segment-overrides-committed",
        "segment-replaces-segment",
    ],
)
def test_next_locale_state(cached: str | None, observed: str | None, expected: str) -> None:
    """Silence needs corroboration; a stated segment never does.

    The `("pt", None) -> PROVISIONAL` row is the whole point: one transient
    timeout on a redirecting account must not reach NOT_REDIRECTED, because that
    state skips the settle forever and looks identical to a real answer.
    """
    assert next_locale_state(cached, observed) == expected


def test_provisional_round_trips(tmp_path: Path) -> None:
    write_account_locale(tmp_path, PROVISIONAL)
    assert read_account_locale(tmp_path) == PROVISIONAL


def test_provisional_is_never_offered_as_a_locale(tmp_path: Path, monkeypatch) -> None:
    """PROVISIONAL is truthy — a `or None` collapse would leak "?" into a URL."""
    import gflow_cli.profile_store as ps

    monkeypatch.setattr(ps, "profile_dir", lambda _name: tmp_path)
    write_account_locale(tmp_path, PROVISIONAL)

    assert ps.account_locale_for("whoever") is None
