"""`gflow models` (list/show) — enumerate the image + video models the CLI accepts.

The catalog is assembled from the `Model` / `VideoModel` enums, their alias
maps, and the per-model reference caps, so it can never drift from what the
generation commands actually accept. ``--json`` emits it machine-readably (the
shape a UI populates model pickers + reference-slot counts from); otherwise a
Rich table is printed. Bare `gflow models` is `gflow models list`; `gflow models
show <id>` prints one catalog entry, the same object `gflow_list_models` carries.
"""

from __future__ import annotations

import difflib
from typing import Any, cast

import click
from rich.console import Console
from rich.table import Table

from gflow_cli import json_output
from gflow_cli.api import image as image_api
from gflow_cli.api import video as video_api
from gflow_cli.image_batch import ALLOWED_MODELS as _IMAGE_CLI_MODELS

console = Console()


# The CLI aliases each generation command's `--model` actually accepts. The
# catalog MUST advertise only these (a UI picks one and passes it straight to
# `image t2i --model` / `video t2v --model`); listing the broader internal
# alias set would offer values the gen commands reject (Click usage error).
#   image  → image_batch.ALLOWED_MODELS (shared with cli_image's --model Choice)
#   video  → mirror of cli_video's --model Choice (keep in sync if it changes)
_VIDEO_CLI_MODELS: tuple[str, ...] = (
    "omni-flash",
    "omni-1.2-flash",
    "omni-1.2",
    "veo-lite",
    "veo-fast",
    "veo-quality",
    "veo-lite-lp",
)

# Aspects the video generation commands' `--aspect` Choice actually accepts.
# `video_api.aspect_choices()` returns 9:16, 16:9, AND 1:1 because the underlying
# `Aspect` enum has SQUARE — but `cli_video.py`'s `t2v` / `i2v` / `r2v` `--aspect`
# options are `click.Choice(["9:16", "16:9"])` (no 1:1). Advertising 1:1 in the
# catalog would mislead a UI into passing a value the gen command rejects.
_VIDEO_CLI_ASPECTS: tuple[str, ...] = ("9:16", "16:9")


def build_catalog() -> dict[str, Any]:
    """Assemble the image + video model/aspect catalog (pure, no I/O)."""
    image_models = [
        {
            "name": m.value,
            "label": image_api.MODEL_LABELS[m],
            # Only aliases the `--model` Choice accepts, mapped back to this model.
            "aliases": [a for a in _IMAGE_CLI_MODELS if image_api.Model.from_cli(a) is m],
            "ref_cap": image_api.reference_cap_for(m),
            "default": m is image_api.Model.NARWHAL,
        }
        for m in image_api.Model
    ]
    image_aspects = [
        {"ratio": ratio, "wire": wire} for ratio, wire in image_api.aspect_choices().items()
    ]
    video_models = [
        {
            "name": m.value,
            # Only aliases the `--model` Choice accepts, mapped back to this model.
            "aliases": [a for a in _VIDEO_CLI_MODELS if video_api.VideoModel.from_cli(a) is m],
            "ref_cap": video_api.reference_cap_for(m),  # applies to r2v
            "max_duration": video_api.max_duration_for(m),
        }
        for m in video_api.VideoModel
    ]
    # Only aspects the `--aspect` Choice accepts (see _VIDEO_CLI_ASPECTS).
    video_aspects = [
        {"ratio": ratio, "wire": wire}
        for ratio, wire in video_api.aspect_choices().items()
        if ratio in _VIDEO_CLI_ASPECTS
    ]
    return {
        "image": {"models": image_models, "aspects": image_aspects},
        "video": {
            "models": video_models,
            "aspects": video_aspects,
            "tiers": [t.value for t in video_api.Tier],
        },
    }


_JSON_HELP = "Machine-readable JSON instead of a table."


@click.group("models", invoke_without_command=True)
@click.option("--json", "as_json", is_flag=True, help=_JSON_HELP)
@click.pass_context
def models(ctx: click.Context, as_json: bool) -> None:
    """List the image + video models, their aliases, and reference caps.

    Bare `gflow models` is `gflow models list`.
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(models_list, as_json=as_json)


@models.command("list")
@click.option("--json", "as_json", is_flag=True, help=_JSON_HELP)
@click.pass_context
def models_list(ctx: click.Context, as_json: bool) -> None:
    """List the image + video models, their aliases, and reference caps."""
    catalog = build_catalog()
    if as_json or _group_json(ctx):
        json_output.emit(catalog)
        return
    _render_catalog(catalog)


@models.command("show")
@click.argument("model_id")
@click.option("--json", "as_json", is_flag=True, help=_JSON_HELP)
@click.pass_context
def models_show(ctx: click.Context, model_id: str, as_json: bool) -> None:
    """Show one model by name or alias (case-insensitive)."""
    catalog = build_catalog()
    kind, entry = find_model(catalog, model_id)
    if as_json or _group_json(ctx):
        json_output.emit(entry)
        return
    _render_model(catalog, kind, entry)


def _group_json(ctx: click.Context) -> bool:
    """`gflow models --json show X` must not print a table to a script expecting JSON."""
    return bool(ctx.parent and ctx.parent.params.get("as_json"))


def find_model(catalog: dict[str, Any], model_id: str) -> tuple[str, dict[str, Any]]:
    """The catalog entry whose name or alias is *model_id*, ignoring case.

    Raises a usage error (exit 2) naming the closest ids when nothing matches.
    """
    by_id = {
        ident.lower(): (ident, kind, m)
        for kind in ("image", "video")
        for m in catalog[kind]["models"]
        for ident in (m["name"], *m["aliases"])
    }
    hit = by_id.get(model_id.lower())
    if hit is not None:
        return hit[1], hit[2]
    near = difflib.get_close_matches(model_id.lower(), list(by_id), n=3, cutoff=0.6)
    hint = f" Did you mean: {', '.join(by_id[n][0] for n in near)}?" if near else ""
    raise click.UsageError(f"no model named {model_id!r}.{hint} See `gflow models list`.")


def _render_model(catalog: dict[str, Any], kind: str, entry: dict[str, Any]) -> None:
    table = Table(title=f"{kind} model {entry['name']}", show_header=False)
    table.add_column("field", style="bold")
    table.add_column("value", overflow="fold")
    for key, value in entry.items():
        shown = ", ".join(cast(list[str], value)) if isinstance(value, list) else str(value)
        table.add_row(key, shown)
    section = catalog[kind]
    table.add_row("aspects", ", ".join(a["ratio"] for a in section["aspects"]))
    if "tiers" in section:
        table.add_row("tiers", ", ".join(section["tiers"]))
    console.print(table)


def _render_catalog(catalog: dict[str, Any]) -> None:
    for kind in ("image", "video"):
        section = catalog[kind]
        table = Table(title=f"{kind} models")
        table.add_column("model", style="bold")
        if kind == "image":
            table.add_column("Flow name")
        table.add_column("aliases", overflow="fold")
        table.add_column("ref cap", justify="right")
        if kind == "video":
            table.add_column("max dur (s)", justify="right")
        for m in section["models"]:
            row = [m["name"], ", ".join(m["aliases"]), str(m["ref_cap"])]
            if kind == "image":
                row.insert(1, m["label"])
            if kind == "video":
                row.append(str(m["max_duration"]))
            table.add_row(*row)
        console.print(table)
        ratios = ", ".join(a["ratio"] for a in section["aspects"])
        console.print(f"  aspects: {ratios}\n")
