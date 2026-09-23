# SPDX-License-Identifier: MIT
"""Failure arms of `gflow auth use` / `auth logout` and of `_resolve_or_exit`.

WHY a module of its own. Every other auth-flavoured test file here is pinned by
its own docstring to one narrow concern — ``test_cli_auth_list.py`` to the #82
cp1252 marker, ``test_auth_status.py`` to the #471 session probe,
``tests/features/auth.feature`` to the happy paths, and
``test_error_handling.py`` to ``auth login``. None of them is a home for "what
does profile *selection* print when it fails", which is how four
``console.print`` error arms in ``cli.py`` reached PR #817 with nothing behind
them at all.

WHY the bracket assertions. These arms interpolate an exception's text into a
Rich markup string. Rich reads a ``[...]`` run whose first character is
lowercase (or ``#``/``/``/``@``) as a style tag and, when that style will not
parse, drops it from the output **silently** — no error, no marker, just
missing text. An unescaped ``f"[red]{exc}[/red]"`` therefore deletes
``[chain]`` out of a "pip install gflow-cli[chain]" hint and ``[chain]`` out of
a profile name. ``rich.markup.escape()`` is the fix (#813); asserting that a
lowercase-bracketed token reaches the user is what keeps it. A test asserting
only "some error text appeared" passes with the escape reverted and is worth
nothing here.
"""

from __future__ import annotations

import re

import pytest
from click.testing import CliRunner

from gflow_cli.cli import main
from gflow_cli.config import get_settings

_ANSI_SGR = re.compile(r"\x1b\[[0-9;]*m")


def _plain(output: str) -> str:
    """Strip ANSI and collapse Rich's line wrapping so substring asserts hold.

    Same helper as ``tests/cli/test_cli_video_chain.py`` — a ``FORCE_COLOR``
    environment leaks SGR codes into otherwise plain text (memory
    ``force-color-breaks-cli-tests``), and Rich soft-wraps at the console width.
    """
    return " ".join(_ANSI_SGR.sub("", output).split())


@pytest.fixture(autouse=True)
def _wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop Rich hard-breaking the long tmp paths these messages carry.

    ``_plain`` re-joins soft wraps with a space, which is right for prose but
    would split ``profile_p[chain]`` in the middle of the token when Rich runs
    out of width inside a single long word. Rich re-reads ``COLUMNS`` on every
    print, so widening it here is enough — no console object is replaced.
    """
    monkeypatch.setenv("COLUMNS", "300")


def _make_profile_dir(name: str) -> None:
    """Create ``$GFLOW_CLI_HOME/profile_<name>`` so ``list_profiles`` finds it."""
    get_settings().profile_subdir(name).mkdir(parents=True, exist_ok=True)


# --- cli.py:439 — `auth use` on a profile that is not there ------------------


def test_auth_use_unknown_profile_exits_2_and_keeps_bracketed_name() -> None:
    """`gflow auth use` on a missing profile: exit 2, and the name is intact.

    ``set_default_profile`` raises ``FileNotFoundError`` naming the directory it
    looked for. A profile called ``p[chain]`` is a legal name (``paths.py``
    interpolates it verbatim), so the message carries a lowercase-bracketed run
    that Rich would eat unescaped — leaving the user hunting for ``profile_p``.
    """
    result = CliRunner().invoke(main, ["auth", "use", "p[chain]"])

    assert result.exit_code == 2, result.output
    out = _plain(result.output)
    assert "Profile dir not found" in out
    assert "profile_p[chain]" in out
    assert "Default profile set to" not in out


# --- cli.py:460 — `auth logout` on a profile that is not there ---------------


def test_auth_logout_unknown_profile_exits_2_and_keeps_bracketed_name() -> None:
    """`gflow auth logout --yes` on a missing profile: exit 2, name intact.

    ``--yes`` skips the ``click.confirm(abort=True)`` so the test exercises the
    delete arm rather than the confirmation prompt.
    """
    result = CliRunner().invoke(main, ["auth", "logout", "--profile", "p[chain]", "--yes"])

    assert result.exit_code == 2, result.output
    out = _plain(result.output)
    assert "Profile dir not found" in out
    assert "profile_p[chain]" in out
    assert "removed" not in out


# --- cli.py:470 — _resolve_or_exit, nothing to resolve at all ----------------


def test_command_without_profile_exits_2_when_no_profiles_exist() -> None:
    """A fresh $GFLOW_CLI_HOME has no profiles: exit 2 and send the user to login.

    ``auth status`` is the shortest command through ``_resolve_or_exit`` — the
    probe it would run afterwards is never reached, so this stays offline.
    """
    result = CliRunner().invoke(main, ["auth", "status"])

    assert result.exit_code == 2, result.output
    out = _plain(result.output)
    assert "No profiles found" in out
    assert "gflow auth login" in out


# --- cli.py:473 — _resolve_or_exit, several profiles and no default ----------


def test_command_without_profile_exits_2_and_lists_bracketed_candidates() -> None:
    """Two profiles, no default: exit 2 listing the names the user can pick.

    ``NoDefaultProfileError`` builds its message out of the discovered directory
    names, so a profile named ``alpha[chain]`` puts untrusted-shaped text
    straight into a Rich markup string. Unescaped, the list renders as
    "alpha, beta" — a name the user cannot then pass to ``gflow auth use``.
    """
    _make_profile_dir("alpha[chain]")
    _make_profile_dir("beta")

    result = CliRunner().invoke(main, ["auth", "status"])

    assert result.exit_code == 2, result.output
    out = _plain(result.output)
    assert "Cannot pick a default profile" in out
    assert "alpha[chain]" in out
    assert "beta" in out
    assert "gflow auth use" in out
