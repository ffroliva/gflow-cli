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
    from gflow_cli.api.model_catalog import classify_catalog, parse_htrjv_families

    assert classify_catalog(parse_htrjv_families(_recorded()), today=_BEFORE) == []


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


_BEFORE = date(2026, 11, 27)  # before the 2026-12-05 retirement
_ON = date(2026, 12, 5)


def _family(key: str, *, usages: tuple[str, ...] = (), when: date | None = None) -> Any:
    from gflow_cli.api.model_catalog import ModelFamily

    return ModelFamily(key=key, display_name=key, enabled=True, retires_on=when, usages=usages)


def _baseline() -> list[Any]:
    from gflow_cli.api.model_catalog import parse_htrjv_families

    return parse_htrjv_families(_recorded())


def _classify(families: list[Any], today: date = _BEFORE) -> list[tuple[str, str]]:
    from gflow_cli.api.model_catalog import classify_catalog

    return [(d.kind.value, d.line) for d in classify_catalog(families, today=today)]


def test_a_family_gflow_neither_maps_nor_ignores_is_added() -> None:
    assert _classify([*_baseline(), _family("veo_4")]) == [
        ("added", "added family veo_4 (veo_4): not mapped in gflow")
    ]


def test_a_mapped_family_dropped_before_any_retirement_is_a_break() -> None:
    families = [f for f in _baseline() if f.key != "veo_3_1_lite"]
    assert _classify(families) == [
        ("break", "removed family veo_3_1_lite: gflow maps veo_3_1_lite")
    ]


def test_the_retiring_families_dropped_on_their_date_are_retired_as_scheduled() -> None:
    """2026-12-05: Flow drops abra, veo_3_1_fast and veo_3_1_quality. Expected, not a break."""
    gone = {"abra", "veo_3_1_fast", "veo_3_1_quality"}
    families = [f for f in _baseline() if f.key not in gone]

    kinds = {kind for kind, _ in _classify(families, today=_ON)}
    assert kinds == {"retired"}
    assert len(_classify(families, today=_ON)) == 3
    assert {kind for kind, _ in _classify(families, today=date(2027, 1, 1))} == {"retired"}


def test_a_retiring_family_dropped_before_its_date_is_a_break() -> None:
    families = [f for f in _baseline() if f.key != "abra"]
    assert _classify(families, today=date(2026, 12, 4)) == [
        ("break", "removed family abra: gflow maps omni_flash")
    ]


def test_an_ignored_family_dropped_or_renamed_is_an_ignored_change() -> None:
    families = [
        _family("gemini_tts_2") if f.key == "gemini_tts" else f
        for f in _baseline()
        if f.key != "upsample_4k"
    ]
    assert _classify(families) == [
        ("added", "added family gemini_tts_2 (gemini_tts_2): not mapped in gflow"),
        ("ignored", "removed family upsample_4k: listed as ignored in gflow"),
        ("ignored", "removed family gemini_tts: listed as ignored in gflow"),
    ]


def test_an_image_wire_key_flow_dropped_is_a_break() -> None:
    families = [
        _family("harbor_seal", usages=("HARBOR_SEAL_2",)) if f.key == "harbor_seal" else f
        for f in _baseline()
    ]
    assert _classify(families) == [
        ("added", "family harbor_seal lists HARBOR_SEAL_2: gflow has no mapping for it"),
        ("break", "family harbor_seal no longer lists HARBOR_SEAL: gflow sends it for HARBOR_SEAL"),
    ]


def test_an_unmapped_key_inside_an_image_family_is_added() -> None:
    families = [
        _family("harbor_seal", usages=("HARBOR_SEAL", "HARBOR_SEAL_PRO"))
        if f.key == "harbor_seal"
        else f
        for f in _baseline()
    ]
    assert _classify(families) == [
        ("added", "family harbor_seal lists HARBOR_SEAL_PRO: gflow has no mapping for it")
    ]


def test_a_mapped_video_familys_new_moved_or_dropped_date_is_a_break() -> None:
    moved = {
        "veo_3_1_lite": date(2027, 1, 31),
        "veo_3_1_fast": date(2026, 12, 12),
        "veo_3_1_quality": None,
    }
    families = [
        _family(f.key, usages=f.usages, when=moved[f.key]) if f.key in moved else f
        for f in _baseline()
    ]
    assert _classify(families) == [
        ("break", "veo_3_1_lite: new retirement date 2027-01-31"),
        ("break", "veo_3_1_fast: retirement date moved 2026-12-05 -> 2026-12-12"),
        ("break", "veo_3_1_quality: retirement date 2026-12-05 withdrawn"),
    ]


@pytest.mark.parametrize(
    ("key", "kind"),
    [("veo_4", "added"), ("nano_banana_pro", "added"), ("gemini_tts", "ignored")],
)
def test_a_date_on_a_new_image_or_ignored_family_is_not_a_break(key: str, kind: str) -> None:
    """RETIRES_ON covers gflow's video models only; a date elsewhere is news, never exit 33."""
    when = date(2027, 3, 1)
    others = [f for f in _baseline() if f.key != key]
    usages = next((f.usages for f in _baseline() if f.key == key), ())
    diffs = _classify([*others, _family(key, usages=usages, when=when)])
    assert (kind, f"{key}: new retirement date {when}") in diffs
    assert all(k != "break" for k, _ in diffs)


def test_an_empty_catalog_is_a_break() -> None:
    [(kind, line)] = _classify([])
    assert kind == "break"
    assert "no model families" in line


def test_the_parser_keeps_each_familys_usage_keys() -> None:
    by_key = {f.key: f for f in _baseline()}
    assert by_key["nano_banana_pro"].usages == ("GEM_PIX_2",)
    assert "abra_t2v_4s" in by_key["abra"].usages
