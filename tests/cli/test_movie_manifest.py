"""Unit tests for MovieManifest and MovieState (movie_manifest.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gflow_cli.errors import ConfigurationError
from gflow_cli.movie_manifest import (
    AssemblyDef,
    CharacterDef,
    CharacterState,
    MovieManifest,
    MovieState,
    SceneDef,
    SceneState,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "movie.toml"
    p.write_text(content, encoding="utf-8")
    return p


_MINIMAL_TOML = """\
title = "Test Film"
project = "proj-abc"

[[scenes]]
title = "Scene 1"
type = "t2v"
prompt = "A quiet forest at dawn"
"""

_FULL_TOML = """\
title = "Full Film"
project = "proj-xyz"
output_dir = "./out/full"

[[characters]]
name = "Alice"
face_prompt = "Young woman with red curly hair"
body_prompt = "Casual jeans and jacket"
model = "nano2"

[[characters]]
name = "Bob"
face_prompt = "Middle-aged man with grey beard"

[[scenes]]
title = "Intro"
type = "t2v"
prompt = "Establishing shot"
aspect = "16:9"
duration = 8
model = "veo-lite"
count = 1

[[scenes]]
title = "Alice Arrives"
type = "r2v"
prompt = "Alice walks in"
characters = ["Alice"]
aspect = "16:9"
duration = 6

[[scenes]]
title = "Close-up"
type = "r2v"
prompt = "Alice smiles"
characters = ["Alice", "Bob"]

[assemble]
output = "./out/full/final.mp4"
"""


# ---------------------------------------------------------------------------
# MovieManifest — valid inputs
# ---------------------------------------------------------------------------


class TestMovieManifestValid:
    def test_minimal_parses(self, tmp_path: Path) -> None:
        path = _write_toml(tmp_path, _MINIMAL_TOML)
        m = MovieManifest.from_toml_path(path)
        assert m.title == "Test Film"
        assert m.project == "proj-abc"
        assert len(m.characters) == 0
        assert len(m.scenes) == 1
        assert m.scenes[0].title == "Scene 1"
        assert m.scenes[0].type == "t2v"
        assert m.scenes[0].aspect == "16:9"
        assert m.scenes[0].count == 1
        assert m.assemble is None
        assert m.output_dir is None

    def test_full_parses(self, tmp_path: Path) -> None:
        path = _write_toml(tmp_path, _FULL_TOML)
        m = MovieManifest.from_toml_path(path)
        assert m.title == "Full Film"
        assert m.output_dir == "./out/full"
        assert len(m.characters) == 2
        assert m.characters[0].name == "Alice"
        assert m.characters[0].body_prompt == "Casual jeans and jacket"
        assert m.characters[1].name == "Bob"
        assert m.characters[1].body_prompt is None
        assert len(m.scenes) == 3
        assert m.scenes[1].characters == ("Alice",)
        assert m.scenes[2].characters == ("Alice", "Bob")
        assert m.assemble is not None
        assert m.assemble.output == "./out/full/final.mp4"

    def test_scene_defaults(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            'title = "T"\nproject = "p"\n[[scenes]]\ntitle = "S"\ntype = "t2v"\nprompt = "x"\n',
        )
        s = MovieManifest.from_toml_path(path).scenes[0]
        assert s.aspect == "16:9"
        assert s.duration is None
        assert s.model is None
        assert s.count == 1
        assert s.characters == ()

    def test_character_model_defaults_to_nano2(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            (
                'title = "T"\nproject = "p"\n'
                '[[characters]]\nname = "X"\nface_prompt = "y"\n'
                '[[scenes]]\ntitle = "S"\ntype = "t2v"\nprompt = "z"\n'
            ),
        )
        c = MovieManifest.from_toml_path(path).characters[0]
        assert c.model == "nano2"

    def test_i2v_with_initial_frame(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            (
                'title = "T"\nproject = "p"\n'
                '[[scenes]]\ntitle = "S"\ntype = "i2v"\n'
                'prompt = "x"\ninitial_frame = "/tmp/frame.png"\n'
            ),
        )
        s = MovieManifest.from_toml_path(path).scenes[0]
        assert s.initial_frame == "/tmp/frame.png"
        assert s.end_frame is None


# ---------------------------------------------------------------------------
# MovieManifest — invalid inputs
# ---------------------------------------------------------------------------


class TestMovieManifestInvalid:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="not found"):
            MovieManifest.from_toml_path(tmp_path / "nonexistent.toml")

    def test_toml_syntax_error_raises(self, tmp_path: Path) -> None:
        path = _write_toml(tmp_path, "title = [unterminated")
        with pytest.raises(ConfigurationError, match="Failed to parse"):
            MovieManifest.from_toml_path(path)

    def test_missing_title_raises(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path, 'project = "p"\n[[scenes]]\ntitle = "S"\ntype = "t2v"\nprompt = "x"\n'
        )
        with pytest.raises(ConfigurationError, match="title"):
            MovieManifest.from_toml_path(path)

    def test_missing_project_raises(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path, 'title = "T"\n[[scenes]]\ntitle = "S"\ntype = "t2v"\nprompt = "x"\n'
        )
        with pytest.raises(ConfigurationError, match="project"):
            MovieManifest.from_toml_path(path)

    def test_no_scenes_raises(self, tmp_path: Path) -> None:
        path = _write_toml(tmp_path, 'title = "T"\nproject = "p"\n')
        with pytest.raises(ConfigurationError, match="scene"):
            MovieManifest.from_toml_path(path)

    def test_invalid_scene_type_raises(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            'title = "T"\nproject = "p"\n[[scenes]]\ntitle = "S"\ntype = "xyz"\nprompt = "x"\n',
        )
        with pytest.raises(ConfigurationError, match="type"):
            MovieManifest.from_toml_path(path)

    def test_invalid_aspect_raises(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            (
                'title = "T"\nproject = "p"\n'
                '[[scenes]]\ntitle = "S"\ntype = "t2v"\nprompt = "x"\naspect = "4:3"\n'
            ),
        )
        with pytest.raises(ConfigurationError, match="aspect"):
            MovieManifest.from_toml_path(path)

    def test_invalid_duration_raises(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            (
                'title = "T"\nproject = "p"\n'
                '[[scenes]]\ntitle = "S"\ntype = "t2v"\nprompt = "x"\nduration = 7\n'
            ),
        )
        with pytest.raises(ConfigurationError, match="duration"):
            MovieManifest.from_toml_path(path)

    def test_count_out_of_range_raises(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            (
                'title = "T"\nproject = "p"\n'
                '[[scenes]]\ntitle = "S"\ntype = "t2v"\nprompt = "x"\ncount = 5\n'
            ),
        )
        with pytest.raises(ConfigurationError, match="count"):
            MovieManifest.from_toml_path(path)

    def test_duplicate_character_name_raises(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            (
                'title = "T"\nproject = "p"\n'
                '[[characters]]\nname = "Alice"\nface_prompt = "x"\n'
                '[[characters]]\nname = "Alice"\nface_prompt = "y"\n'
                '[[scenes]]\ntitle = "S"\ntype = "t2v"\nprompt = "z"\n'
            ),
        )
        with pytest.raises(ConfigurationError, match="Duplicate character"):
            MovieManifest.from_toml_path(path)

    def test_duplicate_scene_title_raises(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            (
                'title = "T"\nproject = "p"\n'
                '[[scenes]]\ntitle = "S"\ntype = "t2v"\nprompt = "x"\n'
                '[[scenes]]\ntitle = "S"\ntype = "t2v"\nprompt = "y"\n'
            ),
        )
        with pytest.raises(ConfigurationError, match="Duplicate scene"):
            MovieManifest.from_toml_path(path)

    def test_unknown_character_in_scene_raises(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            (
                'title = "T"\nproject = "p"\n'
                '[[scenes]]\ntitle = "S"\ntype = "r2v"\nprompt = "x"\n'
                'characters = ["Ghost"]\n'
            ),
        )
        with pytest.raises(ConfigurationError, match="unknown character"):
            MovieManifest.from_toml_path(path)

    def test_i2v_missing_initial_frame_raises(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            'title = "T"\nproject = "p"\n[[scenes]]\ntitle = "S"\ntype = "i2v"\nprompt = "x"\n',
        )
        with pytest.raises(ConfigurationError, match="initial_frame"):
            MovieManifest.from_toml_path(path)

    def test_invalid_character_model_raises(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            (
                'title = "T"\nproject = "p"\n'
                '[[characters]]\nname = "X"\nface_prompt = "y"\nmodel = "imagen4"\n'
                '[[scenes]]\ntitle = "S"\ntype = "t2v"\nprompt = "z"\n'
            ),
        )
        with pytest.raises(ConfigurationError, match="model"):
            MovieManifest.from_toml_path(path)


# ---------------------------------------------------------------------------
# MovieState
# ---------------------------------------------------------------------------


class TestMovieState:
    def test_empty_state_for_missing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "movie-state.json"
        state = MovieState.load(path, title="T", project="p")
        assert state.characters == {}
        assert state.scenes == {}

    def test_save_and_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "movie-state.json"
        state = MovieState(title="T", project="p")
        state.characters["Alice"] = CharacterState(
            entity_id="ent-1",
            image_paths=["/path/to/face.png", None],
        )
        state.scenes["Scene 1"] = SceneState(
            media_id="media-1",
            flow_operation_id="op-1",
            local_path="/out/video.mp4",
            status="completed",
        )
        state.save(path)
        assert path.exists()

        loaded = MovieState.load(path, title="T", project="p")
        assert "Alice" in loaded.characters
        assert loaded.characters["Alice"].entity_id == "ent-1"
        assert loaded.characters["Alice"].image_paths == ["/path/to/face.png", None]
        assert "Scene 1" in loaded.scenes
        assert loaded.scenes["Scene 1"].media_id == "media-1"
        assert loaded.scenes["Scene 1"].flow_operation_id == "op-1"
        assert loaded.scenes["Scene 1"].status == "completed"

    def test_corrupted_state_file_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "movie-state.json"
        path.write_text("not json{{", encoding="utf-8")
        state = MovieState.load(path, title="T", project="p")
        assert state.characters == {}
        assert state.scenes == {}

    def test_state_path_for(self, tmp_path: Path) -> None:
        manifest = tmp_path / "my-film.toml"
        assert MovieState.state_path_for(manifest) == tmp_path / "my-film-state.json"

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "state.json"
        state = MovieState(title="T", project="p")
        state.save(nested)
        assert nested.exists()

    def test_scene_state_failed_status(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        state = MovieState(title="T", project="p")
        state.scenes["Scene X"] = SceneState(
            media_id="",
            flow_operation_id=None,
            local_path=None,
            status="failed",
        )
        state.save(path)
        loaded = MovieState.load(path, title="T", project="p")
        assert loaded.scenes["Scene X"].status == "failed"
