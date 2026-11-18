"""An e2e that reads log events from a child's stderr opts in to INFO (#1332).

The CLI's default log level is WARNING (#1041), so a ``gflow`` subprocess writes no
INFO event to stderr. A test that asserts on one — ``migrated.character_entities_attached``,
``migrated.image_submit_guarded`` — then sees nothing and reports the product broken
while it works: #1332 read "no character was attached" for a character that was attached.
Every such module sets ``GFLOW_CLI_LOG_LEVEL`` for its child.

Lives in ``tests/`` because everything under ``tests/e2e/`` is deselected by default.
"""

from __future__ import annotations

import re
from pathlib import Path

_E2E = Path(__file__).parent / "e2e"
_SPLITS_STDERR = re.compile(r"stderr\.splitlines\(\)")
_READS_EVENT = re.compile(r"\.get\(\"event\"|\[\"event\"\]")


def _reads_child_events(text: str) -> bool:
    """A child's stderr split into log lines, and some record read for its "event"."""
    return bool(_SPLITS_STDERR.search(text) and _READS_EVENT.search(text))


def _modules_reading_child_events_without_info() -> list[str]:
    return sorted(
        path.name
        for path in _E2E.glob("test_*.py")
        if _reads_child_events(text := path.read_text(encoding="utf-8"))
        and "GFLOW_CLI_LOG_LEVEL" not in text
    )


def test_every_e2e_reading_child_log_events_sets_the_log_level() -> None:
    assert _modules_reading_child_events_without_info() == []


def test_the_detector_sees_a_module_that_reads_child_events() -> None:
    sample = 'events = [json.loads(l) for l in proc.stderr.splitlines()]\nevents[0].get("event")\n'
    assert _reads_child_events(sample)
    # In-process structlog capture sees every level; it needs no opt-in.
    assert not _reads_child_events('f"{proc.stderr}"\ncapture.entries[0].get("event")\n')
