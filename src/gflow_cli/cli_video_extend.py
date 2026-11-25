"""Validation and routing helpers for ``gflow video extend``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import click
import structlog
from rich.console import Console

from gflow_cli._cli_helpers import default_project, run_with_handlers
from gflow_cli.api.video import is_media_uuid
from gflow_cli.api.video_extend import (
    EXTEND_MAX_DURATION,
    EXTEND_MIN_DURATION,
    MAX_EXTEND_REFERENCE_IMAGES,
)
from gflow_cli.cli_image import lookup_project_in_catalog
from gflow_cli.errors import ConfigurationError, DataStoreError, FlowHostMigratedError

if TYPE_CHECKING:
    from gflow_cli.api.video_extend import ExtendStarted
    from gflow_cli.data.recorder import OperationRecorder

_logger = structlog.get_logger(__name__)
_EXTEND_COMMAND = "video extend"


def resolve_extend_source(source: str, scene_id: str | None) -> Path | None:
    """Validate *source* (Flow media UUID or existing local video) and optional *scene_id*.

    Returns the resolved local ``Path`` when *source* is a local video file, or ``None``
    when *source* is a Flow media UUID.
    """
    if scene_id is not None and not is_media_uuid(scene_id):
        msg = f"scene id must be a UUID, got {scene_id!r}"
        raise click.BadParameter(msg)
    if is_media_uuid(source):
        return None
    candidate = Path(source).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    msg = f"SOURCE must be a Flow media UUID or an existing local video file, got {source!r}"
    raise click.BadParameter(msg)


def validate_extend_refs(refs: tuple[Path, ...]) -> tuple[Path, ...]:
    """Enforce Flow's ``fZytfe`` hard cap of 0-3 reference images at the Click boundary."""
    if len(refs) > MAX_EXTEND_REFERENCE_IMAGES:
        msg = (
            f"video extend accepts at most {MAX_EXTEND_REFERENCE_IMAGES} --ref image(s) "
            f"(got {len(refs)})"
        )
        raise click.BadParameter(msg, param_hint="'--ref'")
    return tuple(ref.expanduser().resolve() for ref in refs)


def labs_unported_extend_refusal(
    *,
    source_video: Path | None,
    duration: int | None,
    refs: tuple[Path, ...],
) -> FlowHostMigratedError | None:
    """Exit 36 for an extend option not ported to the labs.google extend path yet."""
    reasons: list[str] = []
    if source_video is not None:
        reasons.append("extending a local video file")
    if duration is not None:
        reasons.append(f"--duration ({EXTEND_MIN_DURATION}-{EXTEND_MAX_DURATION}s)")
    if refs:
        reasons.append("--ref reference images")
    if not reasons:
        return None
    joined = ", ".join(reasons)
    return FlowHostMigratedError(
        detail=(
            f"video extend options ({joined}) are not ported to the labs.google extend "
            "path or to multi-segment/--scene/--seed chains yet"
        ),
        remediation_hint=(
            "Use single-segment extend on flow.google.com (one PROMPT, no --segments > 1 / "
            "--scene / --resume-from / --seed, and unset GFLOW_CLI_FLOW_HOST=labs.google)."
        ),
    )


def resolve_extend_project(
    *,
    project_id: str | None,
    project_name: str | None,
    profile_name: str,
    media_id: str | None,
    command: str = "extend",
) -> str | None:
    """The project an extend (or a video edit) runs in, for the CLI and the MCP twin alike.

    ``--project``; else, for a Flow media id, the project the catalog records for it;
    else :func:`default_project` (the ``GFLOW_CLI_PROJECT`` pin). ``None`` means a new
    project is created for a local upload; a media id with no project is exit 11.
    """
    if project_id is None and media_id is not None:
        found = lookup_project_in_catalog(media_id=media_id, profile_name=profile_name)
        if found:
            return found
    resolved = default_project(project_id, project_name, profile_name)
    if resolved is None and media_id is not None:
        msg = (
            f"--project is required: {command} must know which project owns the source clip "
            "(it is not in the local catalog)"
        )
        raise ConfigurationError(msg)
    return resolved


def refuse_unported_extend(
    refusal: Exception, is_json: bool, output_format: str, cli_command: str = _EXTEND_COMMAND
) -> None:
    async def _refuse() -> None:
        raise refusal

    run_with_handlers(
        _refuse,
        cli_command=cli_command,
        as_json=is_json,
        output_format=output_format,
    )


def make_migrated_extend_on_started(  # noqa: PLR0913
    *,
    profile_name: str,
    profile_dir: Path,
    project_id: str,
    source_media_id: str | None,
    aspect: str | None,
    as_json: bool,
    recorder: OperationRecorder | None,
    console: Console | None = None,
) -> Callable[[ExtendStarted, str | None], None]:
    """The migrated arm's submit hook: announce it and record it at acceptance."""

    def _on_started(started: ExtendStarted, page_aspect: str | None) -> None:
        if not as_json and console is not None:
            console.print(f"  submitted   : {started.media_id} ({started.model_key})")
        if recorder is not None:  # at SUBMIT: Flow bills on acceptance
            try:
                recorder.record_started_extend(
                    profile_name=profile_name,
                    profile_dir=profile_dir,
                    project_id=project_id,
                    aspect=page_aspect or aspect or "unknown",
                    started=started,
                    source_media_id=source_media_id,
                )
            except DataStoreError as exc:
                _logger.warning("extend_record_failed", error_class=type(exc).__name__)

    return _on_started


__all__ = [
    "make_migrated_extend_on_started",
    "labs_unported_extend_refusal",
    "refuse_unported_extend",
    "resolve_extend_project",
    "resolve_extend_source",
    "validate_extend_refs",
]
