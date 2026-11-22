"""Tests for the pure ogiZ0b frame builder."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import pytest

from gflow_cli.api.image import Aspect
from gflow_cli.api.transports.frame_builder import build_ogiz0b_body


def _decode_body(body: str) -> tuple[str, list[object], str]:
    form = parse_qs(body)
    f_req_value = form["f.req"][0]
    outer = json.loads(f_req_value)
    rpcid, inner_json, _, _ = outer[0][0]
    return rpcid, json.loads(inner_json), inner_json


def test_builds_proven_ogiz0b_wire_shape() -> None:
    body = build_ogiz0b_body(
        prompt="a fox",
        model="IMAGEN",
        project_id="project-123",
        recaptcha_token="captcha-456",
        at_token="at-789",
        batch_id="batch-abc",
        entry_seeds=(12345,),
    )

    form = parse_qs(body)
    outer = json.loads(form["f.req"][0])
    assert len(outer) == 1
    assert len(outer[0]) == 1
    rpcid, inner_json, none_value, generic = outer[0][0]
    assert rpcid == "ogiZ0b"
    assert isinstance(inner_json, str)
    assert none_value is None
    assert generic == "generic"
    assert form["at"] == ["at-789"]

    inner = json.loads(inner_json)
    ctx = inner[3]
    entry = inner[1][0]
    assert len(ctx) == 11
    assert ctx[5] == "project-123"
    assert ctx[10] == ["captcha-456", 1]
    assert len(entry) == 12
    assert entry[3] == 12345
    assert entry[4] == 2
    assert entry[5] == "IMAGEN"
    assert entry[8] == [[["a fox"]]]
    assert inner[4] == ["batch-abc"]
    assert entry[7] == ctx
    assert entry[7] is not ctx


def test_build_is_deterministic_for_the_same_inputs() -> None:
    values = {
        "prompt": "a fox",
        "model": "IMAGEN",
        "project_id": "project-123",
        "recaptcha_token": "captcha-456",
        "at_token": "at-789",
        "batch_id": "batch-abc",
        "entry_seeds": (12345,),
    }

    assert build_ogiz0b_body(**values) == build_ogiz0b_body(**values)


def test_prompt_round_trips_through_form_and_compact_inner_json() -> None:
    prompt = 'a "fox" & café\n'
    body = build_ogiz0b_body(
        prompt=prompt,
        model="IMAGEN",
        project_id="project-123",
        recaptcha_token="captcha-456",
        at_token="at-789",
        batch_id="batch-abc",
        entry_seeds=(12345,),
    )

    rpcid, inner, inner_json = _decode_body(body)

    assert rpcid == "ogiZ0b"
    assert inner[1][0][8] == [[[prompt]]]
    assert inner_json == json.dumps(inner, separators=(",", ":"))


@pytest.mark.parametrize(
    ("aspect", "wire"),
    [
        (Aspect.SQUARE, 1),
        (Aspect.PORTRAIT, 2),
        (Aspect.LANDSCAPE, 3),
        (Aspect.PORTRAIT_THREE_FOUR, 4),
        (Aspect.LANDSCAPE_FOUR_THREE, 5),
    ],
)
def test_aspect_goes_to_entry_4_as_flows_own_enum(aspect: Aspect, wire: int) -> None:
    """Values measured 2026-11-22 from route-aborted captures of Flow's own submit."""
    _, inner, _ = _decode_body(_build(aspect=aspect))
    assert inner[1][0][4] == wire


def test_count_is_one_entry_per_image_each_with_its_own_seed() -> None:
    _, inner, _ = _decode_body(_build(entry_seeds=(11, 22)))
    entries = inner[1]
    assert [e[3] for e in entries] == [11, 22]
    assert all(e[7] == inner[3] for e in entries)
    assert entries[0] is not entries[1]
    assert inner[4] == ["batch-abc"]  # one batch id for the whole POST


def test_no_seed_is_refused() -> None:
    with pytest.raises(ValueError, match="entry_seeds"):
        _build(entry_seeds=())


def _build(**overrides: object) -> str:
    values: dict[str, object] = {
        "prompt": "a fox",
        "model": "IMAGEN",
        "project_id": "project-123",
        "recaptcha_token": "captcha-456",
        "at_token": "at-789",
        "batch_id": "batch-abc",
        "entry_seeds": (12345,),
    }
    return build_ogiz0b_body(**(values | overrides))  # type: ignore[arg-type]
