from gflow_cli.composition import Character, DialogueLine, Scene, StyleSpec, compose_prompt


def _chars(*cs: Character) -> dict[str, Character]:
    return {c.name: c for c in cs}


def test_minimal_action_only() -> None:
    out = compose_prompt(StyleSpec(), Scene(id="s", action="walks on the beach"), {})
    assert out == "Walks on the beach."


def test_full_canonical_order_and_precedence() -> None:
    style = StyleSpec(
        look="black-ink line art",
        palette="monochrome",
        environment="negative space",
        camera="eye-level",
        lighting="soft",
        mood="calm",
        negative="no text",
    )
    scene = Scene(
        id="s",
        action="stands on the shore",
        setting="vibrant beach",
        camera="slow push-in",
        framing="wide",
        characters=("Stickman",),
        variant="silhouette",
    )
    chars = _chars(
        Character(
            name="Stickman", appearance="round head", variants={"silhouette": "black silhouette"}
        )
    )
    out = compose_prompt(style, scene, chars)
    assert out == (
        "Stands on the shore. "
        "Round head, black silhouette. "
        "Vibrant beach. "
        "Black-ink line art. "
        "Monochrome. "
        "Soft. "
        "Wide shot, slow push-in. "
        "Calm. "
        "Avoid: no text."
    )


def test_negative_merges_global_and_scene() -> None:
    out = compose_prompt(
        StyleSpec(negative="no text"),
        Scene(id="s", action="x", negative="no blur"),
        {},
    )
    assert out.endswith("Avoid: no text, no blur.")


def test_single_speaker_dialogue() -> None:
    scene = Scene(
        id="s",
        action="smiles",
        characters=("Stickman",),
        dialogue=(DialogueLine(speaker="Stickman", line="We made it!", voice="warm"),),
    )
    out = compose_prompt(StyleSpec(), scene, _chars(Character(name="Stickman")))
    assert 'Stickman (warm) says: "We made it!"' in out


def test_two_speaker_dialogue_block_in_order() -> None:
    scene = Scene(
        id="s",
        action="meet",
        characters=("A", "B"),
        dialogue=(
            DialogueLine(speaker="A", line="Hi"),
            DialogueLine(speaker="B", line="Yo"),
        ),
    )
    out = compose_prompt(StyleSpec(), scene, _chars(Character(name="A"), Character(name="B")))
    assert 'Dialogue:\nA: "Hi"\nB: "Yo"' in out


def test_quotes_in_line_are_escaped() -> None:
    scene = Scene(
        id="s",
        action="x",
        characters=("A",),
        dialogue=(DialogueLine(speaker="A", line='say "hi"'),),
    )
    out = compose_prompt(StyleSpec(), scene, _chars(Character(name="A")))
    assert r"say \"hi\"" in out
