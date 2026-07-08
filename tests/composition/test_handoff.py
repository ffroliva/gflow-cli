"""Golden round-trip + schema tests for build_handoff (composition.py)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from gflow_cli import __version__
from gflow_cli.composition import Character, Scene, StyleSpec, build_handoff
from gflow_cli.movie_manifest import CharacterState, MovieState, SceneState

# Resolve the schema relative to the repo root (cwd-independent).
_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "schemas" / "movie-handoff.schema.json"
)


class _FakeManifest:
    title = "T"
    project = "p"
    style = StyleSpec(look="ink", negative="no text")
    characters = {"Stickman": Character(name="Stickman", identity="text", voice="alnilam")}
    scenes = (Scene(id="s1", action="walks", framing="wide", characters=("Stickman",), duration=8),)


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
    assert handoff["generator"]["name"] == "gflow-cli"
    assert handoff["generator"]["version"] == __version__
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
    h = build_handoff(_FakeManifest(), _fake_state(), out_dir=Path("/out/x"), include_prompts=False)
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


def test_build_handoff_consistency_method_defaults_to_text(tmp_path: Path) -> None:
    h = build_handoff(_FakeManifest(), _fake_state(), out_dir=Path("/out/x"))
    assert h["clips"][0]["consistency_method"] == "text"


def test_build_handoff_reflects_entity_consistency_method(tmp_path: Path) -> None:
    state = _fake_state()
    state.scenes["s1"].consistency_method = "entity"
    h = build_handoff(_FakeManifest(), state, out_dir=Path("/out/x"))
    assert h["clips"][0]["consistency_method"] == "entity"
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(h, schema)  # entity must be a valid schema enum value


class _EntityManifest:
    """Manifest with one entity-identity character (has entity_id in state)."""

    title = "T"
    project = "p"
    style = StyleSpec()
    characters = {
        "Hero": Character(
            name="Hero", identity="entity", face_prompt="heroic face", voice="alnilam"
        )
    }
    scenes = (Scene(id="s1", action="walks", characters=("Hero",), duration=8),)


def test_build_handoff_character_entity_id_in_x_gflow(tmp_path: Path) -> None:
    """build_handoff populates x_gflow.entity_id for entity-identity characters
    whose entity_id is recorded in state (spec §7)."""
    state = MovieState(title="T", project="p")
    state.characters["Hero"] = CharacterState(entity_id="ent-abc", image_paths=[])
    state.scenes["s1"] = SceneState(
        media_id="m", flow_operation_id="op", local_path="/out/s1.mp4", status="completed"
    )
    h = build_handoff(_EntityManifest(), state, out_dir=Path("/out"))
    char = h["characters"][0]
    assert char["name"] == "Hero"
    assert char["x_gflow"]["entity_id"] == "ent-abc"


def test_build_handoff_character_no_entity_id_when_not_created(tmp_path: Path) -> None:
    """build_handoff omits entity_id when the character was never created
    (text-identity chars, or entity chars not yet run)."""
    state = MovieState(title="T", project="p")
    h = build_handoff(_FakeManifest(), state, out_dir=Path("/out/x"))
    char = h["characters"][0]
    assert "entity_id" not in char["x_gflow"]
