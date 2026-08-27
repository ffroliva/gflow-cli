"""The account locale is cached per profile — with THREE states, not two (#587).

#580 resolves the account locale by settling the bootstrap navigation. That probe
costs the full ``URL_SETTLE_TIMEOUT_MS`` (4 s, ``_common.py:153``) on any account
Flow does **not** redirect, because ``wait_for_url`` never matches and the wait
runs to timeout. Measured: 6.1 s setup on an ``en`` account vs 3.4 s on a
redirecting ``pt`` one.

A two-state cache (value / no value) does not fix that account — "no locale" is
exactly its answer, and storing it as "nothing" is indistinguishable from "never
asked". So the cache encodes three states:

    None  -> never probed; the caller MUST run the live probe
    ""    -> probed; Flow does not redirect this account. Skip the probe.
    "pt"  -> probed; this is the account's locale segment.

Storage mirrors ``.gflow_account`` (``profile_store.py``, written at
``internal_chromium.py:185``): a sibling dotfile in the profile dir. No schema
migration, no config plumbing, and it is readable offline by ``project list``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.profile_store import LOCALE_FILE, read_account_locale, write_account_locale


def test_never_probed_reads_as_none(tmp_path: Path) -> None:
    """No file at all means "ask Flow", not "no locale"."""
    assert read_account_locale(tmp_path) is None


def test_a_resolved_segment_round_trips(tmp_path: Path) -> None:
    write_account_locale(tmp_path, "pt")
    assert read_account_locale(tmp_path) == "pt"


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
