#!/usr/bin/env python3
"""Fail when a pytest run executed fewer tests than the floor.

A green build that ran nothing is not green: a collection error, a bad marker
expression, or an accidental `-k` filter can skip most of the suite while the
job still exits 0. This gate parses the junit XML pytest wrote and requires a
minimum number of EXECUTED tests (collected minus skipped).

Usage:
    python scripts/ci/check_test_count.py <junit-xml> <min-executed>

Floors are deliberately well below the real counts so routine test deletions
never trip them — they exist to catch collapse, not drift.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET


def main() -> int:
    report_path, floor = sys.argv[1], int(sys.argv[2])
    root = ET.parse(report_path).getroot()
    # pytest writes <testsuites><testsuite .../></testsuites>; tolerate a bare
    # <testsuite> root for robustness across junit_family settings.
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    collected = sum(int(suite.get("tests", "0")) for suite in suites)
    skipped = sum(int(suite.get("skipped", "0")) for suite in suites)
    executed = collected - skipped
    print(f"executed={executed} (collected={collected}, skipped={skipped}), floor={floor}")
    if executed < floor:
        print(
            f"FAIL: only {executed} tests executed but the floor is {floor} — "
            "a green build that ran nothing is not green. Check for collection "
            "errors, marker/-k filters, or a broken conftest.",
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
