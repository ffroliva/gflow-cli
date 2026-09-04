"""Unit tests for MovieManifest and MovieState (movie_manifest.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.composition import Scene, StyleSpec
from gflow_cli.errors import ConfigurationError
from gflow_cli.movie_manifest import (
    CharacterState,
    MovieManifest,
    MovieState,
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
id = "s1"
action = "A quiet forest at dawn"
"""

_FULL_TOML = """\
schema_version = 1
title = "Full Film"
project = "proj-xyz"
output_dir = "./out/full"

[style]
look = "ink"
negative = "no text"

[[characters]]
name = "Alice"
appearance = "red curly hair"
voice = "alnilam"
  [characters.variants]
  white = "solid white"

[[characters]]
name = "Bob"
appearance = "grey beard"

[[scenes]]
id = "intro"
action = "Establishing shot"
framing = "wide"
aspect = "16:9"
duration = 8
# omni-flash, not veo-lite: this fixture pins "a scene carrying BOTH a model and
# a duration", and omni-flash is the only model that renders a duration control.
# With veo-lite it pinned the #634 crash as valid — the same entrenchment #635
# describes for the chain manifest, and the same swap #632 made for the MCP test.
model = "omni-flash"

[[scenes]]
id = "alice-arrives"
action = "Alice walks in"
characters = ["Alice"]
variant = "white"
speaker = "Alice"
line = "Hi"
aspect = "16:9"
duration = 6

[[scenes]]
id = "close-up"
action = "Alice and Bob smile"
framing = "close-up"
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
        assert isinstance(m.style, StyleSpec)
        assert len(m.characters) == 0
        assert len(m.scenes) == 1
        assert m.scenes[0].id == "s1"
        assert m.scenes[0].action == "A quiet forest at dawn"
        assert m.scenes[0].aspect == "16:9"
        assert m.assemble is None
        assert m.output_dir is None
        assert m.continuity == "independent"

    def test_full_parses(self, tmp_path: Path) -> None:
        path = _write_toml(tmp_path, _FULL_TOML)
        m = MovieManifest.from_toml_path(path)
        assert m.title == "Full Film"
        assert m.output_dir == "./out/full"
        assert m.style.look == "ink"
        assert m.style.negative == "no text"
        assert len(m.characters) == 2
        assert m.characters["Alice"].appearance == "red curly hair"
        assert m.characters["Alice"].voice == "alnilam"
        assert m.characters["Alice"].variants["white"] == "solid white"
        assert m.characters["Bob"].appearance == "grey beard"
        assert len(m.scenes) == 3
        assert m.scenes[1].characters == ("Alice",)
        assert m.scenes[1].variant == "white"
        assert m.scenes[1].dialogue[0].speaker == "Alice"
        assert m.scenes[1].dialogue[0].line == "Hi"
        assert m.scenes[2].characters == ("Alice", "Bob")
        assert m.assemble is not None
        assert m.assemble.output == "./out/full/final.mp4"

    def test_parse_full_scene_clip_manifest(self, tmp_path: Path) -> None:
        m = MovieManifest.from_toml_path(
            _write_toml(
                tmp_path,
                """
schema_version = 1
title = "T"
project = "p"

[style]
look = "ink"
negative = "no text"

[[characters]]
name = "Stickman"
appearance = "round head"
voice = "alnilam"
  [characters.variants]
  white = "solid white"

[[scenes]]
id = "s1"
framing = "wide"
action = "walks"
characters = ["Stickman"]
variant = "white"
speaker = "Stickman"
line = "Hi"
duration = 8
""",
            )
        )
        assert isinstance(m.style, StyleSpec) and m.style.look == "ink"
        assert m.characters["Stickman"].voice == "alnilam"
        assert m.characters["Stickman"].variants["white"] == "solid white"
        s = m.scenes[0]
        assert isinstance(s, Scene) and s.id == "s1" and s.framing == "wide"
        assert s.characters == ("Stickman",) and s.variant == "white"
        assert s.dialogue[0].speaker == "Stickman" and s.dialogue[0].line == "Hi"

    def test_scene_defaults(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            'title = "T"\nproject = "p"\n[[scenes]]\nid = "s"\naction = "x"\n',
        )
        s = MovieManifest.from_toml_path(path).scenes[0]
        assert s.aspect == "16:9"
        assert s.duration is None
        assert s.model is None
        assert s.characters == ()
        assert s.dialogue == ()

    def test_character_model_defaults_to_nano2(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            (
                'title = "T"\nproject = "p"\n'
                '[[characters]]\nname = "X"\nappearance = "y"\n'
                '[[scenes]]\nid = "s"\naction = "z"\n'
            ),
        )
        c = MovieManifest.from_toml_path(path).characters["X"]
        assert c.model == "nano2"

    def test_instructions_parsing(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            """
title = "Instructions Film"
project = "proj-123"

[instructions]
[[instructions.card]]
title = "Crayon drawing"
text = "crayon style look"
ref = "./crayon.png"
enabled = true

[[instructions.card]]
title = "Retro look"
text = "seventies polaroid"
enabled = false

[[scenes]]
id = "s1"
action = "walks"
[scenes.instructions]
disable = ["Crayon drawing"]
[[scenes.instructions.card]]
title = "Atmosphere"
text = "dense fog"
ref = ["./fog.png"]
""",
        )
        m = MovieManifest.from_toml_path(path)
        assert len(m.instructions) == 2
        assert m.instructions[0].title == "Crayon drawing"
        assert m.instructions[0].text == "crayon style look"
        assert m.instructions[0].ref == ("./crayon.png",)
        assert m.instructions[0].enabled is True
        assert m.instructions[1].title == "Retro look"
        assert m.instructions[1].enabled is False

        s = m.scenes[0]
        assert s.instructions is not None
        assert s.instructions.disable == ("Crayon drawing",)
        assert len(s.instructions.card) == 1
        assert s.instructions.card[0].title == "Atmosphere"
        assert s.instructions.card[0].ref == ("./fog.png",)

    def test_entity_identity_with_face_prompt(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            (
                'title = "T"\nproject = "p"\n'
                '[[characters]]\nname = "X"\nidentity = "entity"\nface_prompt = "a face"\n'
                '[[scenes]]\nid = "s"\naction = "z"\n'
            ),
        )
        c = MovieManifest.from_toml_path(path).characters["X"]
        assert c.identity == "entity"
        assert c.face_prompt == "a face"


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
        path = _write_toml(tmp_path, 'project = "p"\n[[scenes]]\nid = "s"\naction = "x"\n')
        with pytest.raises(ConfigurationError, match="title"):
            MovieManifest.from_toml_path(path)

    def test_missing_project_raises(self, tmp_path: Path) -> None:
        path = _write_toml(tmp_path, 'title = "T"\n[[scenes]]\nid = "s"\naction = "x"\n')
        with pytest.raises(ConfigurationError, match="project"):
            MovieManifest.from_toml_path(path)

    def test_no_scenes_raises(self, tmp_path: Path) -> None:
        path = _write_toml(tmp_path, 'title = "T"\nproject = "p"\n')
        with pytest.raises(ConfigurationError, match="scene"):
            MovieManifest.from_toml_path(path)

    def test_missing_action_raises(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            'title = "T"\nproject = "p"\n[[scenes]]\nid = "s"\n',
        )
        with pytest.raises(ConfigurationError, match="action"):
            MovieManifest.from_toml_path(path)

    def test_unknown_framing_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="framing"):
            MovieManifest.from_toml_path(
                _write_toml(
                    tmp_path,
                    'title="T"\nproject="p"\n[[scenes]]\nid="s"\nframing="zoomy"\naction="x"\n',
                )
            )

    def test_speaker_must_be_in_characters(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="speaker"):
            MovieManifest.from_toml_path(
                _write_toml(
                    tmp_path,
                    """
title="T"
project="p"
[[characters]]
name="A"
appearance="a"
[[scenes]]
id="s"
action="x"
characters=["A"]
speaker="B"
line="hi"
""",
                )
            )

    def test_shorthand_rejected_for_multi_character(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="per-character"):
            MovieManifest.from_toml_path(
                _write_toml(
                    tmp_path,
                    """
title="T"
project="p"
[[characters]]
name="A"
appearance="a"
[[characters]]
name="B"
appearance="b"
[[scenes]]
id="s"
action="x"
characters=["A","B"]
variant="white"
""",
                )
            )

    def test_invalid_aspect_raises(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            ('title = "T"\nproject = "p"\n[[scenes]]\nid = "s"\naction = "x"\naspect = "4:3"\n'),
        )
        with pytest.raises(ConfigurationError, match="aspect"):
            MovieManifest.from_toml_path(path)

    def test_invalid_duration_raises(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            ('title = "T"\nproject = "p"\n[[scenes]]\nid = "s"\naction = "x"\nduration = 7\n'),
        )
        with pytest.raises(ConfigurationError, match="duration"):
            MovieManifest.from_toml_path(path)

    def test_duplicate_character_name_raises(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            (
                'title = "T"\nproject = "p"\n'
                '[[characters]]\nname = "Alice"\nappearance = "x"\n'
                '[[characters]]\nname = "Alice"\nappearance = "y"\n'
                '[[scenes]]\nid = "s"\naction = "z"\n'
            ),
        )
        with pytest.raises(ConfigurationError, match="Duplicate character"):
            MovieManifest.from_toml_path(path)

    def test_duplicate_scene_id_raises(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            (
                'title = "T"\nproject = "p"\n'
                '[[scenes]]\nid = "s"\naction = "x"\n'
                '[[scenes]]\nid = "s"\naction = "y"\n'
            ),
        )
        with pytest.raises(ConfigurationError, match="Duplicate scene"):
            MovieManifest.from_toml_path(path)

    def test_unknown_character_in_scene_raises(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            (
                'title = "T"\nproject = "p"\n'
                '[[scenes]]\nid = "s"\naction = "x"\n'
                'characters = ["Ghost"]\n'
            ),
        )
        with pytest.raises(ConfigurationError, match="unknown character"):
            MovieManifest.from_toml_path(path)

    def test_entity_identity_without_face_prompt_raises(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            (
                'title = "T"\nproject = "p"\n'
                '[[characters]]\nname = "X"\nidentity = "entity"\n'
                '[[scenes]]\nid = "s"\naction = "z"\n'
            ),
        )
        with pytest.raises(ConfigurationError, match="face_prompt"):
            MovieManifest.from_toml_path(path)

    def test_invalid_character_model_raises(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            (
                'title = "T"\nproject = "p"\n'
                '[[characters]]\nname = "X"\nappearance = "y"\nmodel = "imagen4"\n'
                '[[scenes]]\nid = "s"\naction = "z"\n'
            ),
        )
        with pytest.raises(ConfigurationError, match="model"):
            MovieManifest.from_toml_path(path)

    def test_unknown_variant_rejected(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            (
                'title = "T"\nproject = "p"\n'
                '[[characters]]\nname = "A"\nappearance = "a"\n'
                '[[scenes]]\nid = "s"\naction = "x"\ncharacters = ["A"]\nvariant = "blue"\n'
            ),
        )
        with pytest.raises(ConfigurationError, match="variant"):
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

    def test_version_is_2(self) -> None:
        assert MovieState.VERSION == 2

    def test_save_and_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "movie-state.json"
        state = MovieState(title="T", project="p")
        state.characters["Alice"] = CharacterState(
            entity_id="ent-1",
            image_paths=["/path/to/face.png", None],
        )
        state.scenes["s1"] = SceneState(
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
        assert "s1" in loaded.scenes
        assert loaded.scenes["s1"].media_id == "media-1"
        assert loaded.scenes["s1"].flow_operation_id == "op-1"
        assert loaded.scenes["s1"].status == "completed"

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
        state.scenes["scene-x"] = SceneState(
            media_id="",
            flow_operation_id=None,
            local_path=None,
            status="failed",
        )
        state.save(path)
        loaded = MovieState.load(path, title="T", project="p")
        assert loaded.scenes["scene-x"].status == "failed"

    def test_scene_state_consistency_method_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        state = MovieState(title="T", project="p")
        state.scenes["s1"] = SceneState(
            media_id="m",
            flow_operation_id="op",
            local_path="/out/v.mp4",
            status="completed",
            consistency_method="entity",
        )
        state.save(path)
        loaded = MovieState.load(path, title="T", project="p")
        assert loaded.scenes["s1"].consistency_method == "entity"

    def test_scene_state_consistency_method_defaults_for_old_file(self, tmp_path: Path) -> None:
        # A pre-P2 state file with no consistency_method key loads as "text".
        path = tmp_path / "old-state.json"
        path.write_text(
            '{"version": 2, "title": "T", "project": "p", "characters": {}, '
            '"scenes": {"s1": {"media_id": "m", "flow_operation_id": null, '
            '"local_path": null, "status": "completed"}}}',
            encoding="utf-8",
        )
        loaded = MovieState.load(path, title="T", project="p")
        assert loaded.scenes["s1"].consistency_method == "text"


# --------------------------------------------------------------------------- #
# #634 — duration x model is validated at PARSE time, not at spend time
# --------------------------------------------------------------------------- #
_SCENE_HEAD = 'title = "T"\nproject = "p"\n\n[[scenes]]\nid = "s"\naction = "x"\n'


def test_movie_duration_10_with_veo_model_raises(tmp_path: Path) -> None:
    """10s duration is only available for omni-flash — Veo models cap at 8s."""
    path = _write_toml(tmp_path, _SCENE_HEAD + 'model = "veo-lite"\nduration = 10\n')
    with pytest.raises(ConfigurationError, match="caps at 8s"):
        MovieManifest.from_toml_path(path)


def test_movie_duration_with_veo_lite_is_accepted(tmp_path: Path) -> None:
    """Veo 3.1 models support 4s, 6s, and 8s durations."""
    for model in ("veo-lite", "veo-lite-lp"):
        for dur in (4, 6, 8):
            path = _write_toml(tmp_path, _SCENE_HEAD + f'model = "{model}"\nduration = {dur}\n')
            assert MovieManifest.from_toml_path(path).scenes[0].duration == dur


def test_movie_duration_with_omni_flash_is_accepted(tmp_path: Path) -> None:
    """Negative control: omni-flash DOES render a duration control, so the guard
    must not blanket-ban duration the way chains legitimately do."""
    path = _write_toml(tmp_path, _SCENE_HEAD + 'model = "omni-flash"\nduration = 4\n')
    assert MovieManifest.from_toml_path(path).scenes[0].duration == 4


def test_movie_duration_without_model_is_accepted(tmp_path: Path) -> None:
    """Negative control: no model means Flow's sticky UI default, unknowable
    here, so this stays unguarded BY DESIGN — as t2v/r2v were left after #632."""
    path = _write_toml(tmp_path, _SCENE_HEAD + "duration = 4\n")
    assert MovieManifest.from_toml_path(path).scenes[0].duration == 4


def test_movie_unknown_model_alias_raises_at_parse(tmp_path: Path) -> None:
    """Same class of defect: the alias was only checked to be a *string*, so a
    typo reached `VideoModel.from_cli` inside the render loop and crashed
    mid-spend. Resolve it while parsing instead."""
    path = _write_toml(tmp_path, _SCENE_HEAD + 'model = "veo-lightning"\n')
    with pytest.raises(ConfigurationError, match="model"):
        MovieManifest.from_toml_path(path)


def test_movie_float_duration_with_veo_model_still_parses(tmp_path: Path) -> None:
    """Regression control: `4.0 in {4, 6, 8, 10}` is True, so a float slips past
    the VALUE check — but Scene.duration only keeps ints, so such a manifest used
    to render with the duration silently unset. Guarding the RAW value would have
    turned that into a hard error. The guard tests the coerced value instead."""
    path = _write_toml(tmp_path, _SCENE_HEAD + 'model = "veo-lite"\nduration = 4.0\n')
    assert MovieManifest.from_toml_path(path).scenes[0].duration is None
