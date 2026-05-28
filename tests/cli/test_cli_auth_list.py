"""Regression tests for `gflow auth list` rendering on non-UTF-8 consoles.

Covers issue #82 (`UnicodeEncodeError` on cp1252 / cmd.exe when the default
profile marker `●` cannot be encoded).
"""

from __future__ import annotations

import io
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gflow_cli import cli as cli_mod
from gflow_cli.cli import (
    _default_marker_glyph,
    _profile_name_from_account,
    _render_profiles_table,
)
from gflow_cli.profile_store import _SAFE_PROFILE_NAME_RE, ProfileMeta


@pytest.mark.parametrize(
    ("encoding", "expected"),
    [
        ("utf-8", "●"),
        ("UTF-8", "●"),
        ("cp65001", "●"),
        ("cp1252", "*"),
        ("ascii", "*"),
        ("latin-1", "*"),
        (None, "*"),
        ("", "*"),
        ("bogus-encoding-xyz", "*"),
    ],
)
def test_default_marker_glyph_picks_safe_fallback(encoding: str | None, expected: str) -> None:
    assert _default_marker_glyph(encoding) == expected


def _make_profile(
    *,
    name: str = "default",
    is_default: bool = True,
    google_account: str | None = None,
    tmp_path: Path,
) -> ProfileMeta:
    profile_dir = tmp_path / f"profile_{name}"
    profile_dir.mkdir(parents=True, exist_ok=True)
    return ProfileMeta(
        name=name,
        profile_dir=profile_dir,
        is_default=is_default,
        cookies_present=True,
        last_used_at=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
        google_account=google_account,
    )


def test_render_profiles_table_does_not_crash_on_cp1252(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rendering the marker for the default profile must not raise
    UnicodeEncodeError when the underlying stream is cp1252-encoded."""
    buf = io.BytesIO()
    cp1252_stream = io.TextIOWrapper(buf, encoding="cp1252", newline="")

    # Force the helper to see a cp1252 encoding regardless of host stdout.
    monkeypatch.setattr(sys, "stdout", cp1252_stream, raising=False)

    # Replace the module-level Rich Console with one that writes to the cp1252
    # stream so any glyph rendered hits the cp1252 encoder for real.
    from rich.console import Console

    monkeypatch.setattr(
        cli_mod,
        "console",
        Console(file=cp1252_stream, force_terminal=False, legacy_windows=False),
    )

    profiles = [_make_profile(name="default", is_default=True, tmp_path=tmp_path)]

    _render_profiles_table(profiles)
    cp1252_stream.flush()

    rendered = buf.getvalue().decode("cp1252")
    assert "default" in rendered
    assert "●" not in rendered
    assert "*" in rendered


def test_render_profiles_table_shows_google_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 'Google account' column shows the email when present, 'unknown' otherwise."""
    import io

    from rich.console import Console

    buf = io.StringIO()
    monkeypatch.setattr(
        cli_mod, "console", Console(file=buf, force_terminal=False, highlight=False, width=200)
    )

    profiles = [
        _make_profile(name="alice", google_account="alice@example.com", tmp_path=tmp_path),
        _make_profile(name="bob", google_account=None, tmp_path=tmp_path),
    ]
    _render_profiles_table(profiles)

    rendered = buf.getvalue()
    assert "alice@example.com" in rendered
    assert "unknown" in rendered


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("ffroliva@gmail.com", "ffroliva"),
        ("dev@axelate.io", "dev"),
        ("flavio.oliva@gmail.com", "flavio-oliva"),
        ("john.smith@example.com", "john-smith"),
        ("user+flow@example.com", "user-flow"),
        ("a.b.c@example.com", "a-b-c"),
        ("under_score@example.com", "under_score"),
    ],
)
def test_profile_name_from_account_sanitizes_local_part(email: str, expected: str) -> None:
    """Email local-parts with '.'/'+' (Gmail dots, aliases, firstname.lastname)
    must become a name that rename_profile's _SAFE_PROFILE_NAME_RE accepts —
    otherwise auth_login raises an uncaught ValueError after the session is saved."""
    result = _profile_name_from_account(email)
    assert result == expected
    assert result is not None and _SAFE_PROFILE_NAME_RE.match(result)


@pytest.mark.parametrize("email", ["....@example.com", "@example.com", "++@example.com", ""])
def test_profile_name_from_account_returns_none_when_nothing_usable(email: str) -> None:
    """When the local-part has no safe characters, return None so the caller
    keeps the existing profile name instead of attempting an invalid rename."""
    assert _profile_name_from_account(email) is None
