"""Issue #528 proposal 4 — the incident bundle must show what was SUBMITTED.

All five bundles for the failing scene carried `network.json` with
`records: []`, so the only proof of the reference shape that triggered the
policy 400 lived in the stderr stream. These tests pin the counts-only echo
that now rides in the bundle — and pin that it stays counts-only, because
`network.json` is inside the §5.3 retention boundary (S02/S29: never key names,
field values, or prompt text).
"""

from __future__ import annotations

import json

from gflow_cli.api.transports.ui_automation import _reference_field_count
from gflow_cli.diagnostics import GenerationRequestRecord, IncidentJournal

BODY = json.dumps(
    {
        "requests": [
            {
                "prompt": "a portrait of his adult granddaughter on the porch",
                "referenceEntities": [{"entityId": "ent-1"}, {"entityId": "ent-2"}],
                "referenceImages": [{"bytes": "AAAA"}],
                "aspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE",
            }
        ]
    }
)


def test_reference_field_count_counts_without_naming() -> None:
    """referenceEntities + referenceImages = 2 reference-ish fields."""
    assert _reference_field_count(BODY) == 2


def test_reference_field_count_is_zero_for_unusable_input() -> None:
    for bad in (None, "", "not json", "[]", json.dumps({"requests": []})):
        assert _reference_field_count(bad) == 0


def test_journal_ring_collects_generation_requests() -> None:
    journal = IncidentJournal()
    journal.add_generation_request(
        GenerationRequestRecord(
            ts_utc="2026-08-25T00:00:00+00:00",
            route="flowMedia:batchGenerateImages",
            body_bytes=7500,
            reference_entity_count=2,
            reference_field_count=2,
            mentions_reference_entities=True,
        )
    )

    snap = journal.snapshot()

    assert len(snap.generation_requests) == 1
    assert snap.generation_requests[0].reference_entity_count == 2


def test_freeze_stops_late_generation_records() -> None:
    """Same S17 discipline as the other rings — no mutation mid-finalization."""
    journal = IncidentJournal()
    journal.freeze()
    journal.add_generation_request(
        GenerationRequestRecord(
            ts_utc="2026-08-25T00:00:00+00:00",
            route="r",
            body_bytes=1,
            reference_entity_count=0,
            reference_field_count=0,
            mentions_reference_entities=False,
        )
    )

    assert journal.snapshot().generation_requests == ()


def test_record_carries_no_free_text() -> None:
    """The retention boundary: counts, booleans, a sanitized route, a timestamp.

    If someone adds a prompt, a key name, or an entity id to this record, this
    test is the thing that should stop them.
    """
    allowed = {
        "ts_utc",
        "route",
        "body_bytes",
        "reference_entity_count",
        "reference_field_count",
        "mentions_reference_entities",
    }

    assert set(GenerationRequestRecord.__slots__) == allowed
