"""Offline BDD for model retirement (#1375): no browser, no Flow account.

The warning's entry point is ``api.model_catalog.warn_if_retiring(model, today=...)``: it
logs ``model.retiring`` and returns the notice, and never raises. "Today" is injected, so
no scenario depends on the wall clock.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when
from structlog.testing import capture_logs

from gflow_cli.api.video import VideoModel
from gflow_cli.cli_models import build_catalog

scenarios("model_retirement.feature")

FIXTURE = Path(__file__).parent.parent / "fixtures" / "flow_htrjv_model_families.json"
_BY_LABEL = {
    "Veo 3.1 Fast": VideoModel.VEO_3_1_FAST,
    "Veo 3.1 Quality": VideoModel.VEO_3_1_QUALITY,
    "Veo 3.1 Lite": VideoModel.VEO_3_1_LITE,
    "Omni 1.1 Flash": VideoModel.OMNI_FLASH,
}
_LEAVING = {"veo_3_1_fast", "veo_3_1_quality", "omni_flash"}


@pytest.fixture
def world() -> dict[str, Any]:
    return {}


@when("the model catalog is built")
def _build(world: dict[str, Any]) -> None:
    world["catalog"] = {m["name"]: m for m in build_catalog()["video"]["models"]}


@then("Veo 3.1 Fast, Veo 3.1 Quality and Omni 1.1 Flash carry retires_on 2026-12-05")
def _leaving_dates(world: dict[str, Any]) -> None:
    for name in _LEAVING:
        assert world["catalog"][name]["retires_on"] == "2026-12-05", name


@then("every other video model carries retires_on null")
def _others_null(world: dict[str, Any]) -> None:
    for name, entry in world["catalog"].items():
        if name not in _LEAVING:
            assert entry["retires_on"] is None, name


@given(parsers.parse("today is {day}"))
def _today(world: dict[str, Any], day: str) -> None:
    world["today"] = date.fromisoformat(day)


@when(parsers.parse("a generation request selects {label}"))
def _select(world: dict[str, Any], label: str) -> None:
    from gflow_cli.api.model_catalog import warn_if_retiring

    world["error"] = None
    with capture_logs() as logs:
        try:
            world["notice"] = warn_if_retiring(_BY_LABEL[label], today=world["today"])
        except Exception as exc:  # noqa: BLE001 - the scenario asserts there is none
            world["error"] = exc
    world["logs"] = logs


@then(parsers.parse('a "model.retiring" warning names {model} and {day}'))
def _warned(world: dict[str, Any], model: str, day: str) -> None:
    events = [e for e in world["logs"] if e["event"] == "model.retiring"]
    assert len(events) == 1, world["logs"]
    assert (events[0]["model"], events[0]["retires_on"]) == (model, day)
    assert events[0]["log_level"] == "warning"


@then("the request is not refused")
def _not_refused(world: dict[str, Any]) -> None:
    assert world["error"] is None, world["error"]
    assert world["notice"] is not None


@then('no "model.retiring" warning is emitted')
def _not_warned(world: dict[str, Any]) -> None:
    assert world["error"] is None, world["error"]
    assert world["notice"] is None
    assert not [e for e in world["logs"] if e["event"] == "model.retiring"]


@given("the recorded HTrJv catalog")
def _recorded(world: dict[str, Any]) -> None:
    from gflow_cli.api.model_catalog import parse_htrjv_families

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))["families"]
    world["families"] = {family.key: family for family in parse_htrjv_families(payload)}


@then("every gflow video model maps to a family key present in it")
def _mapped(world: dict[str, Any]) -> None:
    from gflow_cli.api.model_catalog import FAMILY_KEY

    for model in VideoModel:
        assert FAMILY_KEY[model] in world["families"], model


@then("each family's retirement date equals gflow's retires_on")
def _dates_agree(world: dict[str, Any]) -> None:
    from gflow_cli.api.model_catalog import FAMILY_KEY, retires_on

    for model in VideoModel:
        assert world["families"][FAMILY_KEY[model]].retires_on == retires_on(model), model


@then("the catalog shows no drift from gflow's model mapping")
def _no_drift(world: dict[str, Any]) -> None:
    from gflow_cli.api.model_catalog import catalog_drift

    assert catalog_drift(list(world["families"].values())) == []


@given(parsers.parse('the recorded HTrJv catalog with a new family "{key}"'))
def _recorded_plus(world: dict[str, Any], key: str) -> None:
    from gflow_cli.api.model_catalog import ModelFamily

    _recorded(world)
    world["families"][key] = ModelFamily(key=key, display_name=key, enabled=True, retires_on=None)


@then(parsers.parse('the catalog drift names "{line}"'))
def _drift_names(world: dict[str, Any], line: str) -> None:
    from gflow_cli.api.model_catalog import catalog_drift

    assert any(d.startswith(line) for d in catalog_drift(list(world["families"].values())))
