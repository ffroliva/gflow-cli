"""`gflow models` — enumerate the image + video models the CLI accepts.

The catalog is assembled from the `Model` / `VideoModel` enums, their alias
maps, and the per-model reference caps, so it can never drift from what the
generation commands actually accept. ``--json`` emits it machine-readably (the
shape a UI populates model pickers + reference-slot counts from); otherwise a
Rich table is printed.
"""

from __future__ import annotations

from typing import Any

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


@click.command("models")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Machine-readable JSON catalog instead of a table.",
)
def models(as_json: bool) -> None:
    """List the image + video models, their aliases, and reference caps."""
    catalog = build_catalog()
    if as_json:
        json_output.emit(catalog)
        return
    _render_catalog(catalog)


def _render_catalog(catalog: dict[str, Any]) -> None:
    for kind in ("image", "video"):
        section = catalog[kind]
        table = Table(title=f"{kind} models")
        table.add_column("model", style="bold")
        table.add_column("aliases", overflow="fold")
        table.add_column("ref cap", justify="right")
        if kind == "video":
            table.add_column("max dur (s)", justify="right")
        for m in section["models"]:
            row = [m["name"], ", ".join(m["aliases"]), str(m["ref_cap"])]
            if kind == "video":
                row.append(str(m["max_duration"]))
            table.add_row(*row)
        console.print(table)
        ratios = ", ".join(a["ratio"] for a in section["aspects"])
        console.print(f"  aspects: {ratios}\n")
