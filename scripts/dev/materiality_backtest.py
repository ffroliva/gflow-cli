#!/usr/bin/env python3
"""Backtest the advisory materiality gate against real git history.

Answers two questions the predict council could only *estimate*:

  1. False-positive rate (contributor friction) — of commits that touch a
     material path, how many were *trivial* (comment/blank/whitespace/rename
     only, i.e. no substantive logic change)? A high number means the
     path-only gate is too blunt and should classify on diff content.

  2. Coverage / recall (does the gate cover what breaks) — of the commits that
     *fixed* something (`fix:` / `hotfix` / `revert`), how many touched a
     material path? Fixes landing OUTSIDE material paths are candidate
     coverage gaps — areas that bite us but the gate ignores.

Zero API cost; pure git. Imports MATERIAL_PATHS from the classifier so the
backtest measures the EXACT gate that ships (single source of truth).

    uv run python scripts/dev/materiality_backtest.py            # full history
    uv run python scripts/dev/materiality_backtest.py --limit 100
    uv run python scripts/dev/materiality_backtest.py --range origin/main..HEAD

Heuristic honesty: "substantive" is judged from a whitespace-insensitive
(`git show -w`) diff of the material files only, counting any added/removed
line that is non-blank and not a `#` comment. This catches reformatting,
comment, blank-line and pure-rename churn; it does NOT detect Python
docstring-only edits (rare in material paths), so the false-positive rate
reported here is a conservative LOWER bound.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.ci.check_materiality import is_material  # noqa: E402

FIX_RE = re.compile(r"^(fix|hotfix|bugfix)\b", re.IGNORECASE)
FEAT_RE = re.compile(r"^(feat|feature)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------
def is_substantive_diff(diff_text: str) -> bool:
    """True if a (whitespace-insensitive) diff has real code churn.

    Lines that are blank or pure `#` comments after stripping don't count.
    A pure rename / whitespace-only diff yields no content lines -> False.
    """
    for line in diff_text.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith(("+", "-")):
            content = line[1:].strip()
            if content and not content.startswith("#"):
                return True
    return False


def classify_commit_kind(subject: str) -> str:
    """Bucket a commit by its subject line: fix | revert | feature | other."""
    head = subject.strip()
    if head.lower().startswith("revert"):
        return "revert"
    if FIX_RE.match(head):
        return "fix"
    if FEAT_RE.match(head):
        return "feature"
    return "other"


@dataclass(frozen=True)
class Record:
    sha: str
    subject: str
    kind: str
    material: bool
    substantive: bool  # only meaningful when material is True


def summarize(records: list[Record]) -> dict[str, object]:
    """Aggregate backtest metrics. Pure — testable without git."""
    total = len(records)
    material = [r for r in records if r.material]
    trivial = [r for r in material if not r.substantive]
    fixes = [r for r in records if r.kind in ("fix", "revert")]
    fixes_material = [r for r in fixes if r.material]
    fixes_gap = [r for r in fixes if not r.material]

    def pct(n: int, d: int) -> float:
        return round(100.0 * n / d, 1) if d else 0.0

    return {
        "total": total,
        "material_count": len(material),
        "material_pct": pct(len(material), total),
        "false_positive_count": len(trivial),
        "false_positive_pct": pct(len(trivial), len(material)),
        "fix_count": len(fixes),
        "fix_in_material_count": len(fixes_material),
        "coverage_pct": pct(len(fixes_material), len(fixes)),
        "coverage_gap_examples": fixes_gap[:8],
        "false_positive_examples": trivial[:8],
    }


def build_report(metrics: dict[str, object], rng: str) -> str:
    fp_examples = metrics["false_positive_examples"]
    gap_examples = metrics["coverage_gap_examples"]
    assert isinstance(fp_examples, list) and isinstance(gap_examples, list)

    lines = [
        "## Materiality gate backtest",
        f"_Range: `{rng}` — {metrics['total']} non-merge commits_",
        "",
        "### Axis 1 — false-positive rate (contributor friction)",
        f"- Material-path commits: **{metrics['material_count']}** "
        f"({metrics['material_pct']}% of all commits)",
        f"- …of which trivial (comment/blank/whitespace/rename only): "
        f"**{metrics['false_positive_count']}**",
        f"- **False-positive rate: {metrics['false_positive_pct']}%** (conservative lower bound)",
        "",
        "Trivial material-flagged commits (would have been needlessly nudged):",
        *(f"  - `{r.sha[:9]}` {r.subject}" for r in fp_examples),
        "" if fp_examples else "  - (none)",
        "",
        "### Axis 2 — coverage (do fixes land in material paths?)",
        f"- Fix/revert commits: **{metrics['fix_count']}**",
        f"- …touching a material path: **{metrics['fix_in_material_count']}** "
        f"(**{metrics['coverage_pct']}%** coverage)",
        "",
        "Fixes OUTSIDE material paths (candidate coverage gaps):",
        *(f"  - `{r.sha[:9]}` {r.subject}" for r in gap_examples),
        "" if gap_examples else "  - (none)",
        "",
        "### Reading the numbers",
        "- High Axis-1 % → path-only gate is too blunt; classify on diff "
        "content (skip comment/rename-only changes).",
        "- Low Axis-2 % → bugs frequently live outside the flagged paths; "
        "review the gap list for a surface to add to `MATERIAL_PATHS`.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Git-backed driver
# ---------------------------------------------------------------------------
def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True, cwd=ROOT
    ).stdout


def _commit_files(sha: str) -> list[str]:
    out = _git("diff-tree", "--no-commit-id", "--name-only", "-r", sha)
    return [p for p in out.splitlines() if p]


def _material_diff(sha: str, paths: list[str]) -> str:
    # Whitespace-insensitive diff restricted to the material files.
    return _git("show", "-w", "--format=", sha, "--", *paths)


def collect_records(rng: str, limit: int | None) -> list[Record]:
    args = ["log", "--no-merges", "--format=%H%x1f%s"]
    if limit:
        args += [f"-n{limit}"]
    args += [rng]
    records: list[Record] = []
    for line in _git(*args).splitlines():
        if "\x1f" not in line:
            continue
        sha, subject = line.split("\x1f", 1)
        files = _commit_files(sha)
        material_files = [f for f in files if is_material(f)]
        material = bool(material_files)
        substantive = (
            is_substantive_diff(_material_diff(sha, material_files)) if material else False
        )
        records.append(Record(sha, subject, classify_commit_kind(subject), material, substantive))
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backtest the materiality gate.")
    parser.add_argument("--range", default="HEAD", help="git revision range")
    parser.add_argument("--limit", type=int, default=None, help="max commits")
    args = parser.parse_args(argv)

    records = collect_records(args.range, args.limit)
    print(build_report(summarize(records), args.range))
    return 0


if __name__ == "__main__":
    sys.exit(main())
