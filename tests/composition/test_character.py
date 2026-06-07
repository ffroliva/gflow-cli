import pytest

from gflow_cli.composition import FRAMING, Character, DialogueLine, Scene, StyleSpec


def test_style_spec_all_optional() -> None:
    s = StyleSpec()
    assert s.look is None and s.negative is None


def test_character_resolve_variant_merges_delta() -> None:
    c = Character(name="Stickman", appearance="round head", variants={"white": "solid white lines"})
    assert c.resolve_variant("white") == "round head, solid white lines"


def test_character_resolve_variant_none_returns_base() -> None:
    c = Character(name="Stickman", appearance="round head")
    assert c.resolve_variant(None) == "round head"


def test_character_resolve_unknown_variant_raises() -> None:
    c = Character(name="Stickman", appearance="round head", variants={"white": "w"})
    with pytest.raises(ValueError, match="unknown variant 'blue'"):
        c.resolve_variant("blue")


def test_character_resolve_variant_no_appearance() -> None:
    c = Character(name="Stickman", variants={"white": "solid white lines"})
    assert c.resolve_variant("white") == "solid white lines"


def test_framing_vocabulary_members() -> None:
    assert "close-up" in FRAMING and "wide" in FRAMING and "establishing" in FRAMING


def test_scene_defaults() -> None:
    s = Scene(id="s1", action="walks")
    assert s.characters == () and s.dialogue == () and s.aspect == "16:9"


def test_dialogue_line() -> None:
    d = DialogueLine(speaker="Stickman", line="hi", voice="warm")
    assert d.speaker == "Stickman" and d.line == "hi" and d.voice == "warm"
