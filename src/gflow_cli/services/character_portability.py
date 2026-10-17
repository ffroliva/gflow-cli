"""Move a character between projects and accounts: export to a folder, import from it.

Flow has no copy primitive — a character belongs to one project and its images are bound
there — so a character travels as a :mod:`gflow_cli.character_bundle` folder: its name,
voice, personality and full-size images. Import rebuilds it from those files through the
file-built create (``character_create_from_files``); nothing is generated, so neither side
spends credits or image quota. Another account is just another ``--profile``: export and
import run one after the other, never with two profiles open at once.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, TypeVar

import structlog

from gflow_cli.character_bundle import CharacterBundle, check_destination, read_bundle, write_bundle
from gflow_cli.errors import ConfigurationError
from gflow_cli.services.character_create import character_create_from_files

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from gflow_cli.api.character import CharacterCreateResult
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.video import GenerateVideoRequest
    from gflow_cli.data.recorder import OperationRecorder

log = structlog.get_logger(__name__)

#: An image or a video request: both carry the same `reference_entities` pair.
Req = TypeVar("Req", "GenerateImageRequest", "GenerateVideoRequest")


async def export_character(
    client: FlowApiClient,
    *,
    project_id: str,
    out: Path,
    entity_id: str | None = None,
    name: str | None = None,
) -> CharacterBundle:
    """Write the character (by id or exact name) and its images to the folder ``out``.

    ``out`` must not exist or be empty — checked before Flow is read, so a mistyped path
    costs nothing. The bundle names no project, entity or account."""
    check_destination(out)
    char, images = await client.read_character_for_export(
        project_id, entity_id=entity_id, name=name
    )
    bundle = write_bundle(
        out,
        name=char.display_name,
        voice=char.voice,
        personality=char.personality,
        images=images,
    )
    log.info("character.exported", entity_id=char.entity_id, images=len(bundle.images))
    return bundle


async def import_character(
    client: FlowApiClient,
    recorder: OperationRecorder,
    *,
    profile_name: str,
    profile_dir: Path,
    bundle_dir: Path,
    project_id: str,
    name: str | None = None,
    voice: str | None = None,
) -> CharacterCreateResult:
    """Rebuild a bundle's character in ``project_id`` (on the client's profile).

    Order matters, because nothing created on Flow is ever deleted by gflow: the bundle is
    validated in full first (offline), then the target's LIVE voice catalog is checked,
    and only then is anything created. ``name`` / ``voice`` override the bundle's."""
    bundle = read_bundle(bundle_dir)
    portrait = bundle.image("portrait")
    assert portrait is not None  # read_bundle guarantees a portrait
    body = bundle.image("body")
    wanted = voice or bundle.voice
    resolved_voice: str | None = None
    if wanted:
        offered = {v.name.casefold(): v.name for v in await client.list_voices(project_id)}
        resolved_voice = offered.get(wanted.casefold())
        if resolved_voice is None:
            raise ConfigurationError(
                detail=(
                    f"voice {wanted!r} is not among the {len(offered)} this account offers; "
                    "pass --voice (MCP: voice=) with one it does. Nothing was created"
                ),
                remediation_hint=(
                    "This account's LIVE voice catalog decides, and it can differ from the "
                    "static `gflow character voices` list. Override the voice, or edit the "
                    "bundle's manifest."
                ),
            )
    result = await character_create_from_files(
        client,
        recorder,
        profile_name=profile_name,
        profile_dir=profile_dir,
        project_id=project_id,
        name=name or bundle.name,
        portrait=portrait.path,
        body=body.path if body else None,
        voice=resolved_voice,
        personality=bundle.personality,
    )
    log.info("character.imported", entity_id=result.entity_id, project_id=project_id)
    return result


def validate_bundles(bundles: Sequence[Path]) -> None:
    """Read every bundle fully, offline, BEFORE Flow is touched: a bad folder used to surface
    only after a scratch project had been created for it (council D7/D8)."""
    for path in bundles:
        read_bundle(path)


async def apply_character_bundles(
    client: FlowApiClient,
    recorder: OperationRecorder,
    req: Req,
    *,
    project_id: str,
    profile_name: str,
    profile_dir: Path,
    bundles: Sequence[Path],
) -> Req:
    """Make each bundle's character available in ``project_id`` and attach it to ``req``.

    Generate directly from a character folder, in any project on any account (#639,
    option C). Every bundle is validated offline first; then a character already in the
    project with the bundle's name is REUSED (user decision: by name, even if it differs
    from the bundle), and an absent one is IMPORTED — free, nothing generated. Two
    characters sharing the name are refused, never guessed. The character then rides the
    request's references like ``--reference-entity`` + ``--reference-entity-name``."""
    if not bundles:
        return req
    read = [read_bundle(path) for path in bundles]
    existing = await client.list_characters(project_id)
    entities = list(req.reference_entities)
    names = list(req.reference_entity_names)
    done: dict[str, str] = {}  # name → entity: one name in one call is one character
    for path, bundle in zip(bundles, read, strict=True):
        # Case-insensitive, as the `@` picker matches titles: an exact compare imported a
        # second "Nora" beside "nora", and the picker then refused both (CodeRabbit, #987).
        key = bundle.name.casefold()
        if key in done:
            continue
        same = [c for c in existing if c.display_name.casefold() == key]
        if len(same) > 1:
            ids = ", ".join(c.entity_id for c in same)
            raise ConfigurationError(
                detail=(
                    f"project {project_id} has {len(same)} characters named {bundle.name!r} "
                    f"({ids}); pass --reference-entity with the one you mean"
                ),
            )
        if same:
            entity_id = same[0].entity_id
            log.info("character.bundle_reused", entity_id=entity_id, project_id=project_id)
        else:
            created = await import_character(
                client,
                recorder,
                profile_name=profile_name,
                profile_dir=profile_dir,
                bundle_dir=path,
                project_id=project_id,
            )
            entity_id = created.entity_id
        done[key] = entity_id
        if entity_id not in entities:
            entities.append(entity_id)
            names.append(bundle.name)
    return dataclasses.replace(
        req, reference_entities=tuple(entities), reference_entity_names=tuple(names)
    )
