"""MCP tool ``gflow_extend_video`` — single-segment video extend on ``flow.google.com``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import click
import structlog
from pydantic import Field

from gflow_cli.api.video import Aspect, is_media_uuid
from gflow_cli.api.video_extend import (
    EXTEND_MAX_DURATION,
    EXTEND_MIN_DURATION,
    MAX_EXTEND_REFERENCE_IMAGES,
)
from gflow_cli.cli_video_extend import resolve_extend_project
from gflow_cli.config import get_settings
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import ConfigurationError
from gflow_cli.mcp import tools as common
from gflow_cli.mcp.out_file import OutFileParam
from gflow_cli.mcp.server import server

log = structlog.get_logger()


def _validate_extend_source(source: str) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
    if is_media_uuid(source):
        return {"source_media_id": source}, None
    path = Path(source).expanduser().resolve()
    if not path.is_file():
        return None, common._bad_param(  # pyright: ignore[reportPrivateUsage]
            "Invalid Source Video",
            f"Source '{source}' is neither a Flow media UUID nor an existing local video file.",
        )
    return {"source_video": str(path)}, None


def _validate_extend_options(
    *, aspect: str | None, reference_images: list[str] | None
) -> dict[str, Any] | None:
    """The checks the schema cannot make (duration and max_credits ranges are in it)."""
    bad = common._bad_param  # pyright: ignore[reportPrivateUsage]
    if aspect is not None:
        try:
            aspect_enum = Aspect.from_cli(aspect)
        except ValueError as exc:
            return bad("Invalid Aspect Ratio", str(exc))
        if aspect_enum is Aspect.SQUARE:
            return bad("Invalid Aspect Ratio", "Video extend accepts '9:16' or '16:9'.")
    if reference_images and len(reference_images) > MAX_EXTEND_REFERENCE_IMAGES:
        return bad(
            "Too Many Reference Images",
            f"video extend accepts at most {MAX_EXTEND_REFERENCE_IMAGES} reference_images "
            f"(got {len(reference_images)}).",
        )
    return None


def _resolve_ordered_references(
    reference_images: list[str], profile: str
) -> tuple[list[str] | None, dict[str, Any] | None]:
    """Every reference as a local file path, in the caller's order (the wire's order).

    A media UUID resolves to its catalogued file, the way the r2v refs do.
    """
    ordered: list[str] = []
    with DataStore.open(get_settings().resolved_db_path()) as store:
        repo = DataRepository(store)
        for ref in reference_images:
            if is_media_uuid(ref):
                resolved, err = common._resolve_ref_local_path(repo, profile, ref)  # pyright: ignore[reportPrivateUsage]
            else:
                resolved, err = common._resolve_image_path(  # pyright: ignore[reportPrivateUsage]
                    ref, title="Invalid Reference Image", label="Reference image path"
                )
            if err is not None:
                return None, err
            ordered.append(str(resolved))
    return ordered, None


def _resolve_extend_project(
    project: str | None,
    project_name: str | None,
    resolved_profile: str,
    source_frag: dict[str, str],
) -> tuple[str | None, dict[str, Any] | None]:
    """The CLI's project resolution (:func:`resolve_extend_project`), as an envelope."""
    try:
        resolved = resolve_extend_project(
            project_id=project,
            project_name=project_name,
            profile_name=resolved_profile,
            media_id=source_frag.get("source_media_id"),
        )
    except click.UsageError as exc:
        return None, common._bad_param("Default Project Belongs To Another Profile", exc.message)  # pyright: ignore[reportPrivateUsage]
    except ConfigurationError:
        return None, common._bad_param(  # pyright: ignore[reportPrivateUsage]
            "Missing Project",
            "Extending an existing Flow media UUID requires 'project' "
            "(not found in the local catalog).",
        )
    return resolved, None


def _build_extend_payload(
    *,
    source_frag: dict[str, str],
    prompt: str,
    duration: int | None,
    aspect: str | None,
    reference_images: list[str] | None,
    project: str | None,
    project_name: str | None,
    output: str | None,
    max_credits: int | None,
    profile: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    payload: dict[str, Any] = {"prompt": prompt, **source_frag}
    for key, val in (
        ("duration", duration),
        ("aspect", aspect),
        ("project_id", project),
        ("project_name", project_name),
        ("output_file", output),
        ("max_credits", max_credits),
    ):
        if val is not None:
            payload[key] = val
    if reference_images:
        ordered, err = _resolve_ordered_references(reference_images, profile)
        if err is not None:
            return None, err
        assert ordered is not None
        payload["reference_images"] = ordered
    return payload, None


def _preflight_extend_inputs(
    *,
    source: str,
    prompt: str,
    aspect: str | None,
    reference_images: list[str] | None,
    project: str | None,
    out_file: str | None,
) -> tuple[tuple[dict[str, str], str | None] | None, dict[str, Any] | None]:
    if not prompt.strip():
        return None, common._bad_param(  # pyright: ignore[reportPrivateUsage]
            "Invalid Prompt", "prompt must not be empty."
        )
    if (proj_err := common._validate_project(project)) is not None:  # pyright: ignore[reportPrivateUsage]
        return None, proj_err
    if isinstance(resolved := common._resolve_out_file(out_file, local_only=True), dict):  # pyright: ignore[reportPrivateUsage]
        return None, resolved
    source_frag, src_err = _validate_extend_source(source)
    if src_err is not None:
        return None, src_err
    assert source_frag is not None
    opt_err = _validate_extend_options(aspect=aspect, reference_images=reference_images)
    if opt_err is not None:
        return None, opt_err
    return (source_frag, resolved), None


@server.tool(
    name="gflow_extend_video",
    description=(
        "Continue an existing video (local .mp4/.mov/.m4v/.3gp/.avi file or Flow clip media UUID) "
        "by one segment on flow.google.com with a text prompt, optional duration (3-10 seconds), "
        "and 0-3 reference_images. "
        "max_credits reads Flow's pre-submit quote in the extend settings popover and refuses "
        "missing or over-limit quotes before anything is billed. "
        "Existing Flow media UUIDs require project (or a catalogued UUID); local video files "
        "auto-create a project when project is omitted. Preserves the source clip's aspect "
        "(optional aspect '9:16'/'16:9' verifies a match and refuses mismatches pre-submit). "
        "out_file, if given, must resolve inside the configured output directory. "
        "Spends video credits; returns the local file path to the extended clip."
    ),
)
@common._guarded  # pyright: ignore[reportPrivateUsage]
async def gflow_extend_video(  # noqa: PLR0913
    source: str,
    prompt: str,
    duration: Annotated[int | None, Field(ge=EXTEND_MIN_DURATION, le=EXTEND_MAX_DURATION)] = None,
    reference_images: list[str] | None = None,
    aspect: str | None = None,
    max_credits: Annotated[int | None, Field(ge=0)] = None,
    profile: str = common._DEFAULT_PROFILE,  # pyright: ignore[reportPrivateUsage]
    project: str | None = None,
    project_name: str | None = None,
    out_file: OutFileParam = None,
    wait: common._WaitParam = True,  # pyright: ignore[reportPrivateUsage]
) -> dict[str, Any]:
    """Continue a local video file or Flow clip UUID by one segment on flow.google.com."""
    checked, pre_err = _preflight_extend_inputs(
        source=source,
        prompt=prompt,
        aspect=aspect,
        reference_images=reference_images,
        project=project,
        out_file=out_file,
    )
    if pre_err is not None:
        return pre_err
    assert checked is not None
    source_frag, output = checked
    if not await common._rate_limiter.acquire():  # pyright: ignore[reportPrivateUsage]
        log.warning("mcp.tool.rate_limited", tool="gflow_extend_video")
        return common._rate_limited_envelope()  # pyright: ignore[reportPrivateUsage]
    resolved_profile = common._resolve_and_validate_profile(profile)  # pyright: ignore[reportPrivateUsage]
    if isinstance(resolved_profile, dict):
        return resolved_profile
    project, proj_err = _resolve_extend_project(
        project, project_name, resolved_profile, source_frag
    )
    if proj_err is not None:
        return proj_err
    payload, payload_err = _build_extend_payload(
        source_frag=source_frag,
        prompt=prompt,
        duration=duration,
        aspect=aspect,
        reference_images=reference_images,
        project=project,
        project_name=project_name,
        output=output,
        max_credits=max_credits,
        profile=resolved_profile,
    )
    if payload_err is not None:
        return payload_err
    assert payload is not None
    result = await common._run_generation_task(  # pyright: ignore[reportPrivateUsage]
        profile=resolved_profile, task_type="extend", payload=payload, wait=wait
    )
    result["params"] = {
        "source": source,
        "prompt": prompt,
        "duration": duration,
        "reference_images": reference_images or [],
        "aspect": aspect,
        "max_credits": max_credits,
        "profile": resolved_profile,
        "requested_profile": profile,
        "project": project,
        "project_name": project_name,
    }
    return result


__all__ = ["gflow_extend_video"]
