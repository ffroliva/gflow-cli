"""Movie manifest — parse and validate a movie.toml project file.

A movie.toml describes a self-contained film production: a set of named
characters (with face / body reference prompts) and an ordered list of
scenes (each specifying a generation type, prompt, and optional character
references).  The runner in :mod:`gflow_cli.cli_movie` consumes this
manifest and orchestrates ``gflow character`` + ``gflow video`` operations
automatically.

Run state is written to a sibling ``<stem>-state.json`` file so that a
crashed or interrupted run can resume without re-spending credits.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from gflow_cli.composition import FRAMING, Character, DialogueLine, Scene, StyleSpec
from gflow_cli.errors import ConfigurationError

__all__ = [
    "AssemblyDef",
    "CharacterState",
    "MovieManifest",
    "MovieState",
    "SceneState",
]

# Type aliases for TOML-shaped ``cast(...)`` targets. Extracted so the quoted
# cast strings are not duplicated (SonarCloud S1192); module-level aliases keep
# ruff's TC006 happy since call sites pass a bare name, not a subscript.
_TomlObj = dict[str, object]
_TomlList = list[object]

# ---------------------------------------------------------------------------
# Allowed values
# ---------------------------------------------------------------------------

_VALID_VIDEO_ASPECTS: frozenset[str] = frozenset({"9:16", "16:9", "1:1"})
_VALID_DURATIONS: frozenset[int] = frozenset({4, 6, 8, 10})
_VALID_CHARACTER_MODELS: frozenset[str] = frozenset({"nano2", "nanopro"})


# ---------------------------------------------------------------------------
# Manifest DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssemblyDef:
    """Optional assembly step — render all scenes into one .mp4."""

    output: str | None = None


@dataclass(frozen=True)
class MovieManifest:
    """Validated, immutable representation of a movie.toml file (scene = clip)."""

    title: str
    project: str
    style: StyleSpec
    characters: dict[str, Character]  # keyed by name
    scenes: tuple[Scene, ...]
    continuity: str = "independent"
    assemble: AssemblyDef | None = None
    output_dir: str | None = None
    schema_version: int = 1

    @classmethod
    def from_toml_path(cls, path: Path) -> MovieManifest:
        """Parse and validate *path*; raise :class:`ConfigurationError` on any problem."""
        if not path.exists():
            raise ConfigurationError(f"Manifest not found: {path}")
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigurationError(f"Cannot read {path}: {exc}") from exc
        try:
            _raw = tomllib.loads(raw)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigurationError(f"Failed to parse {path}: {exc}") from exc
        return cls._from_dict(cast(_TomlObj, _raw))

    @classmethod
    def _from_dict(cls, data: dict[str, object]) -> MovieManifest:
        title = _require_nonempty_str(data, "title")
        project = _require_nonempty_str(data, "project")
        output_dir = _optional_str(data, "output_dir")
        schema_version = _require_int(data, "schema_version", default=1)
        style = _parse_style(data.get("style"))
        characters = _parse_characters(data)
        scenes = _parse_scenes(data, characters, style)
        continuity = _parse_continuity(data)
        assemble = _parse_assemble(data)

        return cls(
            title=title,
            project=project,
            style=style,
            characters=characters,
            scenes=scenes,
            continuity=continuity,
            assemble=assemble,
            output_dir=output_dir,
            schema_version=schema_version,
        )


# ---------------------------------------------------------------------------
# Internal parsers
# ---------------------------------------------------------------------------


def _require_nonempty_str(data: dict[str, object], key: str) -> str:
    """Extract a required non-empty string from data."""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"'{key}' must be a non-empty string.")
    return value.strip()


def _optional_str(data: dict[str, object], key: str) -> str | None:
    """Extract an optional string from data."""
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise ConfigurationError(f"'{key}' must be a string.")
    return value


def _require_int(data: dict[str, object], key: str, default: int = 0) -> int:
    """Extract a required integer from data."""
    value = data.get(key, default)
    if not isinstance(value, int):
        raise ConfigurationError(f"'{key}' must be an integer.")
    return value


def _parse_characters(data: dict[str, object]) -> dict[str, Character]:
    """Parse characters from data."""
    chars_raw = data.get("characters", [])
    if not isinstance(chars_raw, list):
        raise ConfigurationError("'characters' must be a TOML array.")
    chars_list = cast(_TomlList, chars_raw)
    characters: dict[str, Character] = {}
    for i, c in enumerate(chars_list):
        parsed = _parse_character(c, i)
        if parsed.name in characters:
            raise ConfigurationError(f"Duplicate character name: {parsed.name!r}")
        characters[parsed.name] = parsed
    return characters


def _parse_scenes(
    data: dict[str, object],
    characters: dict[str, Character],
    style: StyleSpec,
) -> tuple[Scene, ...]:
    """Parse scenes from data."""
    scenes_raw = data.get("scenes", [])
    if not isinstance(scenes_raw, list):
        raise ConfigurationError("'scenes' must be a TOML array.")
    if not scenes_raw:
        raise ConfigurationError("At least one [[scenes]] entry is required.")
    scenes_list = cast(_TomlList, scenes_raw)
    char_names = set(characters)
    style_variant_names = set(style.variants)
    scenes = tuple(
        _parse_scene(s, i, char_names, characters, style_variant_names)
        for i, s in enumerate(scenes_list)
    )
    scene_ids: set[str] = set()
    for s in scenes:
        if s.id in scene_ids:
            raise ConfigurationError(f"Duplicate scene id: {s.id!r}")
        scene_ids.add(s.id)
    return scenes


def _parse_continuity(data: dict[str, object]) -> str:
    """Parse continuity setting from data."""
    continuity = "independent"
    movie_raw = data.get("movie")
    if isinstance(movie_raw, dict):
        raw_cont = cast(_TomlObj, movie_raw).get("continuity", "independent")
        if not isinstance(raw_cont, str):
            raise ConfigurationError("movie.continuity must be a string.")
        continuity = raw_cont
    return continuity


def _parse_assemble(data: dict[str, object]) -> AssemblyDef | None:
    """Parse assemble setting from data."""
    assemble_raw = data.get("assemble")
    if assemble_raw is None:
        return None
    if not isinstance(assemble_raw, dict):
        raise ConfigurationError("[assemble] must be a TOML table.")
    assemble_dict = cast(_TomlObj, assemble_raw)
    output = assemble_dict.get("output")
    if output is not None and not isinstance(output, str):
        raise ConfigurationError("assemble.output must be a string path.")
    return AssemblyDef(output=output)


def _parse_style(data: object) -> StyleSpec:
    if data is None:
        return StyleSpec()
    if not isinstance(data, dict):
        raise ConfigurationError("[style] must be a TOML table.")
    d = cast(_TomlObj, data)

    def s(key: str) -> str | None:
        v = d.get(key)
        if v is not None and not isinstance(v, str):
            raise ConfigurationError(f"style.{key} must be a string.")
        return v.strip() if isinstance(v, str) else None

    variants = _parse_style_variants(d)

    return StyleSpec(
        look=s("look"),
        palette=s("palette"),
        environment=s("environment"),
        camera=s("camera"),
        lighting=s("lighting"),
        mood=s("mood"),
        negative=s("negative"),
        prefix=s("prefix"),
        suffix=s("suffix"),
        variants=variants,
    )


def _parse_style_variants(d: _TomlObj) -> dict[str, str]:
    """Parse [style.variants.*] sub-tables into a name → suffix mapping."""
    variants_raw = d.get("variants")
    if variants_raw is None:
        return {}
    if not isinstance(variants_raw, dict):
        raise ConfigurationError("[style.variants] must be a TOML table.")
    variants: dict[str, str] = {}
    for name, val in cast(_TomlObj, variants_raw).items():
        if not isinstance(val, dict):
            raise ConfigurationError(f"[style.variants.{name}] must be a TOML table.")
        variant_d = cast(_TomlObj, val)
        suffix_val = variant_d.get("suffix")
        if suffix_val is not None and not isinstance(suffix_val, str):
            raise ConfigurationError(f"style.variants.{name}.suffix must be a string.")
        if isinstance(suffix_val, str):
            variants[str(name)] = suffix_val.strip()
    return variants


def _parse_character_variants(d: _TomlObj, idx: int) -> dict[str, str]:
    """Parse the optional variants table for a character entry."""
    variants_raw = d.get("variants", {})
    if not isinstance(variants_raw, dict):
        raise ConfigurationError(f"characters[{idx}].variants must be a table.")
    return {str(k): str(v) for k, v in cast(_TomlObj, variants_raw).items()}


def _character_opt_str(d: _TomlObj, key: str, idx: int) -> str | None:
    """Read an optional string field from a character dict; raise on wrong type."""
    v = d.get(key)
    if v is not None and not isinstance(v, str):
        raise ConfigurationError(f"characters[{idx}].{key} must be a string.")
    return v.strip() if isinstance(v, str) else None


def _parse_character(data: object, idx: int) -> Character:
    if not isinstance(data, dict):
        raise ConfigurationError(f"characters[{idx}] must be a TOML table.")
    d = cast(_TomlObj, data)

    name = d.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigurationError(f"characters[{idx}].name must be a non-empty string.")

    identity = d.get("identity", "text")
    if identity not in ("text", "entity"):
        raise ConfigurationError(f"characters[{idx}].identity must be 'text' or 'entity'.")

    variants = _parse_character_variants(d, idx)

    model = d.get("model", "nano2")
    if not isinstance(model, str) or model not in _VALID_CHARACTER_MODELS:
        raise ConfigurationError(
            f"characters[{idx}].model must be one of "
            f"{sorted(_VALID_CHARACTER_MODELS)} (got {model!r})."
        )

    face_prompt = _character_opt_str(d, "face_prompt", idx)
    if identity == "entity" and not face_prompt:
        raise ConfigurationError(f"characters[{idx}] identity='entity' requires face_prompt.")

    return Character(
        name=name.strip(),
        appearance=_character_opt_str(d, "appearance", idx),
        identity=str(identity),
        voice=_character_opt_str(d, "voice", idx),
        variants=variants,
        face_prompt=face_prompt,
        body_prompt=_character_opt_str(d, "body_prompt", idx),
        model=str(model),
    )


def _parse_scene_chars(d: _TomlObj, idx: int, char_names: set[str]) -> list[str]:
    """Parse and validate the characters array for a scene."""
    chars_raw = d.get("characters", [])
    if not isinstance(chars_raw, list):
        raise ConfigurationError(f"scenes[{idx}].characters must be an array.")
    chars: list[str] = []
    for cn in cast(_TomlList, chars_raw):
        if not isinstance(cn, str) or cn not in char_names:
            raise ConfigurationError(f"scenes[{idx}] references unknown character {cn!r}.")
        chars.append(cn)
    return chars


def _parse_scene_shorthand_dialogue(
    d: _TomlObj,
    idx: int,
    chars: list[str],
) -> tuple[list[DialogueLine], object, object]:
    """Parse shorthand speaker/line/variant fields; return (dialogue, speaker, variant).

    Also validates that shorthand fields are not used when >1 character is present.
    """
    dialogue: list[DialogueLine] = []
    speaker = d.get("speaker")
    line = d.get("line")
    variant = d.get("variant")

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

    return dialogue, speaker, variant


def _parse_scene_per_char_dialogue(
    d: _TomlObj,
    idx: int,
    chars: list[str],
) -> list[DialogueLine]:
    """Parse the optional [[scenes.characters_detail]] per-character dialogue table."""
    dialogue: list[DialogueLine] = []
    per_char = d.get("characters_detail")
    if not isinstance(per_char, list):
        return dialogue
    for e in cast(_TomlList, per_char):
        if not isinstance(e, dict):
            continue
        ed = cast(_TomlObj, e)
        nm = ed.get("name")
        ln = ed.get("line")
        if nm not in chars:
            raise ConfigurationError(
                f"scenes[{idx}].characters_detail name {nm!r} not in characters."
            )
        if isinstance(ln, str) and ln.strip():
            dialogue.append(DialogueLine(speaker=str(nm), line=ln.strip()))
    return dialogue


def _validate_scene_variant(
    variant: object,
    chars: list[str],
    characters: dict[str, Character],
    idx: int,
) -> None:
    """Validate that the shorthand variant name exists on the (single) character."""
    if not isinstance(variant, str) or len(chars) != 1:
        return
    ch = characters.get(chars[0])
    if ch is not None and variant not in ch.variants:
        raise ConfigurationError(
            f"scenes[{idx}].variant {variant!r} is not a variant of "
            f"character {chars[0]!r} (defined: {sorted(ch.variants)!r})."
        )


def _parse_scene_numeric_fields(d: _TomlObj, idx: int) -> tuple[str, object]:
    """Parse and validate aspect and duration; return (aspect_str, duration_raw)."""
    aspect = d.get("aspect", "16:9")
    if not isinstance(aspect, str) or aspect not in _VALID_VIDEO_ASPECTS:
        raise ConfigurationError(
            f"scenes[{idx}].aspect must be one of {sorted(_VALID_VIDEO_ASPECTS)}."
        )

    duration = d.get("duration")
    if duration is not None and duration not in _VALID_DURATIONS:
        raise ConfigurationError(
            f"scenes[{idx}].duration must be one of {sorted(_VALID_DURATIONS)}."
        )

    return str(aspect), duration


def _parse_scene(
    data: object,
    idx: int,
    char_names: set[str],
    characters: dict[str, Character],
    style_variant_names: set[str],
) -> Scene:
    if not isinstance(data, dict):
        raise ConfigurationError(f"scenes[{idx}] must be a TOML table.")
    d = cast(_TomlObj, data)

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

    chars = _parse_scene_chars(d, idx, char_names)

    # Dialogue: shorthand (speaker/line) for single-char scenes, else per-character table.
    shorthand_dialogue, _, variant = _parse_scene_shorthand_dialogue(d, idx, chars)
    per_char_dialogue = _parse_scene_per_char_dialogue(d, idx, chars)
    dialogue = shorthand_dialogue + per_char_dialogue

    _validate_scene_variant(variant, chars, characters, idx)

    aspect, duration = _parse_scene_numeric_fields(d, idx)

    model = d.get("model")
    if model is not None and not isinstance(model, str):
        raise ConfigurationError(f"scenes[{idx}].model must be a string.")

    def opt(key: str) -> str | None:
        v = d.get(key)
        return v.strip() if isinstance(v, str) else None

    style_variant_raw = d.get("style_variant")
    if style_variant_raw is not None and not isinstance(style_variant_raw, str):
        raise ConfigurationError(f"scenes[{idx}].style_variant must be a string.")
    style_variant = str(style_variant_raw).strip() if isinstance(style_variant_raw, str) else None

    if (
        style_variant is not None
        and style_variant != "none"
        and style_variant not in style_variant_names
    ):
        raise ConfigurationError(
            f"scenes[{idx}].style_variant {style_variant!r} is not a defined "
            f"style variant (defined: {sorted(style_variant_names)!r})."
        )

    style_suffix_raw = d.get("style_suffix")
    if style_suffix_raw is not None and not isinstance(style_suffix_raw, str):
        raise ConfigurationError(f"scenes[{idx}].style_suffix must be a string.")
    style_suffix = str(style_suffix_raw).strip() if isinstance(style_suffix_raw, str) else None

    return Scene(
        id=sid.strip(),
        action=action.strip(),
        title=opt("title"),
        setting=opt("setting"),
        framing=str(framing) if framing else None,
        camera=opt("camera"),
        lighting=opt("lighting"),
        mood=opt("mood"),
        negative=opt("negative"),
        characters=tuple(chars),
        variant=str(variant) if isinstance(variant, str) else None,
        dialogue=tuple(dialogue),
        duration=duration if isinstance(duration, int) else None,
        model=model if isinstance(model, str) else None,
        aspect=aspect,
        count=1,
        style_variant=style_variant,
        style_suffix=style_suffix,
    )


# ---------------------------------------------------------------------------
# Run state — crash-recoverable JSON written alongside the manifest
# ---------------------------------------------------------------------------


@dataclass
class CharacterState:
    """Persisted state for a created character."""

    entity_id: str
    image_paths: list[str | None]

    def to_dict(self) -> dict[str, object]:
        return {"entity_id": self.entity_id, "image_paths": self.image_paths}

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> CharacterState:
        eid = d.get("entity_id")
        raw_paths = d.get("image_paths")
        paths: list[str | None] = []
        if isinstance(raw_paths, list):
            for p in cast(_TomlList, raw_paths):
                paths.append(str(p) if isinstance(p, str) else None)
        return cls(
            entity_id=str(eid) if eid is not None else "",
            image_paths=paths,
        )


@dataclass
class SceneState:
    """Persisted state for a generated scene."""

    media_id: str
    flow_operation_id: str | None
    local_path: str | None
    status: str  # "completed" | "failed"
    prompt: str | None = None  # composed Veo prompt (for handoff projection)
    consistency_method: str = "text"  # "text" | "entity" | "degraded" (P2)
    style_hash: str | None = None  # SHA-256 of composed prompt for resume detection

    def to_dict(self) -> dict[str, object]:
        return {
            "media_id": self.media_id,
            "flow_operation_id": self.flow_operation_id,
            "local_path": self.local_path,
            "status": self.status,
            "prompt": self.prompt,
            "consistency_method": self.consistency_method,
            "style_hash": self.style_hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> SceneState:
        raw_op_id = d.get("flow_operation_id")
        raw_path = d.get("local_path")
        raw_prompt = d.get("prompt")
        raw_method = d.get("consistency_method", "text")
        raw_hash = d.get("style_hash")
        return cls(
            media_id=str(d.get("media_id") or ""),
            flow_operation_id=str(raw_op_id) if isinstance(raw_op_id, str) else None,
            local_path=str(raw_path) if isinstance(raw_path, str) else None,
            status=str(d.get("status") or "completed"),
            prompt=str(raw_prompt) if isinstance(raw_prompt, str) else None,
            consistency_method=str(raw_method) if isinstance(raw_method, str) else "text",
            style_hash=str(raw_hash) if isinstance(raw_hash, str) else None,
        )


class MovieState:
    """Crash-recoverable run state for a movie project.

    Written as JSON alongside the manifest file after each phase completes
    so that a re-run can skip already-completed characters and scenes.
    """

    VERSION = 2

    def __init__(self, *, title: str, project: str) -> None:
        self.title = title
        self.project = project
        self.characters: dict[str, CharacterState] = {}
        self.scenes: dict[str, SceneState] = {}

    @staticmethod
    def state_path_for(manifest_path: Path) -> Path:
        """Return the sibling state file path for *manifest_path*."""
        return manifest_path.parent / (manifest_path.stem + "-state.json")

    @classmethod
    def load(cls, path: Path, *, title: str, project: str) -> MovieState:
        """Load existing state or return a fresh empty state on any error."""
        state = cls(title=title, project=project)
        if not path.exists():
            return state
        try:
            raw: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return state
        if not isinstance(raw, dict):
            return state
        data = cast(_TomlObj, raw)

        chars_raw = data.get("characters")
        if isinstance(chars_raw, dict):
            for name, raw_char in cast(_TomlObj, chars_raw).items():
                if isinstance(raw_char, dict):
                    state.characters[name] = CharacterState.from_dict(cast(_TomlObj, raw_char))
        scenes_raw = data.get("scenes")
        if isinstance(scenes_raw, dict):
            for title_key, raw_scene in cast(_TomlObj, scenes_raw).items():
                if isinstance(raw_scene, dict):
                    state.scenes[title_key] = SceneState.from_dict(cast(_TomlObj, raw_scene))
        return state

    def save(self, path: Path) -> None:
        """Persist state to *path* (creates parent dirs if missing)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {
            "version": self.VERSION,
            "title": self.title,
            "project": self.project,
            "characters": {n: c.to_dict() for n, c in self.characters.items()},
            "scenes": {t: s.to_dict() for t, s in self.scenes.items()},
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
