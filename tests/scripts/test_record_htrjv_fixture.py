"""The HTrJv fixture recorder keeps family fields and usage keys only (#1375, #1550)."""

from __future__ import annotations

from scripts.dev.record_htrjv_fixture import trim


def test_trim_keeps_family_fields_and_usage_keys_only() -> None:
    usage = ["veo_3_1_t2v_fast", 20, [8], "a cost and limits gflow does not record"]
    family = ["Veo 3.1 - Fast", [usage], True, "veo_3_1_fast", None, None, [2026, 12, 5], "x"]

    assert trim([[family], "other reply data"]) == [
        ["Veo 3.1 - Fast", [["veo_3_1_t2v_fast"]], True, "veo_3_1_fast", None, None, [2026, 12, 5]]
    ]


def test_trim_of_a_reply_with_no_families_is_empty() -> None:
    assert trim([[1, 2], None]) == []
