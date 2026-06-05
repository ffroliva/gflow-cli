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
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from gflow_cli.errors import ConfigurationError

__all__ = [
    "AssemblyDef",
    "CharacterDef",
    "CharacterState",
    "MovieManifest",
    "MovieState",
    "SceneDef",
    "SceneState",
]

# ---------------------------------------------------------------------------
# Allowed values
# ---------------------------------------------------------------------------

_VALID_SCENE_TYPES: frozenset[str] = frozenset({"t2v", "r2v", "i2v"})
_VALID_VIDEO_ASPECTS: frozenset[str] = frozenset({"9:16", "16:9", "1:1"})
_VALID_DURATIONS: frozenset[int] = frozenset({4, 6, 8, 10})
_VALID_CHARACTER_MODELS: frozenset[str] = frozenset({"nano2", "nanopro"})


# ---------------------------------------------------------------------------
# Manifest DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CharacterDef:
    """One named character to be created (or reused) in the Flow project."""

    name: str
    face_prompt: str
    body_prompt: str | None = None
    model: str = "nano2"


@dataclass(frozen=True)
class SceneDef:
    """One scene in the movie."""

    title: str
    type: str  # "t2v" | "r2v" | "i2v"
    prompt: str
    characters: tuple[str, ...] = field(default_factory=tuple)
    aspect: str = "16:9"
    duration: int | None = None
    model: str | None = None
    count: int = 1
    initial_frame: str | None = None  # i2v only
    end_frame: str | None = None  # i2v only (optional)


@dataclass(frozen=True)
class AssemblyDef:
    """Optional assembly step — render all scenes into one .mp4."""

    output: str | None = None


@dataclass(frozen=True)
class MovieManifest:
    """Validated, immutable representation of a movie.toml file."""

    title: str
    project: str
    characters: tuple[CharacterDef, ...]
    scenes: tuple[SceneDef, ...]
    assemble: AssemblyDef | None = None
    output_dir: str | None = None

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
        return cls._from_dict(cast("dict[str, object]", _raw))

    @classmethod
    def _from_dict(cls, data: dict[str, object]) -> MovieManifest:
        title = data.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ConfigurationError("'title' must be a non-empty string.")

        project = data.get("project")
        if not isinstance(project, str) or not project.strip():
            raise ConfigurationError("'project' must be a non-empty string (your Flow project id).")

        output_dir = data.get("output_dir")
        if output_dir is not None and not isinstance(output_dir, str):
            raise ConfigurationError("'output_dir' must be a string.")

        chars_raw = data.get("characters", [])
        if not isinstance(chars_raw, list):
            raise ConfigurationError("'characters' must be a TOML array.")
        chars_list = cast("list[object]", chars_raw)
        characters = tuple(_parse_character(c, i) for i, c in enumerate(chars_list))
        char_names: set[str] = set()
        for c in characters:
            if c.name in char_names:
                raise ConfigurationError(f"Duplicate character name: {c.name!r}")
            char_names.add(c.name)

        scenes_raw = data.get("scenes", [])
        if not isinstance(scenes_raw, list):
            raise ConfigurationError("'scenes' must be a TOML array.")
        if not scenes_raw:
            raise ConfigurationError("At least one [[scenes]] entry is required.")
        scenes_list = cast("list[object]", scenes_raw)
        scenes = tuple(_parse_scene(s, i, char_names) for i, s in enumerate(scenes_list))
        title_names: set[str] = set()
        for s in scenes:
            if s.title in title_names:
                raise ConfigurationError(f"Duplicate scene title: {s.title!r}")
            title_names.add(s.title)

        assemble: AssemblyDef | None = None
        assemble_raw = data.get("assemble")
        if assemble_raw is not None:
            if not isinstance(assemble_raw, dict):
                raise ConfigurationError("[assemble] must be a TOML table.")
            assemble_dict = cast("dict[str, object]", assemble_raw)
            output = assemble_dict.get("output")
            if output is not None and not isinstance(output, str):
                raise ConfigurationError("assemble.output must be a string path.")
            assemble = AssemblyDef(output=output)

        return cls(
            title=title.strip(),
            project=project.strip(),
            characters=characters,
            scenes=scenes,
            assemble=assemble,
            output_dir=output_dir,
        )


# ---------------------------------------------------------------------------
# Internal parsers
# ---------------------------------------------------------------------------


def _parse_character(data: object, idx: int) -> CharacterDef:
    if not isinstance(data, dict):
        raise ConfigurationError(f"characters[{idx}] must be a TOML table.")
    d = cast("dict[str, object]", data)

    name = d.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigurationError(f"characters[{idx}].name must be a non-empty string.")

    face_prompt = d.get("face_prompt")
    if not isinstance(face_prompt, str) or not face_prompt.strip():
        raise ConfigurationError(f"characters[{idx}].face_prompt must be a non-empty string.")

    body_prompt = d.get("body_prompt")
    if body_prompt is not None and not isinstance(body_prompt, str):
        raise ConfigurationError(f"characters[{idx}].body_prompt must be a string.")

    raw_model = d.get("model", "nano2")
    if not isinstance(raw_model, str) or raw_model not in _VALID_CHARACTER_MODELS:
        raise ConfigurationError(
            f"characters[{idx}].model must be one of "
            f"{sorted(_VALID_CHARACTER_MODELS)} (got {raw_model!r})."
        )

    return CharacterDef(
        name=name.strip(),
        face_prompt=face_prompt.strip(),
        body_prompt=body_prompt.strip() if isinstance(body_prompt, str) else None,
        model=raw_model,
    )


def _parse_scene(data: object, idx: int, char_names: set[str]) -> SceneDef:
    if not isinstance(data, dict):
        raise ConfigurationError(f"scenes[{idx}] must be a TOML table.")
    d = cast("dict[str, object]", data)

    title = d.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ConfigurationError(f"scenes[{idx}].title must be a non-empty string.")

    scene_type = d.get("type")
    if not isinstance(scene_type, str) or scene_type not in _VALID_SCENE_TYPES:
        raise ConfigurationError(
            f"scenes[{idx}].type must be one of {sorted(_VALID_SCENE_TYPES)} (got {scene_type!r})."
        )

    prompt = d.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ConfigurationError(f"scenes[{idx}].prompt must be a non-empty string.")

    chars_raw = d.get("characters", [])
    if not isinstance(chars_raw, list):
        raise ConfigurationError(f"scenes[{idx}].characters must be a TOML array.")
    char_names_in_scene: list[str] = []
    for char_name in cast("list[object]", chars_raw):
        if not isinstance(char_name, str):
            raise ConfigurationError(f"scenes[{idx}].characters entries must be strings.")
        if char_name not in char_names:
            raise ConfigurationError(
                f"scenes[{idx}] references unknown character {char_name!r}. "
                f"Defined: {sorted(char_names)!r}"
            )
        char_names_in_scene.append(char_name)

    raw_aspect = d.get("aspect", "16:9")
    if not isinstance(raw_aspect, str) or raw_aspect not in _VALID_VIDEO_ASPECTS:
        raise ConfigurationError(
            f"scenes[{idx}].aspect must be one of "
            f"{sorted(_VALID_VIDEO_ASPECTS)} (got {raw_aspect!r})."
        )

    duration = d.get("duration")
    if duration is not None:
        if not isinstance(duration, int) or duration not in _VALID_DURATIONS:
            raise ConfigurationError(
                f"scenes[{idx}].duration must be one of "
                f"{sorted(_VALID_DURATIONS)} seconds (got {duration!r})."
            )

    raw_model = d.get("model")
    if raw_model is not None and not isinstance(raw_model, str):
        raise ConfigurationError(f"scenes[{idx}].model must be a string.")
    scene_model: str | None = raw_model if isinstance(raw_model, str) else None

    count = d.get("count", 1)
    if not isinstance(count, int) or not (1 <= count <= 4):
        raise ConfigurationError(f"scenes[{idx}].count must be an integer 1–4 (got {count!r}).")

    initial_frame = d.get("initial_frame")
    end_frame = d.get("end_frame")
    if initial_frame is not None and not isinstance(initial_frame, str):
        raise ConfigurationError(f"scenes[{idx}].initial_frame must be a string path.")
    if end_frame is not None and not isinstance(end_frame, str):
        raise ConfigurationError(f"scenes[{idx}].end_frame must be a string path.")

    if scene_type == "i2v" and not initial_frame:
        raise ConfigurationError(f"scenes[{idx}] (type=i2v) requires 'initial_frame'.")

    return SceneDef(
        title=title.strip(),
        type=scene_type,
        prompt=prompt.strip(),
        characters=tuple(char_names_in_scene),
        aspect=raw_aspect,
        duration=duration if isinstance(duration, int) else None,
        model=scene_model,
        count=count,
        initial_frame=initial_frame if isinstance(initial_frame, str) else None,
        end_frame=end_frame if isinstance(end_frame, str) else None,
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
            for p in cast("list[object]", raw_paths):
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

    def to_dict(self) -> dict[str, object]:
        return {
            "media_id": self.media_id,
            "flow_operation_id": self.flow_operation_id,
            "local_path": self.local_path,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> SceneState:
        raw_op_id = d.get("flow_operation_id")
        raw_path = d.get("local_path")
        return cls(
            media_id=str(d.get("media_id") or ""),
            flow_operation_id=str(raw_op_id) if isinstance(raw_op_id, str) else None,
            local_path=str(raw_path) if isinstance(raw_path, str) else None,
            status=str(d.get("status") or "completed"),
        )


class MovieState:
    """Crash-recoverable run state for a movie project.

    Written as JSON alongside the manifest file after each phase completes
    so that a re-run can skip already-completed characters and scenes.
    """

    VERSION = 1

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
        data = cast("dict[str, object]", raw)

        chars_raw = data.get("characters")
        if isinstance(chars_raw, dict):
            for name, raw_char in cast("dict[str, object]", chars_raw).items():
                if isinstance(raw_char, dict):
                    state.characters[name] = CharacterState.from_dict(
                        cast("dict[str, object]", raw_char)
                    )
        scenes_raw = data.get("scenes")
        if isinstance(scenes_raw, dict):
            for title_key, raw_scene in cast("dict[str, object]", scenes_raw).items():
                if isinstance(raw_scene, dict):
                    state.scenes[title_key] = SceneState.from_dict(
                        cast("dict[str, object]", raw_scene)
                    )
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
