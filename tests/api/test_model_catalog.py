"""Model retirement and Flow's model catalog (#1375, slice 1).

Flow lists three video model families as leaving on 2026-12-05 (the menu caption "Leaving 5
Dec", and index 6 of the family entry in the ``HTrJv`` reply). gflow carries those dates
statically; these tests pin them and compare them, offline, with a recorded ``HTrJv``
catalog (``tests/fixtures/flow_htrjv_model_families.json``). Only family keys and retirement
dates are asserted: the usage slots (cost, duration, limits) rest on one capture.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.api.video import VideoModel

FIXTURE = Path(__file__).parent.parent / "fixtures" / "flow_htrjv_model_families.json"
LEAVING = {VideoModel.OMNI_FLASH, VideoModel.VEO_3_1_FAST, VideoModel.VEO_3_1_QUALITY}


def _recorded() -> list[Any]:
    families: list[Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))["families"]
    return families


def test_leaving_models_retire_on_2026_12_05() -> None:
    from gflow_cli.api.model_catalog import retires_on

    for model in VideoModel:
        expected = date(2026, 12, 5) if model in LEAVING else None
        assert retires_on(model) == expected, model


def test_every_video_model_maps_to_a_family_in_the_recorded_catalog() -> None:
    from gflow_cli.api.model_catalog import FAMILY_KEY, parse_htrjv_families

    recorded = {family.key for family in parse_htrjv_families(_recorded())}
    for model in VideoModel:
        assert FAMILY_KEY[model] in recorded, f"{model} -> {FAMILY_KEY.get(model)} not in HTrJv"


def test_static_dates_match_the_recorded_catalog() -> None:
    """A refreshed fixture whose date moved fails here until retires_on is updated."""
    from gflow_cli.api.model_catalog import FAMILY_KEY, parse_htrjv_families, retires_on

    by_key = {family.key: family for family in parse_htrjv_families(_recorded())}
    for model in VideoModel:
        assert by_key[FAMILY_KEY[model]].retires_on == retires_on(model), model


def test_the_recorded_catalog_parses_into_families() -> None:
    from gflow_cli.api.model_catalog import parse_htrjv_families

    families = {family.key: family for family in parse_htrjv_families(_recorded())}
    abra = families["abra"]
    assert (abra.display_name, abra.enabled, abra.retires_on) == (
        "Omni 1.1 Flash",
        True,
        date(2026, 12, 5),
    )
    assert families["macaron_cookie"].retires_on is None


@pytest.mark.parametrize(
    "slot6",
    ["2026-12-05", [2026, 12], [2026, 13, 40], ["2026", 12, 5], {"y": 2026}, None],
    ids=["string", "two-ints", "impossible-date", "string-year", "dict", "null"],
)
def test_parser_tolerates_a_changed_date_slot(slot6: object) -> None:
    """A shape change at index 6 gives no date; it never raises (scenario 8)."""
    from gflow_cli.api.model_catalog import parse_htrjv_families

    entry = ["Veo 3.1 - Fast", [["veo_3_1_t2v_fast"]], True, "veo_3_1_fast", None, None, slot6]
    (family,) = parse_htrjv_families([entry])
    assert (family.key, family.retires_on) == ("veo_3_1_fast", None)


def test_parser_ignores_payloads_with_no_families() -> None:
    from gflow_cli.api.model_catalog import parse_htrjv_families

    assert parse_htrjv_families(None) == []
    assert parse_htrjv_families({"not": "a list"}) == []
    assert parse_htrjv_families([[1, 2, 3], ["only a name"]]) == []
