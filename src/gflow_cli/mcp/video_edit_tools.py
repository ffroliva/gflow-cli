"""MCP tool ``gflow_edit_video`` — video-to-video editing on ``flow.google.com``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import structlog
from pydantic import Field

from gflow_cli.api.video import EDIT_DEFAULT_MODEL, Aspect, EditVideoRequest, VideoModel
from gflow_cli.mcp import tools as common
from gflow_cli.mcp.out_file import OutFileParam
from gflow_cli.mcp.server import server

# One source, reference and project resolution for both clip tools, so they cannot drift.
from gflow_cli.mcp.video_extend_tools import (
    _resolve_extend_project,  # pyright: ignore[reportPrivateUsage]
    _resolve_ordered_references,  # pyright: ignore[reportPrivateUsage]
    _validate_extend_source,  # pyright: ignore[reportPrivateUsage]
)

log = structlog.get_logger()


def _validate_edit_options(
    *,
    source_frag: dict[str, str],
    prompt: str,
    model: str,
    aspect: str | None,
    resolution: str | None,
    reference_images: list[str] | None,
    max_credits: int | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The request's own rules (:class:`EditVideoRequest`), as an envelope, before the queue."""
    norm_res = resolution.lower() if resolution is not None else None
    try:
        EditVideoRequest(
            prompt=prompt,
            source_video=Path(source_frag["source_video"])
            if "source_video" in source_frag
            else None,
            source_media_id=source_frag.get("source_media_id"),
            reference_images=tuple(Path(ref) for ref in reference_images or ()),
            model=VideoModel.from_cli(model) or EDIT_DEFAULT_MODEL,
            aspect=Aspect.from_cli(aspect) if aspect is not None else None,
            resolution=norm_res,
            max_credits=max_credits,
        )
    except ValueError as exc:
        return None, common._bad_param("Invalid Video Edit Request", str(exc))  # pyright: ignore[reportPrivateUsage]
    return {"model": model, "aspect": aspect, "resolution": norm_res}, None


def _build_edit_payload(
    *,
    source_frag: dict[str, str],
    prompt: str,
    opts: dict[str, Any],
    reference_images: list[str] | None,
    tool_specs: tuple[str, ...],
    project: str | None,
    project_name: str | None,
    output: str | None,
    max_credits: int | None,
    profile: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    payload: dict[str, Any] = {"prompt": prompt, "model": opts["model"], **source_frag}
    for key, val in (
        ("aspect", opts["aspect"]),
        ("resolution", opts["resolution"]),
        ("project_id", project),
        ("project_name", project_name),
        ("output_file", output),
        ("max_credits", max_credits),
        ("tool_specs", list(tool_specs) if tool_specs else None),
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


def _preflight_edit_inputs(  # noqa: PLR0913
    *,
    source: str,
    prompt: str,
    model: str,
    aspect: str | None,
    resolution: str | None,
    reference_images: list[str] | None,
    max_credits: int | None,
    project: str | None,
    out_file: str | None,
) -> tuple[tuple[dict[str, str], dict[str, Any], str | None] | None, dict[str, Any] | None]:
    if (proj_err := common._validate_project(project)) is not None:  # pyright: ignore[reportPrivateUsage]
        return None, proj_err
    if isinstance(resolved := common._resolve_out_file(out_file, local_only=True), dict):  # pyright: ignore[reportPrivateUsage]
        return None, resolved
    source_frag, src_err = _validate_extend_source(source)
    if src_err is not None:
        return None, src_err
    assert source_frag is not None
    opts, opt_err = _validate_edit_options(
        source_frag=source_frag,
        prompt=prompt,
        model=model,
        aspect=aspect,
        resolution=resolution,
        reference_images=reference_images,
        max_credits=max_credits,
    )
    if opt_err is not None:
        return None, opt_err
    assert opts is not None
    return (source_frag, opts, resolved), None


@server.tool(
    name="gflow_edit_video",
    description=(
        "Edit an existing video (local .mp4/.mov/.m4v/.3gp/.avi file or Flow clip media UUID) "
        "with a text prompt and 0-5 reference_images on flow.google.com (single-clip output; "
        "Omni models only, default omni-1.2). "
        "max_credits uses Flow's pre-submit quote as a hard cap and refuses missing or over-limit "
        "quotes. Local files support optional aspect ('16:9'/'9:16') and resolution "
        "('360p'/'720p'); existing Flow media UUIDs require project (or a catalogued UUID) and "
        "preserve source aspect at 720p. "
        "out_file, if given, must resolve inside the configured output directory. "
        "Returns the local file path; a model Flow is retiring adds model_retiring to warnings."
    ),
)
@common._guarded  # pyright: ignore[reportPrivateUsage]
async def gflow_edit_video(
    source: str,
    prompt: str,
    reference_images: list[str] | None = None,
    model: str = "omni-1.2",
    aspect: str | None = None,
    resolution: str | None = None,
    max_credits: Annotated[int | None, Field(ge=0)] = None,
    tools: common._ToolsParam = None,  # pyright: ignore[reportPrivateUsage]
    profile: str = common._DEFAULT_PROFILE,  # pyright: ignore[reportPrivateUsage]
    project: str | None = None,
    project_name: str | None = None,
    out_file: OutFileParam = None,
    wait: common._WaitParam = True,  # pyright: ignore[reportPrivateUsage]
) -> dict[str, Any]:
    """Edit a local video file or Flow clip UUID via Flow's V2V edit surface."""
    checked, pre_err = _preflight_edit_inputs(
        source=source,
        prompt=prompt,
        model=model,
        aspect=aspect,
        resolution=resolution,
        reference_images=reference_images,
        max_credits=max_credits,
        project=project,
        out_file=out_file,
    )
    if pre_err is not None:
        return pre_err
    assert checked is not None
    source_frag, opts, output = checked
    if not await common._rate_limiter.acquire():  # pyright: ignore[reportPrivateUsage]
        log.warning("mcp.tool.rate_limited", tool="gflow_edit_video")
        return common._rate_limited_envelope()  # pyright: ignore[reportPrivateUsage]
    resolved_profile = common._resolve_and_validate_profile(profile)  # pyright: ignore[reportPrivateUsage]
    if isinstance(resolved_profile, dict):
        return resolved_profile
    project, proj_err = _resolve_extend_project(
        project, project_name, resolved_profile, source_frag, verb="Editing"
    )
    if proj_err is not None:
        return proj_err
    adapted = common._adapt_tools(tools)  # pyright: ignore[reportPrivateUsage]
    if isinstance(adapted, dict):
        return adapted
    payload, payload_err = _build_edit_payload(
        source_frag=source_frag,
        prompt=prompt,
        opts=opts,
        reference_images=reference_images,
        tool_specs=adapted,
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
        profile=resolved_profile, task_type="edit", payload=payload, wait=wait
    )
    result["params"] = {
        "source": source,
        "prompt": prompt,
        "reference_images": reference_images or [],
        "model": model,
        "aspect": aspect,
        "resolution": opts["resolution"],
        "max_credits": max_credits,
        "tools": tools or [],
        "tool_specs": list(adapted),
        "profile": resolved_profile,
        "requested_profile": profile,
        "project": project,
        "project_name": project_name,
    }
    return result


__all__ = ["gflow_edit_video"]
