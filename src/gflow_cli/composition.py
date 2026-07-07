"""Pure prompt-composition core for gflow movie (no I/O).

Holds the structured style + character model, the framing vocabulary, the
deterministic prompt composer, and the handoff-manifest projection. Imports
nothing from the Flow API or browser layers — it is `(data) -> value` and is
the reusable seam a future second consumer (e.g. remotion) can import.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from gflow_cli import __version__


@dataclass(frozen=True)
class StyleSpec:
    """Global guiding prompt — every field optional, reused verbatim per scene.

    ``prefix`` / ``suffix`` are raw strings prepended / appended to the composed
    prompt.  ``variants`` is a name → suffix mapping that lets a manifest express
    a style arc (e.g. monochrome → warm) without repeating text in every scene.
    """

    look: str | None = None
    palette: str | None = None
    environment: str | None = None
    camera: str | None = None
    lighting: str | None = None
    mood: str | None = None
    negative: str | None = None
    prefix: str | None = None
    suffix: str | None = None
    variants: Mapping[str, str] = field(default_factory=dict[str, str])

    def resolve_suffix(self, variant_name: str | None) -> str | None:
        """Return the named variant's suffix, or the base suffix when ``None``.

        Raises ValueError on an unknown variant name (mirrors
        ``Character.resolve_variant``).
        """
        if variant_name is None:
            return self.suffix
        if variant_name not in self.variants:
            msg = f"unknown style variant {variant_name!r}"
            raise ValueError(msg)
        return self.variants[variant_name]


@dataclass(frozen=True)
class Character:
    """A reusable character. Identity is text (P1) or entity (P2)."""

    name: str
    appearance: str | None = None
    identity: str = "text"  # "text" | "entity"
    voice: str | None = None  # voice resource id / preset name (P2)
    variants: Mapping[str, str] = field(default_factory=dict[str, str])
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
    style_variant: str | None = None
    style_suffix: str | None = None


def _sentence(text: str) -> str:
    """Capitalize first letter, ensure a trailing period (idempotent)."""
    t = text.strip()
    if not t:
        return ""
    t = t[0].upper() + t[1:]
    if t[-1] not in ".!?":
        t += "."
    return t


def _dialogue_block(scene: Scene, characters: Mapping[str, Character]) -> str:
    lines = scene.dialogue
    if not lines:
        return ""

    def voice_for(d: DialogueLine) -> str | None:
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
    rows: list[str] = []
    for d in lines:
        v = voice_for(d)
        who = f"{d.speaker} ({v})" if v else d.speaker
        rows.append(f'{who}: "{esc(d.line)}"')
    return "Dialogue:\n" + "\n".join(rows)


def _subjects_block(scene: Scene, characters: Mapping[str, Character]) -> str:
    """Return the subject sentence for all characters in the scene, or ''."""
    subjects: list[str] = []
    for name in scene.characters:
        c = characters.get(name)
        if c is None:
            continue
        subj = (
            c.resolve_variant(scene.variant) if len(scene.characters) == 1 else (c.appearance or "")
        )
        if subj:
            subjects.append(subj)
    return _sentence("; ".join(subjects)) if subjects else ""


def _framing_camera_block(scene: Scene, style: StyleSpec) -> str:
    """Return the combined framing+camera sentence, or ''."""
    camera = scene.camera or style.camera
    framing_cam = ", ".join(
        x for x in ([f"{scene.framing} shot" if scene.framing else None, camera]) if x
    )
    return _sentence(framing_cam) if framing_cam else ""


def resolve_style_suffix(style: StyleSpec, scene: Scene) -> str | None:
    """Resolve the effective style suffix for a scene.

    ``scene.style_variant = "none"`` → opt out of base and variant suffixes
    (``scene.style_suffix`` is independent and still applied); the manifest
    parser rejects a variant literally named "none" so the sentinel cannot
    shadow a real variant. Otherwise defer to :meth:`StyleSpec.resolve_suffix`
    (variant suffix, or base ``style.suffix`` when no variant is set).
    """
    if scene.style_variant == "none":
        return None
    return style.resolve_suffix(scene.style_variant)


def compose_prompt(
    style: StyleSpec,
    scene: Scene,
    characters: Mapping[str, Character],
) -> str:
    """Assemble the final Veo prompt deterministically (canonical order).

    Order: [prefix] + action + subject(+variant) + setting + look + palette +
    lighting + framing+camera + mood + dialogue + negative + [suffix].
    Each slot: scene override -> global -> omit.  ``negative`` MERGES
    global+scene.  ``prefix`` / ``suffix`` wrap the entire output.
    """
    parts: list[str] = []

    # 0. PREFIX (raw — wraps the entire prompt)
    if style.prefix:
        parts.append(style.prefix)

    # 1. ACTION (required)
    parts.append(_sentence(scene.action))

    # 2. SUBJECT (appearance + variant) for each named character
    subj_block = _subjects_block(scene, characters)
    if subj_block:
        parts.append(subj_block)

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
    framing_cam_block = _framing_camera_block(scene, style)
    if framing_cam_block:
        parts.append(framing_cam_block)

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

    # 11. SUFFIX (variant or base — raw)
    suffix = resolve_style_suffix(style, scene)
    if suffix:
        parts.append(suffix)

    # 12. SCENE STYLE_SUFFIX (raw, always last)
    if scene.style_suffix:
        parts.append(scene.style_suffix)

    return " ".join(p for p in parts if p)


def resume_hash(prompt: str) -> str:
    """Return the full SHA-256 hex digest of *prompt* for resume change detection.

    Same digest as ``gflow_cli.data.redaction.prompt_fields`` computes for
    observability; duplicated here because this module deliberately imports
    nothing from the rest of the package (pure seam).
    """
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _build_handoff_characters(manifest: Any, state: Any) -> list[dict[str, Any]]:
    """Build the ``characters`` list for the handoff manifest."""
    chars_out: list[dict[str, Any]] = []
    for c in manifest.characters.values():
        x_gflow_char: dict[str, Any] = {}
        cstate = state.characters.get(c.name)
        if cstate is not None and cstate.entity_id:
            x_gflow_char["entity_id"] = cstate.entity_id
        chars_out.append(
            {
                "name": c.name,
                "identity": c.identity,
                "voice": c.voice,
                "x_gflow": x_gflow_char,
            }
        )
    return chars_out


def _build_handoff_clip(
    *,
    index: int,
    scene: Any,
    ss: Any,
    manifest: Any,
    rel: Any,
    include_prompts: bool,
) -> dict[str, Any]:
    """Build one clip entry for the handoff manifest."""
    status = ss.status if ss else "failed"
    dur = float(scene.duration) if scene.duration else None

    prompt_val: str | None = None
    if include_prompts:
        stored = getattr(ss, "prompt", None) if ss else None
        prompt_val = stored or compose_prompt(manifest.style, scene, manifest.characters)

    # Style applied — the styling baked into this clip's prompt. Resolved from
    # the current manifest; the resume check in cli_movie regenerates a scene
    # whenever its composed prompt changes, so this stays consistent with the
    # stored prompt for every clip that survives a run.
    style_applied: dict[str, Any] = {
        "variant": scene.style_variant,
        "prefix": manifest.style.prefix,
        "suffix": resolve_style_suffix(manifest.style, scene),
        "scene_suffix": scene.style_suffix,
    }
    if all(v is None for v in style_applied.values()):
        style_applied = {}

    return {
        "id": scene.id,
        "index": index,
        "file": rel(ss.local_path) if ss else None,
        "duration_seconds": dur,
        "framing": scene.framing,
        "characters": list(scene.characters),
        "consistency_method": (getattr(ss, "consistency_method", "text") if ss else "text"),
        "dialogue": [
            {"speaker": d.speaker, "line": d.line, "voice": d.voice} for d in scene.dialogue
        ],
        "prompt": prompt_val,
        "status": status,
        "style_applied": style_applied,
        "x_gflow": {
            k: v
            for k, v in (
                ("media_id", ss.media_id if ss else None),
                ("operation_id", ss.flow_operation_id if ss else None),
                ("project_id", manifest.project),
            )
            if v
        },
    }


def build_handoff(
    manifest: Any,
    state: Any,
    *,
    out_dir: Path,
    version: str = __version__,
    include_prompts: bool = True,
) -> dict[str, Any]:
    """Project a completed/partial movie run into the versioned handoff manifest.

    Pure: derives entirely from ``manifest`` (MovieManifest) + ``state``
    (MovieState). Paths are made relative to ``out_dir`` and POSIX-normalized.
    Flow-internal ids go under ``x_gflow``. No signed URLs / tokens / PII ever
    enter the output. When ``include_prompts`` is ``False`` each clip's
    ``prompt`` is set to ``None`` (honors GFLOW_CLI_HISTORY_PROMPTS upstream).
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

    style_fields = cast("dict[str, Any]", vars(manifest.style))
    style_out: dict[str, Any] = {k: v for k, v in style_fields.items() if v}

    chars_out = _build_handoff_characters(manifest, state)

    clips: list[dict[str, Any]] = []
    total = 0.0
    for index, scene in enumerate(manifest.scenes):
        ss = state.scenes.get(scene.id)
        dur = float(scene.duration) if scene.duration else None
        if dur:
            total += dur
        clips.append(
            _build_handoff_clip(
                index=index,
                scene=scene,
                ss=ss,
                manifest=manifest,
                rel=rel,
                include_prompts=include_prompts,
            )
        )

    return {
        "schema_version": 1,
        "generator": {"name": "gflow-cli", "version": version},
        "movie": {
            "title": manifest.title,
            "output_dir": ".",
            "total_duration_seconds": total,
        },
        "style": style_out,
        "characters": chars_out,
        "clips": clips,
        "stitch": {"performed": False, "output": None},
    }
