"""Release-protocol guard — a release PR must carry the /gflow:release artifacts.

Enforces (for the version in pyproject.toml):
  1. ``src/gflow_cli/__init__.py`` ``__version__`` parity,
  2. a ``## [<version>]`` CHANGELOG section,
  3. CHANGELOG footer links: ``[<version>]: …compare/…v<version>`` and
     ``[Unreleased]: …compare/v<version>...HEAD``,
  4. ``docs/LIVE_VERIFICATION_v<version>.md`` exists (the step-4b gate),
  5. ``docs/INDEX.md`` references that live-verification doc.

Run by the main-base guard workflow on PRs targeting ``main`` (release PRs).
Born 2026-07-17: v0.38.1 was initially cut WITHOUT /gflow:release — the missing
live-verification gate would have shipped a fix whose primary bug (the
composer-render race) only the live run exposed. Owner directive: all releases
must follow /gflow:release; this guard makes the documentation half structural.

Usage: python scripts/ci/check_release_artifacts.py [repo_root]
Exit 0 = all artifacts present; exit 1 = violations (each printed).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def find_violations(root: Path) -> list[str]:
    """Return human-readable violations for the release version in pyproject."""
    violations: list[str] = []
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version = "([^"]+)"', pyproject, re.M)
    if not m:
        return ['pyproject.toml: no version = "…" found']
    version = m.group(1)

    init_text = (root / "src" / "gflow_cli" / "__init__.py").read_text(encoding="utf-8")
    if f'__version__ = "{version}"' not in init_text:
        violations.append(f"src/gflow_cli/__init__.py: __version__ != {version} (pyproject)")

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(rf"^## \[{re.escape(version)}\]", changelog, re.M):
        violations.append(f"CHANGELOG.md: missing '## [{version}]' section")
    footer_re = rf"^\[{re.escape(version)}\]: .+compare/.+v{re.escape(version)}"
    if not re.search(footer_re, changelog, re.M):
        violations.append(f"CHANGELOG.md: footer missing '[{version}]: compare' link")
    if f"compare/v{version}...HEAD" not in changelog:
        violations.append(
            f"CHANGELOG.md: [Unreleased] footer link not updated to compare/v{version}...HEAD"
        )

    lv = root / "docs" / f"LIVE_VERIFICATION_v{version}.md"
    if not lv.exists():
        violations.append(
            f"docs/LIVE_VERIFICATION_v{version}.md missing (step 4b of /gflow:release)"
        )
    elif f"LIVE_VERIFICATION_v{version}" not in (root / "docs" / "INDEX.md").read_text(
        encoding="utf-8"
    ):
        violations.append(f"docs/INDEX.md: no reference to LIVE_VERIFICATION_v{version}.md")

    return violations


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
    violations = find_violations(root)
    if violations:
        for v in violations:
            print(f"::error title=Release protocol violation::{v}")
        print(
            f"\n{len(violations)} violation(s) — releases must follow /gflow:release "
            "(skills/release/SKILL.md)."
        )
        return 1
    print("release artifacts OK — /gflow:release protocol satisfied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
