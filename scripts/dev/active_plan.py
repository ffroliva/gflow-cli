#!/usr/bin/env python3
"""
Print a compact summary of the currently active plan.

Priority chain:
  1. --feature <name>  match against docs/superpowers/plans/
  2. most-recent file  under docs/superpowers/plans/ (by mtime, skipping orchestration files)
  3. PLAN.md           root roadmap — first phase without DONE/COMPLETE/BACKLOG marker

Output is intentionally compact: only the relevant chunk enters the agent's context.
Run directly or via the /gflow:plan slash command.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUPERPOWERS_DIR = ROOT / "docs" / "superpowers" / "plans"
ROOT_PLAN = ROOT / "PLAN.md"

_DONE_MARKERS = re.compile(r"✅|DONE|COMPLETE|BACKLOG", re.IGNORECASE)
_UNCHECKED = re.compile(r"^\s*-\s*\[\s*\]")
_CHECKED = re.compile(r"^\s*-\s*\[[xX]\]")
_TASK_HEADING = re.compile(r"^###\s+Task\s+\d+")
_PHASE_HEADING = re.compile(r"^##\s+Phase\s+\d+")
_SECTION_H2 = re.compile(r"^##\s+")
_SECTION_H3 = re.compile(r"^###\s+")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _superpowers_candidates() -> list[Path]:
    """All non-orchestration plan files, newest first."""
    if not SUPERPOWERS_DIR.exists():
        return []
    candidates: list[Path] = []
    for p in SUPERPOWERS_DIR.iterdir():
        if p.is_file() and p.suffix == ".md" and "orchestration" not in p.name:
            candidates.append(p)
        elif p.is_dir():
            sub = p / "PLAN.md"
            if sub.exists():
                candidates.append(sub)
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)


def _find_plan(feature: str | None) -> Path | None:
    candidates = _superpowers_candidates()
    if not candidates:
        return None
    if feature:
        slug = feature.lower().replace(" ", "-").replace("_", "-")
        matches = [p for p in candidates if slug in str(p).lower()]
        if matches:
            return matches[0]  # already sorted newest-first
    return candidates[0]


# ---------------------------------------------------------------------------
# Superpowers plan extraction
# ---------------------------------------------------------------------------

def _summarise_superpowers(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()

    title = next((l.lstrip("# ").strip() for l in lines if l.startswith("# ")), path.stem)

    goal = ""
    for line in lines:
        if line.startswith("**Goal:**"):
            goal = line.replace("**Goal:**", "").strip()
            break

    checked = sum(1 for l in lines if _CHECKED.match(l))
    unchecked = sum(1 for l in lines if _UNCHECKED.match(l))
    total = checked + unchecked

    # Find the task section containing the first unchecked step.
    first_unchecked_idx: int | None = None
    for i, line in enumerate(lines):
        if _UNCHECKED.match(line):
            first_unchecked_idx = i
            break

    header = [
        f"Plan : {path.relative_to(ROOT)}",
        f"Title: {title}",
    ]
    if goal:
        header.append(f"Goal : {goal}")
    header.append(f"Progress: {checked}/{total} steps complete, {unchecked} remaining")

    if first_unchecked_idx is None:
        return "\n".join(header) + "\n\nAll steps complete."

    # Walk back to find the enclosing ### Task heading.
    task_start = first_unchecked_idx
    for j in range(first_unchecked_idx - 1, -1, -1):
        if _TASK_HEADING.match(lines[j]) or _PHASE_HEADING.match(lines[j]):
            task_start = j
            break

    # Walk forward to the next ### or ## heading to close the block.
    task_end = len(lines)
    for k in range(task_start + 1, len(lines)):
        if (_TASK_HEADING.match(lines[k]) or _PHASE_HEADING.match(lines[k])
                or _SECTION_H2.match(lines[k])):
            task_end = k
            break

    block = "\n".join(lines[task_start:task_end]).rstrip()
    return "\n".join(header) + f"\n\n--- Next task ---\n{block}"


# ---------------------------------------------------------------------------
# PLAN.md extraction
# ---------------------------------------------------------------------------

def _summarise_root_plan() -> str:
    if not ROOT_PLAN.exists():
        return "PLAN.md not found."

    lines = ROOT_PLAN.read_text(encoding="utf-8").splitlines()

    # Find the first ### Phase heading that has no DONE/COMPLETE/BACKLOG marker.
    phase_start = -1
    for i, line in enumerate(lines):
        if line.startswith("### Phase") and not _DONE_MARKERS.search(line):
            phase_start = i
            break

    if phase_start == -1:
        return "Plan: PLAN.md\n\nAll phases appear complete or deferred to backlog."

    # Close at the next ### or ## heading.
    phase_end = len(lines)
    for k in range(phase_start + 1, len(lines)):
        if lines[k].startswith("### ") or lines[k].startswith("## "):
            phase_end = k
            break

    block = "\n".join(lines[phase_start:phase_end]).rstrip()
    return f"Plan: PLAN.md\n\n{block}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print the active plan summary (compact — for agent context)."
    )
    parser.add_argument(
        "--feature", "-f",
        metavar="NAME",
        help="Feature name to match against docs/superpowers/plans/ (e.g. shell-multi-prompt)",
    )
    args = parser.parse_args()

    plan_path = _find_plan(args.feature)
    if plan_path:
        print(_summarise_superpowers(plan_path))
    else:
        print(_summarise_root_plan())


if __name__ == "__main__":
    main()
