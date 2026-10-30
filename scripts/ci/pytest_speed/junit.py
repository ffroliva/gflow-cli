"""Per-test-file times from JUnit XML folders."""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


def file_of(classname: str, root: Path) -> str | None:
    """`tests.unit.test_x.TestY` -> `tests/unit/test_x.py`, checked against the tree."""
    parts = classname.split(".")
    for n in range(len(parts), 0, -1):
        candidate = "/".join(parts[:n]) + ".py"
        if (root / candidate).is_file():
            return candidate
    return None


@dataclass(frozen=True)
class Run:
    groups: dict[str, dict[str, float]]
    tests: dict[str, int]
    unmapped: int
    unreadable: int


def read_run(folder: Path, root: Path) -> Run:
    groups: dict[str, dict[str, float]] = {}
    tests: dict[str, int] = {}
    unmapped = unreadable = 0
    for report in sorted(folder.rglob("*.xml")):
        try:
            cases = list(ET.parse(report).iter("testcase"))
        except ET.ParseError:
            unreadable += 1
            continue
        files = groups.setdefault(report.relative_to(folder).with_suffix("").as_posix(), {})
        for case in cases:
            path = file_of(case.get("classname", ""), root)
            if path is None:
                unmapped += 1
                continue
            files[path] = files.get(path, 0.0) + float(case.get("time") or 0)
            tests[path] = tests.get(path, 0) + 1
    return Run(groups, tests, unmapped, unreadable)


def file_totals(run: Run) -> dict[str, float]:
    totals: dict[str, float] = {}
    for files in run.groups.values():
        for path, seconds in files.items():
            totals[path] = totals.get(path, 0.0) + seconds
    return totals
