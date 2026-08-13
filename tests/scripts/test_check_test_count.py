"""Unit tests for the CI test-count guard (chore/ci-hardening).

The guard's one job: a green build that ran nothing is not green. It parses
pytest's junit XML and fails when EXECUTED tests (collected − skipped −
errored) fall below the floor. Errored tests never ran their body, so they
must not count — that matters the day someone masks pytest's own exit code.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.ci import check_test_count

_WRAPPED = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="{errors}" failures="0" skipped="{skipped}" tests="{tests}" />
</testsuites>
"""

_BARE = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<testsuite name="pytest" tests="7" skipped="2" errors="0" />'
)


def _run(tmp_path: Path, xml: str, floor: int) -> int:
    report = tmp_path / "junit.xml"
    report.write_text(xml, encoding="utf-8")
    with patch.object(sys, "argv", ["check_test_count.py", str(report), str(floor)]):
        return check_test_count.main()


def test_passes_at_or_above_floor(tmp_path: Path) -> None:
    xml = _WRAPPED.format(tests=30, skipped=1, errors=0)
    assert _run(tmp_path, xml, 29) == 0


def test_fails_below_floor(tmp_path: Path) -> None:
    xml = _WRAPPED.format(tests=30, skipped=25, errors=0)
    assert _run(tmp_path, xml, 10) == 1


def test_errored_tests_do_not_count_as_executed(tmp_path: Path) -> None:
    """tests=30 with 25 fixture errors means only 5 ran — below a floor of 10."""
    xml = _WRAPPED.format(tests=30, skipped=0, errors=25)
    assert _run(tmp_path, xml, 10) == 1


def test_bare_testsuite_root_is_parsed(tmp_path: Path) -> None:
    """iter() is self-inclusive: a bare <testsuite> root works with no special case."""
    assert _run(tmp_path, _BARE, 5) == 0
    assert _run(tmp_path, _BARE, 6) == 1


def test_zero_tests_always_fails(tmp_path: Path) -> None:
    xml = _WRAPPED.format(tests=0, skipped=0, errors=0)
    assert _run(tmp_path, xml, 1) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
