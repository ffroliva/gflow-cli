#!/usr/bin/env python3
"""Advisory materiality + traceability classifier (governance signal).

Mirrors the *behaviour* of the reference AI-DLC governance orchestrator's risk
gate — classify every change, recommend human review on material paths — but
**advisory only**: this script ALWAYS exits 0. It never blocks a merge. Hard
enforcement (a ``--block-on`` flag + branch protection) is a conscious deferral,
matching the reference's opt-in design.

Surfaces:
  - stdout (local run / CI log)
  - ``$GITHUB_STEP_SUMMARY`` when set (fork-safe; needs no token)

Run manually:
    uv run python scripts/ci/check_materiality.py [--base origin/develop]

The material-path list here is the SINGLE SOURCE OF TRUTH. ``skills/pr-council-
review/SKILL.md`` §1 documents the same paths in prose; ``_check_material_list_
sync`` asserts they stay in agreement (``check_doc_links.py`` only validates
Markdown links, not constant↔prose parity).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Canonical material-path tokens. A touched path is "material" if it contains
# any of these substrings. Kept in lockstep with skills/pr-council-review/
# SKILL.md §1 by _check_material_list_sync().
MATERIAL_PATHS: list[str] = [
    "src/gflow_cli/api/transports/",
    "src/gflow_cli/api/client.py",  # Bearer/access-token/SAPISID client (backtest: 3 fixes)
    "src/gflow_cli/api/_sapisidhash.py",  # SAPISID hash primitive — auth credential
    "src/gflow_cli/auth/",
    "src/gflow_cli/data/",
    "recaptcha",
]

SKILL_REF = "skills/pr-council-review/SKILL.md"
DEFAULT_BASE = os.environ.get("GFLOW_MATERIALITY_BASE", "origin/develop")


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------
def is_material(path: str) -> bool:
    return any(token in path for token in MATERIAL_PATHS)


def classify(paths: list[str]) -> tuple[list[str], list[str]]:
    """Split touched paths into (material, routine)."""
    material = [p for p in paths if is_material(p)]
    routine = [p for p in paths if not is_material(p)]
    return material, routine


def traceability_signals(paths: list[str], commit_messages: str = "") -> dict[str, bool]:
    """Report-only signals. Never gate on these."""
    src_touched = any(p.startswith("src/") for p in paths)
    tests_touched = any(p.startswith("tests/") for p in paths)
    plan_referenced = any("docs/superpowers/plans/" in p for p in paths) or (
        "superpowers/plans" in commit_messages
    )
    return {
        "src_touched": src_touched,
        "tests_touched": tests_touched,
        "plan_referenced": plan_referenced,
    }


def build_report(material: list[str], routine: list[str], signals: dict[str, bool]) -> str:
    """Build the advisory Markdown report. Tool-agnostic remediation."""
    lines: list[str] = ["## 🧭 Governance advisory (non-blocking)", ""]

    if material:
        lines += [
            f"**Material paths touched ({len(material)})** — human review recommended:",
            "",
            *[f"- `{p}`" for p in material],
            "",
            "### Recommended gates",
            "- Run **`/gflow:predict`** (Claude Code), **or**",
            "- Read **`skills/predict/SKILL.md`** and produce the 5-persona "
            "verdict yourself (Cursor / Codex / Gemini / Aider), **or**",
            "- A human reviewer documents the risk assessment in the PR.",
            "",
            "Then a council pass: `/gflow:pr-council-review` or read "
            "`skills/pr-council-review/SKILL.md`.",
            "",
        ]
    else:
        lines += ["No material paths touched — routine change.", ""]

    # Report-only traceability checklist.
    plan = "x" if signals["plan_referenced"] else " "
    tests = "x" if signals["tests_touched"] else " "
    lines += [
        "### Traceability (report-only — never blocks)",
        f"- [{plan}] references a plan in `docs/superpowers/plans/`",
        f"- [{tests}] touches `tests/` alongside `src/`"
        + (
            "  — _src changed without test changes; verify this is a refactor/docstring/deletion_"
            if signals["src_touched"] and not signals["tests_touched"]
            else ""
        ),
        "",
        f"_Routine paths: {len(routine)}. This report is advisory; the build is not failed by it._",
    ]
    return "\n".join(lines)


def _check_material_list_sync(skill_text: str) -> list[str]:
    """Assert every MATERIAL_PATHS token appears in the SKILL.md prose.

    Returns warning strings (empty == in sync). Advisory by design, but wired
    into the hygiene gate so drift is caught.
    """
    missing = [tok for tok in MATERIAL_PATHS if tok.rstrip("/") not in skill_text]
    if not missing:
        return []
    return [
        f"Material path list drifted — {missing!r} not found in {SKILL_REF} §1. "
        "Update the SKILL prose to match scripts/ci/check_materiality.py:MATERIAL_PATHS."
    ]


# ---------------------------------------------------------------------------
# Git-backed helpers (not unit-tested; exercised via integration / live CI)
# ---------------------------------------------------------------------------
def touched_paths(base: str) -> list[str]:
    """Files changed on this branch vs base (two-dot — NOT symmetric ...)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base}..HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=ROOT,
        )
    except (subprocess.CalledProcessError, OSError):
        return []
    return [p for p in result.stdout.splitlines() if p]


def _commit_messages(base: str) -> str:
    try:
        result = subprocess.run(
            ["git", "log", "--format=%B", f"{base}..HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=ROOT,
        )
    except (subprocess.CalledProcessError, OSError):
        return ""
    return result.stdout


def _emit(report: str) -> None:
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(report + "\n")
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=DEFAULT_BASE, help="base ref to diff against")
    args = parser.parse_args(argv)

    paths = touched_paths(args.base)
    material, routine = classify(paths)
    signals = traceability_signals(paths, _commit_messages(args.base))
    _emit(build_report(material, routine, signals))

    # ALWAYS advisory — never fail the build.
    return 0


if __name__ == "__main__":
    sys.exit(main())
