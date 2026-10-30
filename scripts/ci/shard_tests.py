#!/usr/bin/env python3
"""Split the test suite into balanced CI shards, by whole file.

ci.yml runs the 3.13 suite as parallel shards; root ``conftest.py`` reads
``GFLOW_TEST_SHARD=k/N`` and keeps only the files :func:`shard_owner` gives shard
``k``. Splitting by file keeps module fixtures in one process. The split is
balanced on per-file seconds recorded in ``tests/test_durations.json``; a file
with no record (a new one) counts as the average, so it never lands at random.

Refresh the record from CI's shard reports, never a local run: Windows inflates
some files far past their Linux time (test_cleanup.py: 242 s locally, 3 s on CI),
which unbalances the Linux shards. From a recent develop push run (artifacts are
kept one day):

    gh run download <run-id> -p 'shard-*' -D tmp/shards
    uv run python scripts/ci/shard_tests.py tmp/shards/shard-*/pytest-report.xml

A stale record only unbalances the shards — every file still runs exactly once.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DURATIONS_FILE = ROOT / "tests" / "test_durations.json"


def parse_spec(spec: str) -> tuple[int, int]:
    """``"k/N"`` (1-based, as written in ci.yml) -> ``(k - 1, N)``."""
    try:
        k, n = (int(part) for part in spec.split("/"))
    except ValueError:
        raise ValueError(f"GFLOW_TEST_SHARD must look like 2/3, got {spec!r}") from None
    if not 1 <= k <= n:
        raise ValueError(f"GFLOW_TEST_SHARD {spec!r}: need 1 <= k <= N")
    return k - 1, n


def load_durations(path: Path = DURATIONS_FILE) -> dict[str, float]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def file_weights(files: list[str], durations: dict[str, float]) -> dict[str, float]:
    known = [durations[f] for f in files if f in durations]
    average = sum(known) / len(known) if known else 1.0
    return {f: durations.get(f, average) for f in files}


def shard_owner(files: list[str], total: int, durations: dict[str, float]) -> dict[str, int]:
    """Greedy longest-first: each file goes to the currently lightest shard."""
    weights = file_weights(sorted(set(files)), durations)
    loads = [0.0] * total
    owner: dict[str, int] = {}
    for f in sorted(weights, key=lambda name: (-weights[name], name)):
        k = min(range(total), key=lambda i: (loads[i], i))
        owner[f] = k
        loads[k] += weights[f]
    return owner


def _file_of(classname: str, root: Path) -> str | None:
    """``tests.pkg.test_x.TestThing`` -> ``tests/pkg/test_x.py`` if that file exists."""
    parts = classname.split(".")
    while parts:
        candidate = "/".join(parts) + ".py"
        if (root / candidate).is_file():
            return candidate
        parts.pop()
    return None


def durations_from_junit(reports: list[Path], root: Path = ROOT) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for report in reports:
        for case in ET.parse(report).getroot().iter("testcase"):
            f = _file_of(case.get("classname", ""), root)
            if f is not None:
                totals[f] += float(case.get("time", "0"))
    return {f: round(t, 2) for f, t in sorted(totals.items())}


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: shard_tests.py <junit-xml>...")
        return 2
    durations = durations_from_junit([Path(p) for p in sys.argv[1:]])
    DURATIONS_FILE.write_text(json.dumps(durations, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(durations)} files, {sum(durations.values()):.0f} s -> {DURATIONS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
