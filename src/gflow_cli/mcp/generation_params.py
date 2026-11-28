"""Parameter validation and payload helpers shared by the MCP generation tools.

Kept out of :mod:`gflow_cli.mcp.tools`, which re-binds each under its historical private
name, so the tool module does not grow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from gflow_cli._cli_helpers import upscale_route
from gflow_cli.api.generation_route import Route
from gflow_cli.api.submit_policy import VisibilitySetting
from gflow_cli.config import UiMode
from gflow_cli.data.models import AssetLookup
from gflow_cli.errors import outcome_members

_BAD_PARAM_TYPE = "https://gflow-cli.dev/errors/bad-parameter"


def bad_param(title: str, detail: str) -> dict[str, Any]:
    """Build the standard RFC 9457 bad-parameter (400) error envelope."""
    return {
        "status": "error",
        "error": {
            "type": _BAD_PARAM_TYPE,
            "title": title,
            "status": 400,
            "detail": detail,
            **outcome_members("no", "fix_input"),
        },
    }


#: The twin of the CLI's ``--browser-visibility`` (GFLOW_CLI_BROWSER_VISIBILITY when unset).
BrowserVisibilityParam = Annotated[
    str | None,
    Field(
        description=(
            "Browser window policy for this job; overrides GFLOW_CLI_BROWSER_VISIBILITY. "
            "'auto' (the default): shapes the direct path can send run with a headless "
            "browser that only mints the reCAPTCHA token, and a refusal Flow did not charge "
            "is retried once from a new headless browser; shapes it cannot send, a capped "
            "video (max_credits) among them, open a visible browser. 'headed' always shows "
            "the window. 'headless' also runs Flow UI shapes headless, which is unsupported. "
            "Case-insensitive; any other value is a 400."
        )
    ),
]


def browser_visibility(value: str | None) -> str | dict[str, Any] | None:
    """``browser_visibility`` lower-cased, ``None`` when unset, or the 400 envelope."""
    if value is None:
        return None
    allowed = [v.value for v in VisibilitySetting]
    if (norm := value.lower()) not in allowed:
        return bad_param("Invalid browser_visibility", f"Expected one of {allowed}, got {value!r}.")
    return norm


def path_fields(asset: AssetLookup | None) -> dict[str, Any]:
    """Which path produced a result, as the CLI's ``--json`` reports it (Task 17)."""
    if asset is None:
        return {"route": None, "visibility": None, "retried": False}
    meta = asset.metadata_json
    transport_route = "direct" if meta.get("transport") == "direct_http" else "ui"
    return {
        "route": meta.get("route") or transport_route,
        "visibility": meta.get("visibility"),
        "retried": meta.get("retried") is True,
    }


def validate_image_ui_and_thinking(
    ui_mode: str | None, thinking_level: str | None
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    norm_ui = None
    if ui_mode is not None:
        norm_ui = ui_mode.lower()
        if norm_ui not in {m.value for m in UiMode}:
            return (
                None,
                None,
                bad_param(
                    "Invalid ui_mode",
                    f"Expected one of {[m.value for m in UiMode]}, got {norm_ui!r}.",
                ),
            )
    norm_think = None
    if thinking_level is not None:
        from gflow_cli.api.image import ThinkingLevel

        try:
            resolved = ThinkingLevel.from_cli(thinking_level)
        except ValueError:
            return (
                None,
                None,
                bad_param(
                    "Invalid thinking_level",
                    f"Expected one of {[t.value for t in ThinkingLevel]}, got {thinking_level!r}.",
                ),
            )
        norm_think = resolved.value if resolved is not None else None
    return norm_ui, norm_think, None


def build_video_payload(
    prompt: str,
    mode: str,
    aspect: str,
    count: int,
    model: str | None,
    duration: int | None,
    resolution: str | None,
    tool_specs: Any,
    project: str | None,
    project_name: str | None,
    output: str | None = None,
    ui_mode: str | None = None,
    reference_entities: list[str] | None = None,
    reference_entity_names: list[str] | None = None,
    max_credits: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"prompt": prompt, "mode": mode, "aspect": aspect, "count": count}
    names = list(reference_entity_names) if reference_entity_names else None
    payload.update(
        {
            key: value
            for key, value in (
                ("ui_mode", ui_mode),
                ("model", model),
                ("duration", duration),
                ("resolution", resolution),
                ("project_id", project),
                ("project_name", project_name),
                ("output_file", output),
                ("max_credits", max_credits),
                ("tool_specs", list(tool_specs) if tool_specs else None),
                ("reference_entities", list(reference_entities) if reference_entities else None),
                ("reference_entity_names", names),
            )
            if value is not None
        }
    )
    return payload


def upscale_route_for(
    settings: Any, profile_dir: Path, media_id: str, scale: str, kind: str
) -> Route:
    """A video upscale routes on its local catalog row (no network read); an image upscale
    drives the Flow UI. The same decision as the CLI's ``upscale_visibility``."""
    if kind != "video":
        return Route.UI
    from gflow_cli.api._flow_media_edit import upscale_catalog_row  # noqa: PLC0415

    return upscale_route(scale, upscale_catalog_row(settings, profile_dir, media_id))
