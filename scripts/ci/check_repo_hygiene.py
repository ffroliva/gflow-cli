#!/usr/bin/env python3
"""CI + pre-commit hygiene gate.

Fails (exit 1) if:
  1. Forbidden file types are tracked by git (images, CDP lock files, generated artefacts).
  2. Any Python file in src/, tests/, or scripts/ contains hardcoded Windows absolute paths
     or writes output to test_assets/ instead of tmp/.

Run manually:
    uv run python scripts/ci/check_repo_hygiene.py

Run in CI:
    Added as a step in .github/workflows/ci.yml before lint.

Run as pre-commit hook (see .pre-commit-config.yaml):
    - id: repo-hygiene
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# 1. Tracked-file denylist
#    These patterns must NEVER appear in `git ls-files` output.
#    Add allowlist exceptions as negations in .gitignore instead.
# ---------------------------------------------------------------------------
TRACKED_DENYLIST: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\.gflow-cdp\.lock$"), "CDP lock file (browser session state)"),
    (re.compile(r"\.(jpg|jpeg)$", re.IGNORECASE), "image artefact — use tmp/ for outputs"),
    (re.compile(r"^test_assets/(smoke_|debug_)"), "generated output in test_assets/ — use tmp/"),
    # Catch profile directories accidentally created in the repo root.
    # Heuristic: any top-level directory whose only content is .gflow-cdp.lock.
    # The lock file rule above already catches the file; this documents the intent.
]

# ---------------------------------------------------------------------------
# 2. Source-code path denylist
#    Scanned in scripts/**/*.py to catch hardcoded machine-specific paths
#    and output-to-tracked-directory mistakes before they reach git.
# ---------------------------------------------------------------------------
SOURCE_DENYLIST: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"[A-Za-z]:\\[Uu]sers\\"),
        "hardcoded Windows user path — use auth.profile_dir(args.profile) instead",
    ),
    (
        re.compile(r"[A-Za-z]:/[Uu]sers/"),
        "hardcoded Windows user path (forward slash) — use auth.profile_dir(args.profile)",
    ),
    (
        re.compile(r"""[Pp]ath\s*\(\s*['"]test_assets/(?:smoke|debug)"""),
        "output written to test_assets/ — scripts must write to tmp/ instead",
    ),
    (
        re.compile(r"""['"]test_assets/(?:smoke_|debug_)"""),
        "test_assets/ output path in string literal — use tmp/",
    ),
]

SCAN_DIRS = ["scripts", "src", "tests"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git_ls_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    return result.stdout.splitlines()


def _check_tracked(files: list[str]) -> list[str]:
    violations: list[str] = []
    for f in files:
        for pattern, label in TRACKED_DENYLIST:
            if pattern.search(f):
                violations.append(f"  TRACKED   {f!r:60s}  ← {label}")
                break
    return violations


def _check_sources() -> list[str]:
    violations: list[str] = []
    for scan_dir in SCAN_DIRS:
        for path in (ROOT / scan_dir).rglob("*.py"):
            # Skip this file itself.
            if path == Path(__file__).resolve():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for pattern, label in SOURCE_DENYLIST:
                for m in pattern.finditer(text):
                    line_no = text[: m.start()].count("\n") + 1
                    rel = path.relative_to(ROOT)
                    violations.append(
                        f"  SOURCE    {str(rel)!r:60s}  line {line_no:<4d}  ← {label}\n"
                        f"            found: {m.group()!r}"
                    )
    return violations


def main() -> int:
    print("── repo hygiene check ───────────────────────────────────────")
    errors: list[str] = []

    tracked = _git_ls_files()
    errors += _check_tracked(tracked)
    errors += _check_sources()

    if errors:
        print(f"\n❌  {len(errors)} violation(s):\n")
        for e in errors:
            print(e)
        print(
            "\nRemediation:\n"
            "  Tracked artefact  →  git rm --cached <file>  and add pattern to .gitignore\n"
            "  Hardcoded path    →  replace with auth.profile_dir(args.profile)\n"
            "  Output to test_assets/  →  change OUT to Path('tmp/...')\n"
        )
        return 1

    print(f"✅  {len(tracked)} tracked files checked — no violations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
