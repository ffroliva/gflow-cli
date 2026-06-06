"""Golden round-trip + schema tests for build_handoff (composition.py)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from gflow_cli.composition import Character, Scene, StyleSpec, build_handoff
from gflow_cli.movie_manifest import MovieState, SceneState

# Resolve the schema relative to the repo root (cwd-independent).
_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "schemas" / "movie-handoff.schema.json"
)


class _FakeManifest:
    title = "T"
    project = "p"
    style = StyleSpec(look="ink", negative="no text")
    characters = {"Stickman": Character(name="Stickman", identity="text", voice="alnilam")}
    scenes = (
        Scene(id="s1", action="walks", framing="wide", characters=("Stickman",), duration=8),
    )


def _fake_state() -> MovieState:
    st = MovieState(title="T", project="p")
    st.scenes["s1"] = SceneState(
        media_id="m",
        flow_operation_id="op-1",
        local_path="/out/x/s1.mp4",
        status="completed",
    )
    return st


def test_build_handoff_shape_and_schema(tmp_path: Path) -> None:
    handoff = build_handoff(_FakeManifest(), _fake_state(), out_dir=Path("/out/x"))
    assert handoff["schema_version"] == 1
    assert handoff["clips"][0]["index"] == 0
    assert handoff["clips"][0]["id"] == "s1"
    # relative POSIX path, no backslashes, no signed-url leak
    assert handoff["clips"][0]["file"] == "s1.mp4"
    blob = json.dumps(handoff)
    assert "fifeUrl" not in blob and "\\\\" not in blob and "Bearer" not in blob

    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(handoff, schema)  # raises on contract drift


def test_build_handoff_includes_prompt_by_default(tmp_path: Path) -> None:
    h = build_handoff(_FakeManifest(), _fake_state(), out_dir=Path("/out/x"))
    assert h["clips"][0]["prompt"]  # composed, non-empty


def test_build_handoff_uses_stored_prompt(tmp_path: Path) -> None:
    state = _fake_state()
    state.scenes["s1"].prompt = "STORED PROMPT"
    h = build_handoff(_FakeManifest(), state, out_dir=Path("/out/x"))
    assert h["clips"][0]["prompt"] == "STORED PROMPT"


def test_build_handoff_redacts_prompt_when_disabled(tmp_path: Path) -> None:
    h = build_handoff(
        _FakeManifest(), _fake_state(), out_dir=Path("/out/x"), include_prompts=False
    )
    assert h["clips"][0]["prompt"] is None


def test_build_handoff_x_gflow_carries_internal_ids(tmp_path: Path) -> None:
    h = build_handoff(_FakeManifest(), _fake_state(), out_dir=Path("/out/x"))
    xg = h["clips"][0]["x_gflow"]
    assert xg["media_id"] == "m"
    assert xg["operation_id"] == "op-1"
    assert xg["project_id"] == "p"


def test_build_handoff_missing_scene_state_is_failed(tmp_path: Path) -> None:
    state = MovieState(title="T", project="p")  # no scene state at all
    h = build_handoff(_FakeManifest(), state, out_dir=Path("/out/x"))
    assert h["clips"][0]["status"] == "failed"
    assert h["clips"][0]["file"] is None
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(h, schema)
