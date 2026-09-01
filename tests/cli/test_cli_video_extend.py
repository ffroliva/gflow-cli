"""Tests for `gflow video extend` — the extend primitive at the CLI seam.

Driven through `CliRunner`, with the async runner patched so no browser, no
network and no credits are involved.

Two things are deliberately pinned as behaviour, not implementation detail,
because the predict council identified both as the difference between a safe
command and an expensive one:

* nothing is spent before the user has seen the cost, and
* `1:1` is refused at the Click boundary — Flow has no square extend model in
  either family, so accepting it could only ever produce a late failure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner

from gflow_cli.cli import main as cli

if TYPE_CHECKING:
    pass

MEDIA = "b9458021-fc2d-4d95-ab53-cf844c6f1079"
PROJECT = "7d3d6bd9-a39f-4c2d-b772-146e73e539cf"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_rejects_square_aspect_at_the_boundary(runner: CliRunner) -> None:
    """No SQUARE key exists in either extend family, so this can never succeed.
    Refusing in Click means it costs nothing and says so immediately."""
    result = runner.invoke(
        cli, ["video", "extend", MEDIA, "keep going", "--aspect", "1:1", "--yes"]
    )
    assert result.exit_code == 2
    assert "1:1" in result.output or "aspect" in result.output.lower()


def test_dry_run_spends_nothing(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """--dry-run must not construct a client, let alone submit. The council's
    rule: the cost gate comes before anything billable exists."""
    called: list[str] = []

    def _boom(*_a: Any, **_k: Any) -> None:
        called.append("ran")
        raise AssertionError("dry-run must not reach the runner")

    monkeypatch.setattr("gflow_cli.cli_video.run_with_handlers", _boom)
    result = runner.invoke(
        cli, ["video", "extend", MEDIA, "keep going", "--project", PROJECT, "--dry-run"]
    )
    assert called == []
    assert result.exit_code == 0
    assert "extend" in result.output.lower()


def test_rejects_a_malformed_media_id(runner: CliRunner) -> None:
    """Fail before any network call rather than after a token has been minted."""
    result = runner.invoke(
        cli, ["video", "extend", "not-a-uuid", "keep going", "--yes", "--dry-run"]
    )
    assert result.exit_code != 0
    assert "uuid" in result.output.lower() or "invalid" in result.output.lower()


def test_help_names_the_cost_and_the_ceiling(runner: CliRunner) -> None:
    """`--help` is the only documentation most callers read. It must say that
    this spends credits and that a segment is 8s, because both drive the
    decision to run it."""
    result = runner.invoke(cli, ["video", "extend", "--help"])
    assert result.exit_code == 0
    low = result.output.lower()
    assert "credit" in low
    assert "8" in result.output
