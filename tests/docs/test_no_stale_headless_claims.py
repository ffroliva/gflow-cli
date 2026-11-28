"""Docs must not repeat the pre-v0.108.0 claim that a headless browser cannot mint (#1371)."""

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_STALE = (
    "rejects headless",
    "Headless Chromium is rejected",
    "headless mode *causes*",
    "cannot create in a headless browser",
)
_HISTORY = re.compile(r"<!-- history -->.*?<!-- /history -->", re.DOTALL)


def _docs() -> list[Path]:
    globs = ("README*.md", "AGENTS.md", "KNOWN_ISSUES.md", "skills/**/SKILL.md", "docs/*.md")
    return [p for g in globs for p in _REPO.glob(g)]


def test_no_stale_headless_claims() -> None:
    hits = [
        f"{path.relative_to(_REPO)}: {phrase!r}"
        for path in _docs()
        for phrase in _STALE
        if phrase in _HISTORY.sub("", path.read_text(encoding="utf-8"))
    ]
    assert not hits, hits


def test_macos_is_stated_unmeasured() -> None:
    for name in ("README.md", "docs/ACCOUNT_SAFETY.md"):
        text = (_REPO / name).read_text(encoding="utf-8")
        assert re.search(r"macOS.{0,120}?unmeasured", text, re.DOTALL), name
