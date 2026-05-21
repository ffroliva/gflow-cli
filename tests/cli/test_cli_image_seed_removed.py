"""TDD red tests for commit #1b: --seed flag removal from gflow image t2i and i2i.

These tests fail now (--seed still accepted) and pass after Task 2.1 deletes
the click option. Spec: docs/superpowers/specs/2026-05-21-multi-image-prompt-design.md §1, §12 D8.
"""

from __future__ import annotations

from click.testing import CliRunner

from gflow_cli.cli import main as cli


def test_t2i_rejects_seed_flag() -> None:
    """--seed removed from `gflow image t2i` in commit #1b."""
    runner = CliRunner()
    result = runner.invoke(cli, ["image", "t2i", "--seed", "42", "dummy prompt"])
    assert result.exit_code != 0, (
        f"--seed should be unknown; got exit {result.exit_code}, output:\n{result.output}"
    )
    assert "no such option" in result.output.lower() or "--seed" in result.output


def test_i2i_rejects_seed_flag() -> None:
    """--seed removed from `gflow image i2i` in commit #1b."""
    runner = CliRunner()
    result = runner.invoke(cli, ["image", "i2i", "--seed", "42", "--ref", "x.png", "dummy prompt"])
    assert result.exit_code != 0, (
        f"--seed should be unknown; got exit {result.exit_code}, output:\n{result.output}"
    )
    assert "no such option" in result.output.lower() or "--seed" in result.output
