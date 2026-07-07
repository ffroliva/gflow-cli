"""Tests for style variant composition, resolution, and handoff (Issue #239)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from gflow_cli.composition import (
    Character,
    Scene,
    StyleSpec,
    build_handoff,
    compose_prompt,
    prompt_hash,
)
from gflow_cli.movie_manifest import MovieState, SceneState

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "schemas" / "movie-handoff.schema.json"
)


# ---------------------------------------------------------------------------
# StyleSpec extensions
# ---------------------------------------------------------------------------


class TestStyleSpecExtensions:
    def test_prefix_and_suffix_fields(self) -> None:
        s = StyleSpec(prefix="PRE:", suffix="SUF.")
        assert s.prefix == "PRE:"
        assert s.suffix == "SUF."

    def test_variants_mapping(self) -> None:
        s = StyleSpec(variants={"warm": "Warm grade.", "cool": "Cool grade."})
        assert s.resolve_suffix("warm") == "Warm grade."
        assert s.resolve_suffix("cool") == "Cool grade."

    def test_resolve_suffix_returns_base_when_no_variant(self) -> None:
        s = StyleSpec(suffix="Base suffix.")
        assert s.resolve_suffix(None) == "Base suffix."

    def test_resolve_suffix_returns_none_for_unknown_variant(self) -> None:
        s = StyleSpec(variants={"warm": "X"})
        assert s.resolve_suffix("nope") is None

    def test_resolve_suffix_returns_none_when_no_suffix(self) -> None:
        s = StyleSpec()
        assert s.resolve_suffix(None) is None


# ---------------------------------------------------------------------------
# compose_prompt — prefix / suffix / variant
# ---------------------------------------------------------------------------


class TestComposePromptStyleVariants:
    def test_suffix_appended(self) -> None:
        style = StyleSpec(suffix="Cinematic, photorealistic.")
        out = compose_prompt(style, Scene(id="s", action="walks"), {})
        assert out.endswith("Cinematic, photorealistic.")
        assert "Walks." in out

    def test_prefix_prepended(self) -> None:
        style = StyleSpec(prefix="SCENE 1:")
        out = compose_prompt(style, Scene(id="s", action="walks"), {})
        assert out.startswith("SCENE 1:")
        assert "Walks." in out

    def test_prefix_and_suffix_together(self) -> None:
        style = StyleSpec(prefix="PRE:", suffix="SUF.")
        out = compose_prompt(style, Scene(id="s", action="go"), {})
        assert out == "PRE: Go. SUF."

    def test_variant_suffix_replaces_base(self) -> None:
        style = StyleSpec(suffix="Base.", variants={"warm": "Warm grade."})
        scene = Scene(id="s", action="walks", style_variant="warm")
        out = compose_prompt(style, scene, {})
        assert "Warm grade." in out
        assert "Base." not in out

    def test_fallback_to_base_when_no_variant(self) -> None:
        style = StyleSpec(suffix="Base.", variants={"warm": "Warm."})
        scene = Scene(id="s", action="walks")
        out = compose_prompt(style, scene, {})
        assert "Base." in out

    def test_style_none_skips_all_suffixes(self) -> None:
        style = StyleSpec(suffix="Cinematic.", prefix="PRE:")
        scene = Scene(id="s", action="walks", style_variant="none")
        out = compose_prompt(style, scene, {})
        assert "Cinematic." not in out
        assert out.startswith("PRE:")
        assert "Walks." in out

    def test_scene_style_suffix_appended_last(self) -> None:
        style = StyleSpec(suffix="Base.")
        scene = Scene(id="s", action="walks", style_suffix="sunset light")
        out = compose_prompt(style, scene, {})
        assert out.endswith("sunset light")
        assert "Base." in out

    def test_prefix_composes_with_suffix_and_scene_suffix(self) -> None:
        style = StyleSpec(prefix="P:", suffix="S.", variants={"w": "W."})
        scene = Scene(id="s", action="go", style_variant="w", style_suffix="extra")
        out = compose_prompt(style, scene, {})
        assert out == "P: Go. W. extra"

    def test_empty_action_with_prefix_only(self) -> None:
        style = StyleSpec(prefix="SCENE:")
        out = compose_prompt(style, Scene(id="s", action=""), {})
        assert out == "SCENE:"


# ---------------------------------------------------------------------------
# prompt_hash
# ---------------------------------------------------------------------------


class TestPromptHash:
    def test_hash_is_sha256_hex(self) -> None:
        h = prompt_hash("hello")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_same_input_same_hash(self) -> None:
        assert prompt_hash("abc") == prompt_hash("abc")

    def test_different_input_different_hash(self) -> None:
        assert prompt_hash("abc") != prompt_hash("xyz")

    def test_hash_changes_when_suffix_changes(self) -> None:
        s1 = StyleSpec(suffix="A")
        s2 = StyleSpec(suffix="B")
        scene = Scene(id="s", action="walks")
        h1 = prompt_hash(compose_prompt(s1, scene, {}))
        h2 = prompt_hash(compose_prompt(s2, scene, {}))
        assert h1 != h2

    def test_hash_changes_when_prefix_changes(self) -> None:
        s1 = StyleSpec(prefix="A")
        s2 = StyleSpec(prefix="B")
        scene = Scene(id="s", action="walks")
        h1 = prompt_hash(compose_prompt(s1, scene, {}))
        h2 = prompt_hash(compose_prompt(s2, scene, {}))
        assert h1 != h2

    def test_hash_changes_when_scene_suffix_changes(self) -> None:
        s = StyleSpec(suffix="Base.")
        s1 = Scene(id="s", action="walks", style_suffix="X")
        s2 = Scene(id="s", action="walks", style_suffix="Y")
        h1 = prompt_hash(compose_prompt(s, s1, {}))
        h2 = prompt_hash(compose_prompt(s, s2, {}))
        assert h1 != h2

    def test_hash_changes_when_variant_changes(self) -> None:
        s = StyleSpec(variants={"a": "A.", "b": "B."})
        s1 = Scene(id="s", action="walks", style_variant="a")
        s2 = Scene(id="s", action="walks", style_variant="b")
        h1 = prompt_hash(compose_prompt(s, s1, {}))
        h2 = prompt_hash(compose_prompt(s, s2, {}))
        assert h1 != h2


# ---------------------------------------------------------------------------
# build_handoff — style_applied
# ---------------------------------------------------------------------------


class _FakeManifest:
    title = "T"
    project = "p"
    style = StyleSpec(suffix="Cinematic.", variants={"warm": "Warm grade."})
    characters: dict[str, Character] = {}
    scenes = (
        Scene(id="s1", action="walks", style_variant="warm"),
        Scene(id="s2", action="runs"),
        Scene(id="s3", action="jumps", style_variant="none"),
        Scene(id="s4", action="flies", style_suffix="golden hour"),
    )


def _fake_state() -> MovieState:
    st = MovieState(title="T", project="p")
    for sid in ("s1", "s2", "s3", "s4"):
        st.scenes[sid] = SceneState(
            media_id=f"m-{sid}",
            flow_operation_id=f"op-{sid}",
            local_path=f"/out/{sid}.mp4",
            status="completed",
        )
    return st


def test_handoff_style_applied_variant() -> None:
    h = build_handoff(_FakeManifest(), _fake_state(), out_dir=Path("/out"))
    clip = next(c for c in h["clips"] if c["id"] == "s1")
    assert clip["style_applied"]["variant"] == "warm"
    assert clip["style_applied"]["suffix"] == "Warm grade."


def test_handoff_style_applied_base_suffix() -> None:
    h = build_handoff(_FakeManifest(), _fake_state(), out_dir=Path("/out"))
    clip = next(c for c in h["clips"] if c["id"] == "s2")
    assert clip["style_applied"]["variant"] is None
    assert clip["style_applied"]["suffix"] == "Cinematic."


def test_handoff_style_applied_none_variant() -> None:
    h = build_handoff(_FakeManifest(), _fake_state(), out_dir=Path("/out"))
    clip = next(c for c in h["clips"] if c["id"] == "s3")
    assert clip["style_applied"]["variant"] == "none"
    assert clip["style_applied"]["suffix"] is None


def test_handoff_style_applied_scene_suffix() -> None:
    h = build_handoff(_FakeManifest(), _fake_state(), out_dir=Path("/out"))
    clip = next(c for c in h["clips"] if c["id"] == "s4")
    assert clip["style_applied"]["scene_suffix"] == "golden hour"
    assert clip["style_applied"]["suffix"] == "Cinematic."


def test_handoff_validates_against_schema() -> None:
    h = build_handoff(_FakeManifest(), _fake_state(), out_dir=Path("/out"))
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(h, schema)


def test_handoff_empty_style_applied_when_no_style_fields() -> None:
    class _PlainManifest:
        title = "T"
        project = "p"
        style = StyleSpec()
        characters: dict[str, Character] = {}
        scenes = (Scene(id="s1", action="walks"),)

    st = MovieState(title="T", project="p")
    st.scenes["s1"] = SceneState(
        media_id="m", flow_operation_id="op", local_path="/out/s1.mp4", status="completed"
    )
    h = build_handoff(_PlainManifest(), st, out_dir=Path("/out"))
    assert h["clips"][0]["style_applied"] == {}
