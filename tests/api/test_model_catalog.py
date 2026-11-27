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


# --- #1550: the cross-check. A refreshed fixture that adds or drops a model fails here. ---


def test_the_recorded_catalog_matches_gflows_mapping() -> None:
    """Every family is mapped or ignored, every mapped family is present, dates agree."""
    from gflow_cli.api.model_catalog import catalog_drift, parse_htrjv_families

    assert catalog_drift(parse_htrjv_families(_recorded())) == []


def test_every_image_model_is_mapped_or_recorded_as_unlisted() -> None:
    from gflow_cli.api.image import Model
    from gflow_cli.api.model_catalog import IMAGE_FAMILY, UNLISTED_MODELS

    assert set(IMAGE_FAMILY).isdisjoint(UNLISTED_MODELS)
    assert set(IMAGE_FAMILY) | set(UNLISTED_MODELS) == set(Model)


def test_direct_transport_wire_names_are_the_catalog_keys() -> None:
    from gflow_cli.api.model_catalog import IMAGE_FAMILY
    from gflow_cli.api.transports.direct_http import _WIRE_MODEL

    for model, wire in _WIRE_MODEL.items():
        assert IMAGE_FAMILY[model][1] == wire, model


def _family(key: str, *, usages: tuple[str, ...] = (), when: date | None = None) -> Any:
    from gflow_cli.api.model_catalog import ModelFamily

    return ModelFamily(key=key, display_name=key, enabled=True, retires_on=when, usages=usages)


def _baseline() -> list[Any]:
    from gflow_cli.api.model_catalog import parse_htrjv_families

    return parse_htrjv_families(_recorded())


def test_drift_names_a_family_gflow_neither_maps_nor_ignores() -> None:
    from gflow_cli.api.model_catalog import catalog_drift

    assert catalog_drift([*_baseline(), _family("veo_4")]) == [
        "added family veo_4 (veo_4): not mapped in gflow"
    ]


def test_drift_names_a_mapped_family_flow_dropped() -> None:
    from gflow_cli.api.model_catalog import catalog_drift

    families = [f for f in _baseline() if f.key != "veo_3_1_lite"]
    assert catalog_drift(families) == ["removed family veo_3_1_lite: gflow maps veo_3_1_lite"]


def test_drift_names_an_ignored_family_flow_dropped() -> None:
    from gflow_cli.api.model_catalog import catalog_drift

    families = [f for f in _baseline() if f.key != "gemini_tts"]
    assert catalog_drift(families) == ["removed family gemini_tts: listed as ignored in gflow"]


def test_drift_names_an_image_wire_key_flow_dropped() -> None:
    from gflow_cli.api.model_catalog import catalog_drift

    families = [
        _family("harbor_seal", usages=("HARBOR_SEAL_2",)) if f.key == "harbor_seal" else f
        for f in _baseline()
    ]
    assert catalog_drift(families) == [
        "family harbor_seal lists HARBOR_SEAL_2: gflow has no mapping for it",
        "family harbor_seal no longer lists HARBOR_SEAL: gflow sends it for HARBOR_SEAL",
    ]


def test_drift_names_an_unmapped_key_inside_an_image_family() -> None:
    from gflow_cli.api.model_catalog import catalog_drift, catalog_news

    families = [
        _family("harbor_seal", usages=("HARBOR_SEAL", "HARBOR_SEAL_PRO"))
        if f.key == "harbor_seal"
        else f
        for f in _baseline()
    ]
    line = "family harbor_seal lists HARBOR_SEAL_PRO: gflow has no mapping for it"
    assert catalog_drift(families) == [line]
    assert catalog_news(families) == [line]


def test_news_is_only_what_flow_added() -> None:
    from gflow_cli.api.model_catalog import catalog_news

    assert catalog_news(_baseline()) == []
    assert catalog_news([*_baseline(), _family("veo_4")]) == [
        "added family veo_4 (veo_4): not mapped in gflow"
    ]
    assert catalog_news([f for f in _baseline() if f.key != "veo_3_1_lite"]) == []


def test_drift_names_new_moved_and_dropped_retirement_dates() -> None:
    from gflow_cli.api.model_catalog import catalog_drift

    moved = {
        "veo_3_1_lite": date(2027, 1, 31),
        "veo_3_1_fast": date(2026, 12, 12),
        "veo_3_1_quality": None,
    }
    families = [
        _family(f.key, usages=f.usages, when=moved[f.key]) if f.key in moved else f
        for f in _baseline()
    ]
    assert catalog_drift(families) == [
        "veo_3_1_lite: new retirement date 2027-01-31",
        "veo_3_1_fast: retirement date moved 2026-12-05 -> 2026-12-12",
        "veo_3_1_quality: retirement date 2026-12-05 withdrawn",
    ]


def test_the_parser_keeps_each_familys_usage_keys() -> None:
    by_key = {f.key: f for f in _baseline()}
    assert by_key["nano_banana_pro"].usages == ("GEM_PIX_2",)
    assert "abra_t2v_4s" in by_key["abra"].usages
