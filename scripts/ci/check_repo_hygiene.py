#!/usr/bin/env python3
"""CI + pre-commit hygiene gate.

Fails (exit 1) if:
  1. Forbidden file types are tracked by git (images, CDP lock files, generated artefacts).
  2. A tracked top-level *.md / *.py file is outside ROOT_DOC_ALLOWLIST (stray
     planning / review / session artefact at the repo root).
  3. Any Python file in src/, tests/, or scripts/ contains hardcoded Windows absolute paths
     or writes output to test_assets/ instead of tmp/.
  4. The three declared versions disagree: pyproject.toml [project].version,
     src/gflow_cli/__init__.py __version__, and .codex-plugin/plugin.json
     "version" must be identical (a release bumping one but not the others
     ships a self-contradictory artefact).

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
# 2b. Root-level doc/script allowlist
#    Tracked top-level *.md and *.py files must be in this set. Anything else is
#    a stray planning / review / session artifact (e.g. a shipped-PR review doc
#    or an agent session marker) that belongs in docs/, auto-memory, or nowhere.
#    This is what would have caught PR162_MOVIE_CHARACTER_REVIEW.md and
#    .continue-here.md before they were committed (project-health audit, 2026-06).
#    Add a genuinely new canonical root doc here; route everything else into docs/.
# ---------------------------------------------------------------------------
ROOT_DOC_ALLOWLIST: frozenset[str] = frozenset(
    {
        "AGENTS.md",
        "CHANGELOG.md",
        "CLAUDE.md",
        "CONFIGURATION.md",
        "CONTRIBUTING.md",
        "DISCLAIMER.md",
        "KNOWN_ISSUES.md",
        "PLAN.md",
        "README.md",
        "RELEASE.md",
        "ROADMAP.md",
        "conftest.py",  # root pytest conftest (basetemp + directory-based marker tagging)
    }
)

# ---------------------------------------------------------------------------
# 3. Branch-naming advisory
#    AGENTS.md mandates conventional branch prefixes. This is ADVISORY only:
#    it warns but never fails the gate. Rationale: it no-ops in CI (pull_request
#    checks out a detached HEAD → "HEAD"), and automation platforms (Claude Code
#    on the web, dependabot) create branches the contributor cannot rename, so a
#    hard block would break those workflows. Normal contributors still get the
#    nudge on their dev machine.
# ---------------------------------------------------------------------------
BRANCH_PREFIX_RE = re.compile(r"^(feature|bugfix|hotfix|chore|docs|test|release)/")
_PROTECTED_OR_DETACHED = frozenset({"main", "develop", "HEAD"})


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


def _check_root_docs(files: list[str]) -> list[str]:
    """Flag tracked top-level ``*.md`` / ``*.py`` files outside the allowlist.

    Top-level = no ``/`` in the git path. Dotfiles like ``.continue-here.md`` are
    included (they have no path separator and end in ``.md``).
    """
    violations: list[str] = []
    for f in files:
        if "/" in f:
            continue
        if f.endswith((".md", ".py")) and f not in ROOT_DOC_ALLOWLIST:
            violations.append(
                f"  ROOTDOC   {f!r:60s}  ← stray root doc/script; move it under docs/ "
                "(or extract to memory and delete), or add it to ROOT_DOC_ALLOWLIST "
                "in scripts/ci/check_repo_hygiene.py if it is a new canonical root doc"
            )
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


def _current_branch() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=ROOT,
        )
    except (subprocess.CalledProcessError, OSError):
        return None
    return result.stdout.strip() or None


def _check_branch_name(branch: str | None) -> list[str]:
    """Advisory branch-name check. Returns warnings, never errors.

    No-ops on an unresolved branch, a detached HEAD ("HEAD"), and the protected
    `main` / `develop` branches. Callers MUST treat the result as advisory and
    keep it out of the exit-1 error list.
    """
    if branch is None or branch in _PROTECTED_OR_DETACHED:
        return []
    if BRANCH_PREFIX_RE.match(branch):
        return []
    return [
        f"  BRANCH    {branch!r}  ← non-conventional name; prefer one of: "
        "feature/ bugfix/ hotfix/ chore/ docs/ test/ release/ (advisory)"
    ]


def _check_version_agreement() -> list[str]:
    """pyproject == __init__ == plugin.json — one version, three declarations.

    tomllib is 3.11+; the pre-commit hook may run under an older interpreter,
    so the import failure degrades to a graceful skip instead of a traceback
    (CI runs the gate on 3.11+ where the check is authoritative).
    """
    versions: dict[str, str] = {}
    try:
        import json
        import tomllib
    except ImportError:
        return []
    try:
        with (ROOT / "pyproject.toml").open("rb") as fh:
            versions["pyproject.toml"] = str(tomllib.load(fh)["project"]["version"])
        init_text = (ROOT / "src" / "gflow_cli" / "__init__.py").read_text(encoding="utf-8")
        match = re.search(r'^__version__\s*=\s*"([^"]+)"', init_text, re.MULTILINE)
        if match is None:
            return ["src/gflow_cli/__init__.py: __version__ assignment not found"]
        versions["src/gflow_cli/__init__.py"] = match.group(1)
        plugin_raw = (ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        versions[".codex-plugin/plugin.json"] = str(json.loads(plugin_raw)["version"])
    except (OSError, KeyError, ValueError) as exc:
        return [f"version-agreement check could not read a source: {exc}"]
    if len(set(versions.values())) > 1:
        listing = ", ".join(f"{src}={ver}" for src, ver in versions.items())
        return [f"version disagreement — {listing} (bump all three together)"]
    return []


def main() -> int:
    print("── repo hygiene check ───────────────────────────────────────")
    errors: list[str] = []

    tracked = _git_ls_files()
    errors += _check_tracked(tracked)
    errors += _check_root_docs(tracked)
    errors += _check_sources()
    errors += _check_version_agreement()

    # Advisory: warn on non-conventional branch names but never fail the gate.
    warnings = _check_branch_name(_current_branch())
    if warnings:
        print("\n⚠️  advisory (non-blocking):")
        for w in warnings:
            print(w)

    if errors:
        print(f"\n❌  {len(errors)} violation(s):\n")
        for e in errors:
            print(e)
        print(
            "\nRemediation:\n"
            "  Tracked artefact  →  git rm --cached <file>  and add pattern to .gitignore\n"
            "  Stray root doc    →  move under docs/ (or extract to memory + delete), or\n"
            "                       add to ROOT_DOC_ALLOWLIST if it is a new canonical root doc\n"
            "  Hardcoded path    →  replace with auth.profile_dir(args.profile)\n"
            "  Output to test_assets/  →  change OUT to Path('tmp/...')\n"
        )
        return 1

    print(f"✅  {len(tracked)} tracked files checked — no violations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
