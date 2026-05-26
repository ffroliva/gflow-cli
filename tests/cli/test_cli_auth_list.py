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
from gflow_cli.cli import _default_marker_glyph, _render_profiles_table
from gflow_cli.profile_store import ProfileMeta


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


def _make_profile(*, name: str = "default", is_default: bool = True, tmp_path: Path) -> ProfileMeta:
    profile_dir = tmp_path / f"profile_{name}"
    profile_dir.mkdir(parents=True, exist_ok=True)
    return ProfileMeta(
        name=name,
        profile_dir=profile_dir,
        is_default=is_default,
        cookies_present=True,
        last_used_at=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
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
