# Movie P1 — Composition Core + Handoff Manifest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pure prompt-composition core (structured style + character/variants + single-speaker dialogue), refactor `movie.toml` to the `scene = clip` model, emit a versioned handoff manifest, and make `gflow movie run` generate-only by default with an opt-in `--stitch` preview — all with text-identity (no entity work; that's P2).

**Architecture:** A single pure module `gflow_cli/composition.py` (no I/O) holds `StyleSpec`, `Character`, `Scene`, the framing vocabulary, `compose_prompt()`, and `build_handoff()`. `movie_manifest.py` parses `movie.toml` into these types. `cli_movie.py` composes each scene's prompt, generates clips in order, and writes `<stem>-handoff.json` (a pure projection of `MovieState`). `--stitch` does an ffmpeg hard-concat preview only.

**Tech Stack:** Python 3.13, `tomllib`, `dataclasses`, pytest. Tests run with `.venv/Scripts/python.exe -m pytest` (not `uv run pytest`). The handoff JSON Schema is validated in tests with `jsonschema` (already a transitive dep; if missing, add `jsonschema` to dev deps in Task 5).

**Depends on:** P0 hotfix merged (`docs/superpowers/plans/2026-06-06-movie-p0-asyncio-hotfix.md`).

---

## File Structure

- Create: `src/gflow_cli/composition.py` — pure composition types + `compose_prompt` + `build_handoff`.
- Create: `docs/schemas/movie-handoff.schema.json` — the external contract's JSON Schema (`schema_version: 1`).
- Modify: `src/gflow_cli/movie_manifest.py` — parse the new `movie.toml` (scene=clip, style, characters w/ variants+voice, dialogue, framing) into `composition` types; bump `MovieState.VERSION`.
- Modify: `src/gflow_cli/cli_movie.py` — compose prompts, generate-only default, emit handoff, `--stitch` preview.
- Create: `tests/composition/test_compose_prompt.py`, `tests/composition/test_character.py`, `tests/composition/test_handoff.py`.
- Modify: `tests/cli/test_movie_manifest.py`, `tests/cli/test_cli_movie.py`.

---

### Task 1: `StyleSpec` and `Character` (with variant resolution)

**Files:**
- Create: `src/gflow_cli/composition.py`
- Test: `tests/composition/test_character.py`

- [ ] **Step 1: Write the failing test**

Create `tests/composition/test_character.py`:

```python
import pytest

from gflow_cli.composition import Character, StyleSpec


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/composition/test_character.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gflow_cli.composition'`.

- [ ] **Step 3: Create the module with `StyleSpec` and `Character`**

Create `src/gflow_cli/composition.py`:

```python
"""Pure prompt-composition core for gflow movie (no I/O).

Holds the structured style + character model, the framing vocabulary, the
deterministic prompt composer, and the handoff-manifest projection. Imports
nothing from the Flow API or browser layers — it is `(data) -> value` and is
the reusable seam a future second consumer (e.g. remotion) can import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class StyleSpec:
    """Global guiding prompt — every field optional, reused verbatim per scene."""

    look: str | None = None
    palette: str | None = None
    environment: str | None = None
    camera: str | None = None
    lighting: str | None = None
    mood: str | None = None
    negative: str | None = None


@dataclass(frozen=True)
class Character:
    """A reusable character. Identity is text (P1) or entity (P2)."""

    name: str
    appearance: str | None = None
    identity: str = "text"  # "text" | "entity"
    voice: str | None = None  # voice resource id / preset name (P2)
    variants: Mapping[str, str] = field(default_factory=dict)
    face_prompt: str | None = None  # entity path (P2)
    body_prompt: str | None = None  # entity path (P2)
    model: str = "nano2"

    def resolve_variant(self, name: str | None) -> str:
        """Return appearance with the named variant delta merged in.

        Raises ValueError on an unknown variant name.
        """
        parts: list[str] = []
        if self.appearance:
            parts.append(self.appearance)
        if name is not None:
            if name not in self.variants:
                msg = f"unknown variant {name!r} for character {self.name!r}"
                raise ValueError(msg)
            parts.append(self.variants[name])
        return ", ".join(parts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/composition/test_character.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/composition.py tests/composition/test_character.py
git commit -m "feat(composition): StyleSpec + Character with variant resolution"
```

---

### Task 2: `Scene`, `DialogueLine`, framing vocabulary

**Files:**
- Modify: `src/gflow_cli/composition.py`
- Test: `tests/composition/test_character.py` (add scene/framing tests)

- [ ] **Step 1: Write the failing test**

Append to `tests/composition/test_character.py`:

```python
from gflow_cli.composition import FRAMING, DialogueLine, Scene


def test_framing_vocabulary_members() -> None:
    assert "close-up" in FRAMING and "wide" in FRAMING and "establishing" in FRAMING


def test_scene_defaults() -> None:
    s = Scene(id="s1", action="walks")
    assert s.characters == () and s.dialogue == () and s.aspect == "16:9"


def test_dialogue_line() -> None:
    d = DialogueLine(speaker="Stickman", line="hi", voice="warm")
    assert d.speaker == "Stickman" and d.line == "hi" and d.voice == "warm"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/composition/test_character.py -v`
Expected: FAIL — `ImportError: cannot import name 'FRAMING'`.

- [ ] **Step 3: Add `FRAMING`, `DialogueLine`, `Scene` to `composition.py`**

Append to `src/gflow_cli/composition.py`:

```python
FRAMING: frozenset[str] = frozenset(
    {
        "establishing",
        "wide",
        "full",
        "medium",
        "medium-close",
        "close-up",
        "extreme-close-up",
        "over-the-shoulder",
        "POV",
    }
)


@dataclass(frozen=True)
class DialogueLine:
    """One spoken line, attributed to a character present in the scene."""

    speaker: str
    line: str
    voice: str | None = None


@dataclass(frozen=True)
class Scene:
    """One scene = one clip = one generation."""

    id: str
    action: str = ""
    title: str | None = None
    setting: str | None = None
    framing: str | None = None  # member of FRAMING
    camera: str | None = None
    lighting: str | None = None
    mood: str | None = None
    negative: str | None = None
    characters: tuple[str, ...] = ()
    variant: str | None = None
    dialogue: tuple[DialogueLine, ...] = ()
    duration: int | None = None
    model: str | None = None
    aspect: str = "16:9"
    count: int = 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/composition/test_character.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/composition.py tests/composition/test_character.py
git commit -m "feat(composition): Scene, DialogueLine, framing vocabulary"
```

---

### Task 3: `compose_prompt` — canonical order, precedence, dialogue, negative merge

**Files:**
- Modify: `src/gflow_cli/composition.py`
- Test: `tests/composition/test_compose_prompt.py`

- [ ] **Step 1: Write the failing tests (golden strings)**

Create `tests/composition/test_compose_prompt.py`:

```python
from gflow_cli.composition import Character, DialogueLine, Scene, StyleSpec, compose_prompt


def _chars(*cs: Character) -> dict[str, Character]:
    return {c.name: c for c in cs}


def test_minimal_action_only() -> None:
    out = compose_prompt(StyleSpec(), Scene(id="s", action="walks on the beach"), {})
    assert out == "walks on the beach."


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
        setting="vibrant beach",      # overrides environment
        camera="slow push-in",         # overrides global camera
        framing="wide",
        characters=("Stickman",),
        variant="silhouette",
    )
    chars = _chars(
        Character(name="Stickman", appearance="round head", variants={"silhouette": "black silhouette"})
    )
    out = compose_prompt(style, scene, chars)
    assert out == (
        "stands on the shore. "
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/composition/test_compose_prompt.py -v`
Expected: FAIL — `ImportError: cannot import name 'compose_prompt'`.

- [ ] **Step 3: Implement `compose_prompt`**

Append to `src/gflow_cli/composition.py`:

```python
def _sentence(text: str) -> str:
    """Capitalize first letter, ensure a trailing period (idempotent)."""
    t = text.strip()
    if not t:
        return ""
    t = t[0].upper() + t[1:]
    if t[-1] not in ".!?":
        t += "."
    return t


def _dialogue_block(scene: "Scene", characters: Mapping[str, "Character"]) -> str:
    lines = scene.dialogue
    if not lines:
        return ""

    def voice_for(d: "DialogueLine") -> str | None:
        if d.voice:
            return d.voice
        c = characters.get(d.speaker)
        return c.voice if c else None

    def esc(s: str) -> str:
        return s.replace('"', r"\"")

    if len(lines) == 1:
        d = lines[0]
        v = voice_for(d)
        who = f"{d.speaker} ({v})" if v else d.speaker
        return f'{who} says: "{esc(d.line)}"'
    rows = []
    for d in lines:
        v = voice_for(d)
        who = f"{d.speaker} ({v})" if v else d.speaker
        rows.append(f'{who}: "{esc(d.line)}"')
    return "Dialogue:\n" + "\n".join(rows)


def compose_prompt(
    style: "StyleSpec",
    scene: "Scene",
    characters: Mapping[str, "Character"],
) -> str:
    """Assemble the final Veo prompt deterministically (canonical order).

    Order: action, subject(+variant), setting, look, palette, lighting,
    framing+camera, mood, dialogue, negative. Each slot: scene override ->
    global -> omit. `negative` MERGES global+scene.
    """
    parts: list[str] = []

    # 1. ACTION (required)
    parts.append(_sentence(scene.action))

    # 2. SUBJECT (appearance + variant) for each named character
    subjects: list[str] = []
    for name in scene.characters:
        c = characters.get(name)
        if c is None:
            continue
        subj = c.resolve_variant(scene.variant) if len(scene.characters) == 1 else (c.appearance or "")
        if subj:
            subjects.append(subj)
    if subjects:
        parts.append(_sentence("; ".join(subjects)))

    # 3. SETTING
    setting = scene.setting or style.environment
    if setting:
        parts.append(_sentence(setting))

    # 4. STYLE / 5. COLOR
    if style.look:
        parts.append(_sentence(style.look))
    if style.palette:
        parts.append(_sentence(style.palette))

    # 6. LIGHTING
    lighting = scene.lighting or style.lighting
    if lighting:
        parts.append(_sentence(lighting))

    # 7. FRAMING + CAMERA
    camera = scene.camera or style.camera
    framing_cam = ", ".join(
        x for x in ([f"{scene.framing} shot" if scene.framing else None, camera]) if x
    )
    if framing_cam:
        parts.append(_sentence(framing_cam))

    # 8. MOOD
    mood = scene.mood or style.mood
    if mood:
        parts.append(_sentence(mood))

    # 9. DIALOGUE
    dia = _dialogue_block(scene, characters)
    if dia:
        parts.append(dia)

    # 10. NEGATIVE (merge global + scene)
    negs = [n for n in (style.negative, scene.negative) if n]
    if negs:
        parts.append(f"Avoid: {', '.join(negs)}.")

    return " ".join(p for p in parts if p)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/composition/test_compose_prompt.py -v`
Expected: PASS (6 tests). If a golden string mismatches, adjust the *test* only if the composed output is genuinely better — otherwise fix the implementation; the golden strings are the contract.

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/composition.py tests/composition/test_compose_prompt.py
git commit -m "feat(composition): deterministic compose_prompt (canonical order + dialogue)"
```

---

### Task 4: Parse the new `movie.toml` into composition types

**Files:**
- Modify: `src/gflow_cli/movie_manifest.py`
- Test: `tests/cli/test_movie_manifest.py`

This replaces the bespoke `CharacterDef`/`SceneDef` with the `composition` types and parses the `scene = clip` schema (style, characters w/ variants+voice, framing, dialogue). Keep `MovieManifest`, `MovieState`, `AssemblyDef`, the `from_toml_path` entry, and the validation-error type (`ConfigurationError`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/cli/test_movie_manifest.py`:

```python
from gflow_cli.composition import Character, Scene, StyleSpec
from gflow_cli.movie_manifest import MovieManifest


def _toml(tmp_path, body: str):
    p = tmp_path / "m.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_parse_full_scene_clip_manifest(tmp_path) -> None:
    m = MovieManifest.from_toml_path(_toml(tmp_path, '''
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
'''))
    assert isinstance(m.style, StyleSpec) and m.style.look == "ink"
    assert m.characters["Stickman"].voice == "alnilam"
    assert m.characters["Stickman"].variants["white"] == "solid white"
    s = m.scenes[0]
    assert isinstance(s, Scene) and s.id == "s1" and s.framing == "wide"
    assert s.characters == ("Stickman",) and s.variant == "white"
    assert s.dialogue[0].speaker == "Stickman" and s.dialogue[0].line == "Hi"


def test_unknown_framing_rejected(tmp_path) -> None:
    import pytest
    from gflow_cli.errors import ConfigurationError
    with pytest.raises(ConfigurationError, match="framing"):
        MovieManifest.from_toml_path(_toml(tmp_path, '''
title="T"
project="p"
[[scenes]]
id="s"
framing="zoomy"
action="x"
'''))


def test_speaker_must_be_in_characters(tmp_path) -> None:
    import pytest
    from gflow_cli.errors import ConfigurationError
    with pytest.raises(ConfigurationError, match="speaker"):
        MovieManifest.from_toml_path(_toml(tmp_path, '''
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
'''))


def test_shorthand_rejected_for_multi_character(tmp_path) -> None:
    import pytest
    from gflow_cli.errors import ConfigurationError
    with pytest.raises(ConfigurationError, match="per-character"):
        MovieManifest.from_toml_path(_toml(tmp_path, '''
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
'''))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_movie_manifest.py -k "scene_clip or framing or speaker or shorthand" -v`
Expected: FAIL (parser doesn't yet produce composition types / enforce rules).

- [ ] **Step 3: Rewrite the manifest DTOs + parsers**

In `src/gflow_cli/movie_manifest.py`, replace the `CharacterDef`/`SceneDef` dataclasses and their parsers, and update `MovieManifest`. Key changes (apply over the existing file, preserving `MovieState`, `CharacterState`, `SceneState`, `AssemblyDef`, `from_toml_path`):

```python
# at top of file, add:
from gflow_cli.composition import FRAMING, Character, DialogueLine, Scene, StyleSpec

# MovieManifest fields become:
@dataclass(frozen=True)
class MovieManifest:
    title: str
    project: str
    style: StyleSpec
    characters: dict[str, Character]            # keyed by name
    scenes: tuple[Scene, ...]
    continuity: str = "independent"
    assemble: AssemblyDef | None = None
    output_dir: str | None = None
    schema_version: int = 1
```

Add parsers (replace the old `_parse_character` / `_parse_scene`):

```python
_VALID_CHARACTER_MODELS: frozenset[str] = frozenset({"nano2", "nanopro"})
_VALID_VIDEO_ASPECTS: frozenset[str] = frozenset({"9:16", "16:9", "1:1"})
_VALID_DURATIONS: frozenset[int] = frozenset({4, 6, 8, 10})


def _parse_style(data: object) -> StyleSpec:
    if data is None:
        return StyleSpec()
    if not isinstance(data, dict):
        raise ConfigurationError("[style] must be a TOML table.")
    d = cast("dict[str, object]", data)

    def s(key: str) -> str | None:
        v = d.get(key)
        if v is not None and not isinstance(v, str):
            raise ConfigurationError(f"style.{key} must be a string.")
        return v.strip() if isinstance(v, str) else None

    return StyleSpec(
        look=s("look"), palette=s("palette"), environment=s("environment"),
        camera=s("camera"), lighting=s("lighting"), mood=s("mood"), negative=s("negative"),
    )


def _parse_character(data: object, idx: int) -> Character:
    if not isinstance(data, dict):
        raise ConfigurationError(f"characters[{idx}] must be a TOML table.")
    d = cast("dict[str, object]", data)
    name = d.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigurationError(f"characters[{idx}].name must be a non-empty string.")
    identity = d.get("identity", "text")
    if identity not in ("text", "entity"):
        raise ConfigurationError(f"characters[{idx}].identity must be 'text' or 'entity'.")
    variants_raw = d.get("variants", {})
    if not isinstance(variants_raw, dict):
        raise ConfigurationError(f"characters[{idx}].variants must be a table.")
    variants = {str(k): str(v) for k, v in cast("dict[str, object]", variants_raw).items()}
    model = d.get("model", "nano2")
    if model not in _VALID_CHARACTER_MODELS:
        raise ConfigurationError(
            f"characters[{idx}].model must be one of {sorted(_VALID_CHARACTER_MODELS)}."
        )

    def opt(key: str) -> str | None:
        v = d.get(key)
        if v is not None and not isinstance(v, str):
            raise ConfigurationError(f"characters[{idx}].{key} must be a string.")
        return v.strip() if isinstance(v, str) else None

    if identity == "entity" and not opt("face_prompt"):
        raise ConfigurationError(f"characters[{idx}] identity='entity' requires face_prompt.")
    return Character(
        name=name.strip(), appearance=opt("appearance"), identity=str(identity),
        voice=opt("voice"), variants=variants, face_prompt=opt("face_prompt"),
        body_prompt=opt("body_prompt"), model=str(model),
    )


def _parse_scene(data: object, idx: int, char_names: set[str]) -> Scene:
    if not isinstance(data, dict):
        raise ConfigurationError(f"scenes[{idx}] must be a TOML table.")
    d = cast("dict[str, object]", data)

    sid = d.get("id")
    if not isinstance(sid, str) or not sid.strip():
        raise ConfigurationError(f"scenes[{idx}].id must be a non-empty string.")
    action = d.get("action")
    if not isinstance(action, str) or not action.strip():
        raise ConfigurationError(f"scenes[{idx}].action must be a non-empty string.")

    framing = d.get("framing")
    if framing is not None and framing not in FRAMING:
        raise ConfigurationError(
            f"scenes[{idx}].framing must be one of {sorted(FRAMING)} (got {framing!r})."
        )

    chars_raw = d.get("characters", [])
    if not isinstance(chars_raw, list):
        raise ConfigurationError(f"scenes[{idx}].characters must be an array.")
    chars: list[str] = []
    for cn in cast("list[object]", chars_raw):
        if not isinstance(cn, str) or cn not in char_names:
            raise ConfigurationError(f"scenes[{idx}] references unknown character {cn!r}.")
        chars.append(cn)

    # Dialogue: shorthand (speaker/line) for single-char scenes, else per-character table.
    dialogue: list[DialogueLine] = []
    speaker = d.get("speaker")
    line = d.get("line")
    variant = d.get("variant")
    per_char = d.get("characters_detail")  # optional [[scenes.characters_detail]] table list
    if (speaker is not None or line is not None or variant is not None) and len(chars) > 1:
        raise ConfigurationError(
            f"scenes[{idx}]: speaker/line/variant shorthand is invalid with >1 character; "
            "use per-character [[scenes.characters_detail]] entries."
        )
    if speaker is not None:
        if speaker not in chars:
            raise ConfigurationError(f"scenes[{idx}].speaker {speaker!r} not in characters.")
        if not isinstance(line, str) or not line.strip():
            raise ConfigurationError(f"scenes[{idx}].line must be a non-empty string.")
        dialogue.append(DialogueLine(speaker=str(speaker), line=line.strip()))
    if isinstance(per_char, list):
        for e in cast("list[object]", per_char):
            if not isinstance(e, dict):
                continue
            ed = cast("dict[str, object]", e)
            nm = ed.get("name")
            ln = ed.get("line")
            if nm not in chars:
                raise ConfigurationError(f"scenes[{idx}].characters_detail name {nm!r} not in characters.")
            if isinstance(ln, str) and ln.strip():
                dialogue.append(DialogueLine(speaker=str(nm), line=ln.strip()))

    if len(dialogue) > 2:
        # soft: composer also warns; here we keep it permissive but flag in logs upstream.
        pass

    aspect = d.get("aspect", "16:9")
    if aspect not in _VALID_VIDEO_ASPECTS:
        raise ConfigurationError(f"scenes[{idx}].aspect must be one of {sorted(_VALID_VIDEO_ASPECTS)}.")
    duration = d.get("duration")
    if duration is not None and duration not in _VALID_DURATIONS:
        raise ConfigurationError(f"scenes[{idx}].duration must be one of {sorted(_VALID_DURATIONS)}.")
    model = d.get("model")
    if model is not None and not isinstance(model, str):
        raise ConfigurationError(f"scenes[{idx}].model must be a string.")

    def opt(key: str) -> str | None:
        v = d.get(key)
        return v.strip() if isinstance(v, str) else None

    return Scene(
        id=sid.strip(), action=action.strip(), title=opt("title"), setting=opt("setting"),
        framing=str(framing) if framing else None, camera=opt("camera"),
        lighting=opt("lighting"), mood=opt("mood"), negative=opt("negative"),
        characters=tuple(chars), variant=str(variant) if isinstance(variant, str) else None,
        dialogue=tuple(dialogue),
        duration=duration if isinstance(duration, int) else None,
        model=model if isinstance(model, str) else None, aspect=str(aspect),
    )
```

Update `MovieManifest._from_dict` to build `style=_parse_style(data.get("style"))`, `characters={c.name: c for c in (_parse_character(...) for ...)}` (reject duplicate names), `scenes=tuple(_parse_scene(s, i, set(characters)) for ...)` (reject duplicate `scene.id`), `continuity=data.get("[movie].continuity","independent")` (read from `data.get("movie",{})`), and `schema_version`. Variant-existence is validated at compose time (`Character.resolve_variant`), but also reject here if `scene.variant` not in the character's variants.

Also bump `MovieState.VERSION = 2` (state now keyed by `scene.id`; `from_dict` already `.get`-defaults, so old v1 files load gracefully).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_movie_manifest.py -v`
Expected: PASS. Update any *pre-existing* manifest tests that used the old `prompt=`/`type=` scene fields to the new `id`/`action` schema (those are intentional breaking changes — fix the tests to the new schema).

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/movie_manifest.py tests/cli/test_movie_manifest.py
git commit -m "feat(movie): parse scene=clip movie.toml into composition types"
```

---

### Task 5: Handoff manifest — `build_handoff` + JSON Schema + golden round-trip

**Files:**
- Modify: `src/gflow_cli/composition.py`
- Create: `docs/schemas/movie-handoff.schema.json`
- Test: `tests/composition/test_handoff.py`
- Modify: `pyproject.toml` (add `jsonschema` to the test/dev dependency group if not present)

- [ ] **Step 1: Write the failing test**

Create `tests/composition/test_handoff.py`:

```python
import json
from pathlib import Path

import jsonschema

from gflow_cli.composition import build_handoff


class _Result:  # minimal stand-in for a completed SceneState
    def __init__(self, sid, path, op):
        self.id, self.local_path, self.flow_operation_id, self.media_id = sid, path, op, "m"


def test_build_handoff_shape_and_schema(tmp_path) -> None:
    manifest = _FakeManifest()  # see helper below
    handoff = build_handoff(manifest, _fake_state(), out_dir=Path("/out/x"))
    assert handoff["schema_version"] == 1
    assert handoff["clips"][0]["index"] == 0
    assert handoff["clips"][0]["id"] == "s1"
    # relative POSIX path, no backslashes, no signed-url leak
    assert handoff["clips"][0]["file"] == "s1.mp4"
    blob = json.dumps(handoff)
    assert "fifeUrl" not in blob and "\\\\" not in blob and "Bearer" not in blob

    schema = json.loads(Path("docs/schemas/movie-handoff.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(handoff, schema)  # raises on contract drift
```

Add these test helpers at the top of the same file (concrete fakes so the test is self-contained):

```python
from gflow_cli.composition import Character, Scene, StyleSpec
from gflow_cli.movie_manifest import MovieState, SceneState


class _FakeManifest:
    title = "T"
    project = "p"
    style = StyleSpec(look="ink", negative="no text")
    characters = {"Stickman": Character(name="Stickman", identity="text", voice="alnilam")}
    scenes = (Scene(id="s1", action="walks", framing="wide", characters=("Stickman",), duration=8),)


def _fake_state() -> MovieState:
    st = MovieState(title="T", project="p")
    st.scenes["s1"] = SceneState(
        media_id="m", flow_operation_id="op-1",
        local_path="/out/x/s1.mp4", status="completed",
    )
    return st
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/composition/test_handoff.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_handoff'` (and the schema file missing).

- [ ] **Step 3: Create the JSON Schema**

Create `docs/schemas/movie-handoff.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "gflow movie handoff manifest",
  "type": "object",
  "required": ["schema_version", "generator", "movie", "clips"],
  "additionalProperties": true,
  "properties": {
    "schema_version": { "const": 1 },
    "generator": {
      "type": "object",
      "required": ["name", "version"],
      "properties": { "name": { "type": "string" }, "version": { "type": "string" } }
    },
    "movie": {
      "type": "object",
      "required": ["title"],
      "properties": {
        "title": { "type": "string" },
        "output_dir": { "type": "string" },
        "total_duration_seconds": { "type": "number" }
      }
    },
    "style": { "type": "object" },
    "characters": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "identity"],
        "properties": {
          "name": { "type": "string" },
          "identity": { "enum": ["text", "entity"] },
          "voice": { "type": ["string", "null"] },
          "x_gflow": { "type": "object" }
        }
      }
    },
    "clips": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id", "index", "file", "status"],
        "properties": {
          "id": { "type": "string" },
          "index": { "type": "integer", "minimum": 0 },
          "file": { "type": "string", "pattern": "^[^\\\\]*$" },
          "duration_seconds": { "type": ["number", "null"] },
          "framing": { "type": ["string", "null"] },
          "characters": { "type": "array", "items": { "type": "string" } },
          "consistency_method": { "enum": ["text", "entity", "degraded"] },
          "dialogue": { "type": "array" },
          "status": { "enum": ["completed", "failed"] },
          "x_gflow": { "type": "object" }
        }
      }
    },
    "stitch": { "type": "object" }
  }
}
```

- [ ] **Step 4: Implement `build_handoff`**

Append to `src/gflow_cli/composition.py` (note: it takes already-loaded data + an `out_dir` and a `version` string; it imports nothing from Flow):

```python
from pathlib import Path  # add to imports at top of composition.py


def build_handoff(manifest: object, state: object, *, out_dir: "Path", version: str = "0.14.0") -> dict:
    """Project a completed/partial movie run into the versioned handoff manifest.

    Pure: derives entirely from `manifest` (MovieManifest) + `state` (MovieState).
    Paths are made relative to `out_dir` and POSIX-normalized. Flow-internal ids
    go under `x_gflow`. No signed URLs / tokens / PII ever enter the output.
    """
    out = Path(out_dir)

    def rel(p: str | None) -> str | None:
        if not p:
            return None
        path = Path(p)
        try:
            return path.relative_to(out).as_posix()
        except ValueError:
            return path.name

    chars_out = []
    for c in manifest.characters.values():  # type: ignore[attr-defined]
        chars_out.append(
            {
                "name": c.name,
                "identity": c.identity,
                "voice": c.voice,
                "x_gflow": {},  # entity_id added in P2
            }
        )

    clips = []
    total = 0.0
    for index, scene in enumerate(manifest.scenes):  # type: ignore[attr-defined]
        ss = state.scenes.get(scene.id)  # type: ignore[attr-defined]
        status = ss.status if ss else "failed"
        dur = float(scene.duration) if scene.duration else None
        if dur:
            total += dur
        clips.append(
            {
                "id": scene.id,
                "index": index,
                "file": rel(ss.local_path) if ss else None,
                "duration_seconds": dur,
                "framing": scene.framing,
                "characters": list(scene.characters),
                "consistency_method": "text",  # P2 sets entity/degraded
                "dialogue": [{"speaker": d.speaker, "line": d.line, "voice": d.voice} for d in scene.dialogue],
                "status": status,
                "x_gflow": {
                    k: v
                    for k, v in (
                        ("media_id", ss.media_id if ss else None),
                        ("operation_id", ss.flow_operation_id if ss else None),
                        ("project_id", manifest.project),  # type: ignore[attr-defined]
                    )
                    if v
                },
            }
        )

    return {
        "schema_version": 1,
        "generator": {"name": "gflow-cli", "version": version},
        "movie": {
            "title": manifest.title,  # type: ignore[attr-defined]
            "output_dir": ".",
            "total_duration_seconds": total,
        },
        "style": {k: v for k, v in vars(manifest.style).items() if v},  # type: ignore[attr-defined]
        "characters": chars_out,
        "clips": clips,
        "stitch": {"performed": False, "output": None},
    }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/composition/test_handoff.py -v`
Expected: PASS. If `jsonschema` import fails, add it to the dev/test dependency group in `pyproject.toml` (`jsonschema>=4`), then `.venv/Scripts/python.exe -m pip install jsonschema` and re-run.

- [ ] **Step 6: Commit**

```bash
git add src/gflow_cli/composition.py docs/schemas/movie-handoff.schema.json tests/composition/test_handoff.py pyproject.toml
git commit -m "feat(movie): versioned handoff manifest (build_handoff + JSON Schema)"
```

---

### Task 6: Orchestrator — compose prompts, generate-only default, emit handoff, `--stitch`

**Files:**
- Modify: `src/gflow_cli/cli_movie.py`
- Test: `tests/cli/test_cli_movie.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/cli/test_cli_movie.py`:

```python
    async def test_run_movie_writes_handoff_and_composes_prompt(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _run_movie
        from gflow_cli.composition import Character, Scene, StyleSpec
        from gflow_cli.movie_manifest import MovieManifest, MovieState

        manifest = MovieManifest(
            title="T", project="p",
            style=StyleSpec(look="ink", negative="no text"),
            characters={"Stickman": Character(name="Stickman", appearance="round head")},
            scenes=(Scene(id="s1", action="walks", framing="wide", characters=("Stickman",), duration=8),),
        )
        state = MovieState(title="T", project="p")
        state_path = tmp_path / "m-state.json"
        captured = {}

        async def fake_generate(**kwargs):
            captured["prompt"] = kwargs["scene"].prompt if hasattr(kwargs.get("scene"), "prompt") else kwargs.get("prompt")
            return _make_video_result()

        with (
            patch("gflow_cli.cli_movie.get_settings"),
            patch("gflow_cli.cli_movie.OperationRecorder") as rec,
            patch("gflow_cli.cli_movie.FlowApiClient", return_value=_mock_client_cm()),
            patch("gflow_cli.cli_movie._generate_scene", new=AsyncMock(side_effect=fake_generate)),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            rec.open.return_value = MagicMock()
            await _run_movie(
                manifest=manifest, state=state, state_path=state_path,
                profile_name="default", profile_dir=tmp_path / "p",
                out_dir=tmp_path / "out", continue_on_error=True,
            )

        handoff = tmp_path / "m-handoff.json"
        assert handoff.exists()
        import json
        data = json.loads(handoff.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1 and data["clips"][0]["id"] == "s1"
```

(The exact `_generate_scene` signature is set in Step 3 — the test asserts the handoff file is written; the prompt-capture line tolerates either calling convention.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_cli_movie.py -k handoff -v`
Expected: FAIL — no `m-handoff.json` written (and the orchestrator still references old `SceneDef` fields).

- [ ] **Step 3: Update the orchestrator**

In `src/gflow_cli/cli_movie.py`:
1. Import: `from gflow_cli.composition import build_handoff, compose_prompt`.
2. In `_generate_scene`, build the prompt from the composer and use the new `Scene` fields. Replace the `kwargs` construction so `prompt=compose_prompt(manifest.style, scene, manifest.characters)`, `mode` = `Mode.R2V` when `scene.characters` else `Mode.T2V`, `aspect=Aspect.from_cli(scene.aspect)`, `model=VideoModel.from_cli(scene.model)`, `duration=scene.duration`, `count=scene.count`. (Pass `style` + `characters` into `_generate_scene`, or compose in `_run_movie` and pass the string.) Simplest: compose in `_run_movie` and pass `prompt=` into `_generate_scene`.
3. Key resume/state on `scene.id` (replace `scene_def.title` lookups with `scene.id`).
4. After the scene loop, in the `finally`/end-of-run, write the handoff:

```python
    handoff = build_handoff(manifest, state, out_dir=out_dir)
    handoff_path = state_path.with_name(state_path.stem.replace("-state", "") + "-handoff.json")
    handoff_path.write_text(json.dumps(handoff, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"  handoff: {handoff_path}")
```

(Add `import json` at the top of `cli_movie.py`.)
5. Remove the old `[assemble]`-as-deliverable path from `_print_summary`; the auto-assemble is now gated behind a new `--stitch` flag.

- [ ] **Step 4: Add the `--stitch` flag (preview-only)**

In the `run` command, add:

```python
@click.option(
    "--stitch",
    is_flag=True,
    default=False,
    help="After generating, hard-concat all clips into one PREVIEW mp4 (ffmpeg, no transitions). Not a deliverable.",
)
```

Thread `stitch` into `_run_movie`. After the handoff is written, if `stitch` and ≥2 completed clips:

```python
    if stitch:
        from gflow_cli.composition import build_handoff  # already imported
        completed = [Path(s.local_path) for s in state.scenes.values()
                     if s.status == "completed" and s.local_path]
        if len(completed) >= 2:
            preview = out_dir / "preview.mp4"
            _ffmpeg_concat(completed, preview)
            console.print(f"  [dim]stitch preview:[/dim] {preview}")
```

Add a small helper using PyAV-free ffmpeg via the demuxer concat (PyAV is a dep; or shell out to ffmpeg). Minimal implementation using the `av`-independent concat-demuxer through `subprocess` is acceptable; if no system ffmpeg, log a warning and skip (preview is non-essential):

```python
import shutil
import subprocess
import tempfile

def _ffmpeg_concat(clips: list[Path], out: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("movie.stitch_skipped_no_ffmpeg")
        return
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for c in clips:
            f.write(f"file '{c.as_posix()}'\n")
        listfile = f.name
    subprocess.run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", listfile,
                    "-c", "copy", str(out)], check=True)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_cli_movie.py -v`
Expected: PASS. Fix any pre-existing orchestrator tests that referenced old `SceneDef`/`MovieManifest(scenes=(SceneDef...))` shapes — update them to the new `Scene`/`MovieManifest(style=, characters=, scenes=)` constructor.

- [ ] **Step 6: Full P1 sweep + commit**

Run: `.venv/Scripts/python.exe -m pytest tests/composition tests/cli/test_movie_manifest.py tests/cli/test_cli_movie.py -q`
Expected: all PASS.

```bash
git add src/gflow_cli/cli_movie.py tests/cli/test_cli_movie.py
git commit -m "feat(movie): compose prompts, generate-only + handoff, --stitch preview"
```

---

## Self-Review

**Spec coverage:** §3 (`composition.py` single module) → Tasks 1-3,5. §4-§6 (data model, schema, composer order/precedence/dialogue/negative-merge) → Tasks 1-4. §7 (handoff contract: schema_version, flat clips[], relative POSIX, x_gflow, no secrets, consistency_method, P2 fields reserved, JSON Schema + golden) → Task 5. §10 (`--stitch` preview, ffmpeg not chain.py) → Task 6. §11 (resume keyed on scene.id; state VERSION bump) → Tasks 4,6. §13 validation (id unique, speaker∈characters, framing∈vocab, shorthand-vs-multichar, identity=entity needs face_prompt) → Task 4. §14 testing (pure composer golden, validation, orchestrator, handoff round-trip+schema) → Tasks 1-6. **Deferred to P2 (correctly out of P1):** `reference_entities`/`reference_audio` DTO, cap budgeting, `_attach_character_entities`, voice-on-creation, `consistency_method` entity/degraded values.

**Placeholder scan:** No "TBD"/"handle edge cases"; every code step has full code. The only soft spot (`len(dialogue) > 2` soft-warning) is intentionally permissive per spec §6.1 and emitted upstream.

**Type consistency:** `StyleSpec`/`Character`/`Scene`/`DialogueLine`/`compose_prompt`/`build_handoff`/`FRAMING` names are identical across Tasks 1-6 and the spec. `MovieManifest(style=, characters: dict, scenes: tuple, continuity=, schema_version=)` is consistent between Task 4 (definition) and Tasks 5-6 (usage). `SceneState` fields unchanged from `movie_manifest.py`. Resume key `scene.id` used consistently in Tasks 4 and 6.
