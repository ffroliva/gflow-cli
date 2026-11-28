"""The README copies of the per-command browser-mode table match `docs/BROWSER_MODES.md`."""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]


def _groups(text: str) -> list[list[str]]:
    """Each table's command cells (the first cell of rows starting with a backtick), in order."""
    groups: list[list[str]] = []
    in_table = False
    for line in text.splitlines():
        if not line.startswith("|"):
            in_table = False
            continue
        if not in_table:
            groups.append([])
            in_table = True
        if m := re.match(r"\| (`[^|]+?) \|", line):
            groups[-1].append(m.group(1))
    return [g for g in groups if g]


@pytest.mark.parametrize("readme", ["README.md", "README.zh-CN.md"])
def test_browser_modes_command_column_matches_in_doc_readme_and_zh(readme: str) -> None:
    doc = _groups((_REPO / "docs" / "BROWSER_MODES.md").read_text(encoding="utf-8"))
    text = (_REPO / readme).read_text(encoding="utf-8")
    block = re.search(r"<!-- browser-modes-table -->(.*?)<!-- /browser-modes-table -->", text, re.S)
    assert block, f"{readme}: no browser-modes-table block"
    assert len(doc) == 3, doc
    assert _groups(block.group(1)) == doc
    assert "docs/BROWSER_MODES.md" in block.group(1)
