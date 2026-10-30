"""Compare runs by median per-file time, and find files that pay setup on their first test."""

import xml.etree.ElementTree as ET
from collections.abc import Set
from pathlib import Path
from statistics import median

from .junit import Run, file_of


def _medians(runs: list[dict[str, float]], files: set[str]) -> dict[str, float]:
    # A file absent from a run counts as 0.0 there.
    return {f: median(run.get(f, 0.0) for run in runs) for f in files} if runs else {}


def median_compare(
    base_runs: list[dict[str, float]],
    new_runs: list[dict[str, float]],
    exclude: Set[str] = frozenset(),
) -> list[tuple[str, float, float, float]]:
    files = {f for run in (*base_runs, *new_runs) for f in run} - exclude
    base, new = _medians(base_runs, files), _medians(new_runs, files)
    rows = [(f, base.get(f, 0.0), new.get(f, 0.0)) for f in files]
    return sorted(((f, b, n, n - b) for f, b, n in rows), key=lambda row: (row[3], row[0]))


def noise(base_runs: list[dict[str, float]], exclude: Set[str]) -> float:
    """Median over files of the spread (max - min) across the base runs."""
    if len(base_runs) < 2:
        return 0.0
    files = {f for run in base_runs for f in run} - exclude
    if not files:
        return 0.0
    spreads = []
    for f in files:
        times = [run.get(f, 0.0) for run in base_runs]
        spreads.append(max(times) - min(times))
    return median(spreads)


def first_test_cost(folder: Path, root: Path) -> list[tuple[str, float, float]]:
    cases: dict[str, list[float]] = {}
    for report in sorted(folder.rglob("*.xml")):
        try:
            tree = ET.parse(report)
        except ET.ParseError:
            continue
        for case in tree.iter("testcase"):
            path = file_of(case.get("classname", ""), root)
            if path is not None:
                cases.setdefault(path, []).append(float(case.get("time") or 0))
    rows = [(f, times[0], median(times[1:])) for f, times in cases.items() if len(times) >= 2]
    return sorted(rows, key=lambda row: (row[2] - row[1], row[0]))


def _table(header: tuple[str, ...], rows: list[tuple]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for name, *seconds in rows:
        lines.append("| " + " | ".join([name, *(f"{s:.1f}" for s in seconds)]) + " |")
    return "\n".join(lines)


def render_compare(rows: list[tuple[str, float, float, float]]) -> str:
    return _table(("file", "base", "new", "delta"), rows)


def rank(run: Run) -> list[tuple[str, str, float, float]]:
    """(file, group, seconds, share of the group's seconds), largest share first."""
    rows = []
    for group, files in run.groups.items():
        total = sum(files.values())
        rows += [(f, group, s, s / total if total else 0.0) for f, s in files.items()]
    return sorted(rows, key=lambda row: (-row[3], row[0]))


def render_rank(rows: list[tuple[str, str, float, float]]) -> str:
    lines = ["| file | group | seconds | share |", "|---|---|---|---|"]
    lines += [f"| {f} | {g} | {s:.1f} | {share:.2f} |" for f, g, s, share in rows]
    return "\n".join(lines)


def render_first_cost(rows: list[tuple[str, float, float]]) -> str:
    return _table(("file", "first", "median rest"), rows)
