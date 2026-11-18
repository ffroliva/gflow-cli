"""An e2e that reads log events from a child's stderr opts in to INFO (#1332).

The CLI's default log level is WARNING (#1041), so a ``gflow`` subprocess writes no
INFO event to stderr. A test that asserts on one — ``migrated.character_entities_attached``,
``migrated.image_submit_guarded`` — then sees nothing and reports the product broken
while it works: #1332 read "no character was attached" for a character that was attached.

This is a text scan, so it catches the common form (a module that splits a child's
``stderr`` into lines and reads their ``"event"``) and not every way of parsing stderr.

Lives in ``tests/`` because everything under ``tests/e2e/`` is deselected by default.
"""

from __future__ import annotations

import re
from pathlib import Path

_E2E = Path(__file__).parent / "e2e"
_SPLITS_STDERR = re.compile(r"stderr\.splitlines\(\)")
_READS_EVENT = re.compile(r"\.get\(\"event\"|\[\"event\"\]")
# An assignment of INFO (a dict entry, a subscript or a setenv) on a line that is not a
# comment, so neither a mention nor a commented-out assignment counts.
_SETS_INFO = re.compile(
    r"^(?![ \t]*#).*\"GFLOW_CLI_LOG_LEVEL\"(?:\]\s*=|\s*[:,])\s*\"INFO\"", re.MULTILINE
)
# A module whose child runs through another module's runner; that runner must set INFO.
_VIA_RUNNER = {"test_earlier_run_ref_in_place_bdd.py": "test_image_uuid_ref_e2e.py"}


def _reads_child_events(text: str) -> bool:
    """A child's stderr split into log lines, and some record read for its "event"."""
    return bool(_SPLITS_STDERR.search(text) and _READS_EVENT.search(text))


def _sets_info(name: str) -> bool:
    """INFO is assigned in the module, or in the runner it borrows."""
    module = _E2E / _VIA_RUNNER.get(name, name)
    return bool(_SETS_INFO.search(module.read_text(encoding="utf-8")))


def _modules_reading_child_events_without_info() -> list[str]:
    return sorted(
        path.name
        for path in _E2E.glob("test_*.py")
        if _reads_child_events(path.read_text(encoding="utf-8")) and not _sets_info(path.name)
    )


def test_every_e2e_reading_child_log_events_sets_the_log_level() -> None:
    assert _modules_reading_child_events_without_info() == []


def test_the_detector_sees_a_module_that_reads_child_events() -> None:
    sample = 'events = [json.loads(l) for l in proc.stderr.splitlines()]\nevents[0].get("event")\n'
    assert _reads_child_events(sample)
    # In-process structlog capture sees every level; it needs no opt-in.
    assert not _reads_child_events('f"{proc.stderr}"\ncapture.entries[0].get("event")\n')


def test_only_an_info_assignment_counts() -> None:
    for sets in (
        '{**env, "GFLOW_CLI_LOG_LEVEL": "INFO"}',
        'env["GFLOW_CLI_LOG_LEVEL"] = "INFO"',
        'monkeypatch.setenv("GFLOW_CLI_LOG_LEVEL", "INFO")',
    ):
        assert _SETS_INFO.search(sets), sets
    for not_set in (
        "# GFLOW_CLI_LOG_LEVEL is INFO here",
        'env["GFLOW_CLI_LOG_LEVEL"] = "WARNING"',
        '    # env["GFLOW_CLI_LOG_LEVEL"] = "INFO"',
    ):
        assert not _SETS_INFO.search(not_set), not_set
