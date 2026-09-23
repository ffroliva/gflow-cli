"""Validate internal markdown links in selected docs.

Walks each input file, extracts every `[text](path)` link whose target is a
relative path (no scheme), and verifies the target file exists. Anchors (`#foo`)
are checked only for file existence, not anchor presence. External links
(`http://`, `https://`, `mailto:`) are skipped.

Exit code 0 = all good; 1 = at least one broken link. Print broken links to
stdout with the source file + line number.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Files we audit for this release. Add new entries when new docs land.
FILES: tuple[str, ...] = (
    "README.md",
    "AGENTS.md",
    "llms.txt",
    "CLAUDE.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "RELEASE.md",
    "docs/INDEX.md",
    "docs/AGENT_GUIDE.md",
    "docs/AUTHENTICATION.md",
    "docs/PROJECT_STATUS.md",
    "docs/ARCHITECTURE.md",
    "docs/CONFIGURATION.md",
    "docs/DATA_LAYER.md",
    "docs/DEVELOPMENT.md",
    "docs/E2E_TESTING.md",
    "docs/EXTERNAL_STORAGE.md",
    "docs/GITHUB.md",
    "docs/SECURITY.md",
    "docs/USAGE.md",
    "docs/USER_GUIDE.md",
    "docs/MCP.md",
    "docs/LIVE_VERIFICATION_v0.8.1.md",
    "docs/LIVE_VERIFICATION_v0.57.0.md",
    "docs/CHARACTER.md",
    "docs/REFERENCE_STRATEGIES.md",
    "docs/MOVIE.md",
    "docs/ASSET_TAGGING_RECON.md",
    "docs/LIVE_VERIFICATION_v0.27.1.md",
)

# [text](target) — non-greedy text, balanced target (no nested parens).
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def is_external(target: str) -> bool:
    return target.startswith(("http://", "https://", "mailto:", "#"))


def check_file(path: Path, repo_root: Path) -> list[tuple[int, str, str]]:
    """Return a list of (lineno, target, reason) for each broken link."""
    broken: list[tuple[int, str, str]] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in LINK_RE.finditer(line):
            target = match.group(1).strip()
            if is_external(target):
                continue
            # Strip an anchor fragment.
            target_path_str = target.split("#", 1)[0]
            if not target_path_str:
                # Pure-anchor link — points to a section in this same file.
                continue
            target_path = (path.parent / target_path_str).resolve()
            if not target_path.exists():
                # Try as repo-root relative.
                root_path = (repo_root / target_path_str).resolve()
                if not root_path.exists():
                    broken.append((lineno, target, "file not found"))
    return broken


#: Directories audited WHOLE, rather than file-by-file in ``FILES``. A spike note is
#: written once and never edited again, so an allowlist that must be extended by hand is
#: an allowlist that silently stops covering them -- before this, every note under
#: ``docs/superpowers/spikes/`` was outside the gate, and a green run said nothing about
#: any of their links. All 34 existing notes pass, so there is no debt being grandfathered.
#:
#: ``docs/*.md`` is globbed for exactly the same reason, and it was found the same way: a
#: new page was added, the gate reported "All links resolved", and a deliberately broken
#: control link in it passed untouched. ``FILES`` listed 22 of the 122 top-level docs, so
#: 100 of them -- every ``LIVE_VERIFICATION_*`` record among them -- were outside the gate
#: while it reported green. Globbing raised coverage from 64 files to 186 and surfaced 9
#: real broken links (dead absolute ``file://`` paths that only ever resolved on the
#: author's own machine, and specs/plans since consolidated into memory); all 9 are fixed,
#: so again
#: nothing is grandfathered. Add a page and it is covered -- no list to remember.
GLOB_DIRS: tuple[tuple[str, str], ...] = (("docs/superpowers/spikes", "*.md"), ("docs", "*.md"))


def _audited(repo_root: Path) -> list[str]:
    """Every path this gate checks: the explicit list plus the globbed directories."""
    found = list(FILES)
    for rel_dir, pattern in GLOB_DIRS:
        found.extend(
            p.relative_to(repo_root).as_posix() for p in sorted((repo_root / rel_dir).glob(pattern))
        )
    return found


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    bad = 0
    audited = _audited(repo_root)
    for rel in audited:
        path = repo_root / rel
        if not path.exists():
            print(f"{rel}: FILE MISSING")
            bad += 1
            continue
        for lineno, target, reason in check_file(path, repo_root):
            print(f"{rel}:{lineno}  →  {target}  ({reason})")
            bad += 1
    if bad:
        print(f"\n{bad} broken link(s)")
        return 1
    print(f"All links resolved across {len(audited)} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
