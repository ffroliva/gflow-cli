"""End-to-end integration tests for style variants (Issue #239).

Exercises the full pipeline: TOML parse → MovieManifest → compose_prompt →
resume_hash → build_handoff, verifying all pieces work together correctly.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from gflow_cli.composition import (
    build_handoff,
    compose_prompt,
    resume_hash,
)
from gflow_cli.movie_manifest import (
    MovieManifest,
    MovieState,
    SceneState,
)

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "schemas" / "movie-handoff.schema.json"
)


def _write_toml(tmp_path: Path, content: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "movie.toml"
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Full pipeline: TOML → manifest → compose → hash → handoff
# ---------------------------------------------------------------------------


class TestStyleVariantsE2E:
    """End-to-end tests that parse a real TOML, compose prompts, hash them,
    build a handoff, and validate the handoff against the JSON schema."""

    def test_arc_manifest_parses_and_composes(self, tmp_path: Path) -> None:
        """A monochrome-to-warm arc: 3 scenes with different style selections."""
        toml = """\
title = "Arc Test"
project = "proj-arc"

[style]
suffix = "Black and white cinematic."

[style.variants.warm]
suffix = "Warm golden-hour grade."

[style.variants.cool]
suffix = "Cool blue grade."

[[scenes]]
id = "s1"
action = "walks in the rain"
style_variant = "cool"

[[scenes]]
id = "s2"
action = "stands at a crossroads"

[[scenes]]
id = "s3"
action = "smiles in sunlight"
style_variant = "warm"
style_suffix = "lens flare"
"""
        m = MovieManifest.from_toml_path(_write_toml(tmp_path, toml))

        # Verify manifest parsed correctly
        assert m.style.suffix == "Black and white cinematic."
        assert m.style.variants == {"warm": "Warm golden-hour grade.", "cool": "Cool blue grade."}
        assert len(m.scenes) == 3
        assert m.scenes[0].style_variant == "cool"
        assert m.scenes[1].style_variant is None
        assert m.scenes[2].style_variant == "warm"
        assert m.scenes[2].style_suffix == "lens flare"

        # Compose prompts for each scene
        prompts = [compose_prompt(m.style, s, m.characters) for s in m.scenes]

        # s1: cool variant suffix
        assert prompts[0].endswith("Cool blue grade.")
        assert "Black and white" not in prompts[0]

        # s2: base suffix (no variant)
        assert prompts[1].endswith("Black and white cinematic.")

        # s3: warm variant + scene suffix
        assert prompts[2].endswith("Warm golden-hour grade. lens flare")
        assert "Black and white" not in prompts[2]

        # Hashes are all different (different styles)
        hashes = [resume_hash(p) for p in prompts]
        assert len(set(hashes)) == 3

    def test_arc_handoff_records_style_applied(self, tmp_path: Path) -> None:
        """Build handoff from the arc manifest; verify style_applied per clip."""
        toml = """\
title = "Arc"
project = "p"

[style]
suffix = "Base."

[style.variants.warm]
suffix = "Warm."

[[scenes]]
id = "s1"
action = "walks"
style_variant = "warm"

[[scenes]]
id = "s2"
action = "runs"

[[scenes]]
id = "s3"
action = "jumps"
style_variant = "none"
style_suffix = "sunset"
"""
        m = MovieManifest.from_toml_path(_write_toml(tmp_path, toml))

        state = MovieState(title="Arc", project="p")
        for sid in ("s1", "s2", "s3"):
            state.scenes[sid] = SceneState(
                media_id=f"m-{sid}",
                flow_operation_id=f"op-{sid}",
                local_path=f"/out/{sid}.mp4",
                status="completed",
            )

        h = build_handoff(m, state, out_dir=Path("/out"))
        schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(h, schema)

        clips = {c["id"]: c for c in h["clips"]}

        # s1: warm variant
        assert clips["s1"]["style_applied"]["variant"] == "warm"
        assert clips["s1"]["style_applied"]["suffix"] == "Warm."

        # s2: base suffix
        assert clips["s2"]["style_applied"]["variant"] is None
        assert clips["s2"]["style_applied"]["suffix"] == "Base."

        # s3: none + scene suffix
        assert clips["s3"]["style_applied"]["variant"] == "none"
        assert clips["s3"]["style_applied"]["suffix"] is None
        assert clips["s3"]["style_applied"]["scene_suffix"] == "sunset"

    def test_resume_hash_detects_style_change(self, tmp_path: Path) -> None:
        """Simulate: run with style A, change to style B, verify hash mismatch."""
        base_toml = """\
title = "T"
project = "p"

[style]
suffix = "Original."

[[scenes]]
id = "s1"
action = "walks"
"""
        m1 = MovieManifest.from_toml_path(_write_toml(tmp_path, base_toml))
        prompt1 = compose_prompt(m1.style, m1.scenes[0], m1.characters)
        hash1 = resume_hash(prompt1)

        # Simulate editing the suffix
        changed_toml = """\
title = "T"
project = "p"

[style]
suffix = "Changed."

[[scenes]]
id = "s1"
action = "walks"
"""
        m2 = MovieManifest.from_toml_path(_write_toml(tmp_path / "v2", changed_toml))
        prompt2 = compose_prompt(m2.style, m2.scenes[0], m2.characters)
        hash2 = resume_hash(prompt2)

        # Hashes differ → resume would re-run this scene
        assert hash1 != hash2

        # Same manifest → same hash (idempotent)
        prompt1b = compose_prompt(m1.style, m1.scenes[0], m1.characters)
        assert resume_hash(prompt1b) == hash1

    def test_state_round_trip_with_style_hash(self, tmp_path: Path) -> None:
        """SceneState with style_hash round-trips through save/load."""
        from gflow_cli.movie_manifest import MovieState

        state = MovieState(title="T", project="p")
        state.scenes["s1"] = SceneState(
            media_id="m1",
            flow_operation_id="op1",
            local_path="/out/s1.mp4",
            status="completed",
            style_hash="abc123def456",
        )
        state_path = tmp_path / "state.json"
        state.save(state_path)

        loaded = MovieState.load(state_path, title="T", project="p")
        assert loaded.scenes["s1"].style_hash == "abc123def456"

    def test_style_variant_validation_rejects_unknown(self, tmp_path: Path) -> None:
        """Unknown style_variant raises ConfigurationError with the name and defined set."""
        from gflow_cli.errors import ConfigurationError

        toml = """\
title = "T"
project = "p"

[style]
suffix = "Base."

[style.variants.warm]
suffix = "Warm."

[[scenes]]
id = "s1"
action = "walks"
style_variant = "typo_variant"
"""
        with pytest.raises(ConfigurationError, match="style_variant.*typo_variant"):
            MovieManifest.from_toml_path(_write_toml(tmp_path, toml))

    def test_full_manifest_with_characters_and_style(self, tmp_path: Path) -> None:
        """Full manifest: characters + style variants + dialogue + style fields."""
        toml = """\
title = "Full"
project = "p"

[style]
look = "cinematic"
suffix = "Photorealistic."

[style.variants.dream]
suffix = "Dreamy soft-focus."

[[characters]]
name = "Alice"
appearance = "red hair"
voice = "alnilam"
  [characters.variants]
  formal = "wearing a suit"

[[scenes]]
id = "s1"
action = "Alice walks"
characters = ["Alice"]
variant = "formal"
style_variant = "dream"
speaker = "Alice"
line = "Hello world"
aspect = "9:16"
duration = 8

[[scenes]]
id = "s2"
action = "Alice runs"
characters = ["Alice"]
style_suffix = "golden hour"
"""
        m = MovieManifest.from_toml_path(_write_toml(tmp_path, toml))

        # Compose both scenes
        p1 = compose_prompt(m.style, m.scenes[0], m.characters)
        p2 = compose_prompt(m.style, m.scenes[1], m.characters)

        # s1: character variant "formal" + style variant "dream"
        assert "wearing a suit" in p1
        assert p1.endswith("Dreamy soft-focus.")
        assert "Photorealistic." not in p1

        # s2: base style + scene suffix
        assert p2.endswith("Photorealistic. golden hour")

        # Handoff validates
        state = MovieState(title="Full", project="p")
        for sid in ("s1", "s2"):
            state.scenes[sid] = SceneState(
                media_id=f"m-{sid}",
                flow_operation_id=f"op-{sid}",
                local_path=f"/out/{sid}.mp4",
                status="completed",
            )
        h = build_handoff(m, state, out_dir=Path("/out"))
        schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(h, schema)
