"""`docs/BROWSER_MODES.md` answers "does this command open a window" for real commands."""

import re
from pathlib import Path

import click

from gflow_cli.cli import main

_DOC = Path(__file__).resolve().parents[2] / "docs" / "BROWSER_MODES.md"
_GROUPS = ("## Headless", "## Headed", "## No browser")


def _commands(group: click.Group, prefix: tuple[str, ...] = ()) -> set[str]:
    out: set[str] = set()
    for name, cmd in group.commands.items():
        path = (*prefix, name)
        out.add(" ".join(path))
        if isinstance(cmd, click.Group):
            out |= _commands(cmd, path)
    return out


def _table_commands(text: str) -> list[str]:
    """The first backticked token of each table row's first cell."""
    rows = [line for line in text.splitlines() if line.startswith("| `")]
    return [m.group(1) for row in rows if (m := re.match(r"\| `([^`]+)`", row))]


def test_every_listed_command_exists() -> None:
    known = _commands(main)
    listed = _table_commands(_DOC.read_text(encoding="utf-8"))
    assert len(listed) >= 30, listed
    unknown = [c for c in listed if c.split(" --")[0] not in known]
    assert not unknown, unknown


def test_three_groups_and_the_stated_limits() -> None:
    text = _DOC.read_text(encoding="utf-8")
    for heading in _GROUPS:
        assert heading in text, heading
    assert re.search(r"macOS.{0,120}?unmeasured", text, re.DOTALL)  # SCENARIO row 21
    assert "#1554" in text
    assert "GFLOW_CLI_TRANSPORT=ui_automation" in text
    assert "```mermaid" in text
