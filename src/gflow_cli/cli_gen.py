"""`gflow image generate` / `gflow video generate`: bridge verbs that infer the mode (#1193).

Each picks the mode verb from its inputs, then runs exactly that verb (see
``_cli_helpers.run_bridged``). Kept out of ``cli_image`` / ``cli_video`` so neither grows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from gflow_cli._cli_helpers import BridgeCommand, bridge_params, run_bridged, spends_credits
from gflow_cli.cli_image import i2i, t2i
from gflow_cli.cli_video import i2v, r2v, t2v
from gflow_cli.tools.runtime import IMAGE_EXTS


def resolve_image_mode(*, refs: bool) -> tuple[click.Command, str]:
    """Routing table for `image generate` (#1193): a reference image makes it i2i."""
    return (i2i, "--ref was given") if refs else (t2i, "no --ref")


@spends_credits(True)  # resolves to t2i/i2i, both spend the image quota
@click.command(
    "generate",
    cls=BridgeCommand,
    short_help="Generate image(s); picks t2i or i2i from the inputs.",
    help=(
        "Generate image(s) without naming the mode: a bridge verb that resolves to a "
        "mode verb from its inputs, then runs exactly that verb (same options, output "
        'and exit codes; --json adds "mode").\n\n'
        "\b\n"
        "Resolves to:\n"
        "  PROMPT...            -> gflow image t2i\n"
        "  PROMPT --ref IMAGE   -> gflow image i2i\n\n"
        "The options below are the union of both verbs; their defaults and limits are "
        "the resolved verb's (see its --help). An option the resolved verb does not take "
        "is a usage error (exit 2). The mode verbs stay; use them when you want the "
        "mode fixed.\n\n"
        "\b\n"
        "Examples:\n"
        '  gflow image generate "a serene mountain lake at dawn"\n'
        '  gflow image generate "make it cinematic" --ref hero.png'
    ),
)
@click.argument("prompts", nargs=-1, required=False)
@click.pass_context
def image_gen(ctx: click.Context, positionals: tuple[str, ...], **values: Any) -> None:
    """Route to `image t2i` or `image i2i` (#1193)."""
    mode, why = resolve_image_mode(refs=bool(values["refs"]))
    run_bridged(ctx, mode, why)


bridge_params(
    image_gen,
    t2i,
    i2i,
    help_for={
        "refs": (
            "Reference image: a local path (auto-uploaded) or the media UUID of an "
            "uploaded asset. Repeatable. Giving one resolves to `image i2i`."
        ),
        "reference_entities": (
            "Flow CHARACTER entity id to reference for consistency (repeatable). The "
            "entity must live in the --project you target. Single-prompt only."
        ),
        "ui_mode": (
            "Which Flow UI arm to require: 'classic', 'agentic' (needed for -i) or "
            "'auto' (default). Overrides GFLOW_CLI_UI_MODE. Single-prompt only."
        ),
    },
)


def resolve_video_mode(*, initial: bool, end: bool, refs: bool) -> tuple[click.Command, str]:
    """Routing table for `video generate` (#1193): frames -> i2v, --ref -> r2v, neither -> t2v.

    A frame and refs together, or an end frame without a start, is a usage error, never a
    guess.
    """
    if (initial or end) and refs:
        msg = (
            "A frame (--initial-frame/--end-frame) and --ref were both given: that is either "
            "`gflow video i2v` (animate a frame) or `gflow video r2v` (reference images), "
            "not both. Call the mode verb you mean."
        )
        raise click.UsageError(msg)
    if end and not initial:
        msg = "--end-frame needs --initial-frame (that is `gflow video i2v`)."
        raise click.UsageError(msg)
    if initial:
        return i2v, "--initial-frame was given"
    return (r2v, "--ref was given") if refs else (t2v, "no frame and no --ref")


def _refuse_an_image_as_the_prompt(positionals: tuple[str, ...]) -> None:
    """`video generate hero.png` would bill a t2v clip whose prompt is the file name."""
    for value in positionals:
        path = Path(value)
        try:  # an unstat-able prompt (ENAMETOOLONG on POSIX < 3.13) is just a prompt
            is_image = path.suffix.lower() in IMAGE_EXTS and path.is_file()
        except OSError:
            is_image = False
        if is_image:
            others = [v for v in positionals if v != value]
            prompt = f'"{others[0]}"' if len(others) == 1 else "PROMPT"
            msg = (
                f"{value} is an image file, not a prompt. To animate it, pass it as the "
                f"start frame: gflow video generate --initial-frame {value} {prompt}"
            )
            raise click.UsageError(msg)


@spends_credits(True)  # resolves to t2v/i2v/r2v, all spend Veo credits
@click.command(
    "generate",
    cls=BridgeCommand,
    short_help="Generate a video; picks t2v, i2v or r2v from the inputs.",
    help=(
        "Generate a video without naming the mode: a bridge verb that resolves to a "
        "mode verb from its inputs, then runs exactly that verb (same options, output "
        'and exit codes; --json adds "mode").\n\n'
        "\b\n"
        "Resolves to:\n"
        "  PROMPT                                -> gflow video t2v\n"
        "  PROMPT --initial-frame F [--end-frame E] -> gflow video i2v\n"
        "  PROMPT --ref IMAGE                    -> gflow video r2v\n\n"
        "The options below are the union of the three verbs; their defaults and limits "
        "are the resolved verb's (see its --help). A frame AND --ref, --end-frame without "
        "--initial-frame, or an option the resolved verb does not take is a usage error "
        "(exit 2). A character by id (--reference-entity) without --ref is passed to "
        "`video t2v` unchanged. The mode verbs stay; use them when you want the mode "
        "fixed.\n\n"
        "\b\n"
        "Examples:\n"
        '  gflow video generate "a golden sunset over mountains"\n'
        '  gflow video generate "slow pan left" --initial-frame hero.png\n'
        '  gflow video generate "knight walks forward" --ref armor.png --model omni-flash'
    ),
)
@click.argument("prompt")
@click.pass_context
def video_gen(ctx: click.Context, positionals: tuple[str, ...], **values: Any) -> None:
    """Route to `video t2v`, `video i2v` or `video r2v` (#1193)."""
    _refuse_an_image_as_the_prompt(positionals)
    mode, why = resolve_video_mode(
        initial=values["initial_frame"] is not None,
        end=values["end_frame"] is not None,
        refs=bool(values["refs"]),
    )
    run_bridged(ctx, mode, why)


bridge_params(
    video_gen,
    t2v,
    i2v,
    r2v,
    exclude=("end_image_deprecated",),
    help_for={
        "model": (
            "Video model. Omit for the resolved verb's default (`video i2v` uses "
            "veo-lite; see each verb's --help)."
        ),
    },
)
