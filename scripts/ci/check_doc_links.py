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


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    bad = 0
    for rel in FILES:
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
    print(f"All links resolved across {len(FILES)} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
