"""`gflow video` command group.

Phase B wires `t2v` to `UiAutomationTransport.generate_video` with
auto-download. `i2v` and `batch` remain stubbed pending Phase B I2V work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
from rich.console import Console

from gflow_cli._cli_helpers import (
    _make_provider_dir,
    _resolve_profile,
    run_with_handlers,
)

console = Console()

_I2V_UNAVAILABLE = (
    "[yellow]`gflow video i2v` is not yet available.[/yellow]\n"
    "I2V on UiAutomationTransport lands in a later Phase B release."
)

_BATCH_UNAVAILABLE = (
    "[yellow]`gflow video batch` is not yet available.[/yellow]\n"
    "Batch video on UiAutomationTransport lands in a later Phase B release."
)


async def _run_t2v(
    *,
    profile_dir: Path,
    prompt: str,
    aspect: str,
    out_dir: Path | None,
) -> None:
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport
    from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode

    request = GenerateVideoRequest(
        prompt=prompt,
        mode=Mode.T2V,
        aspect=Aspect.from_cli(aspect),
    )
    transport = UiAutomationTransport()
    try:
        await transport.setup(profile_dir)
        console.print("[dim]Generating video — this takes ~2 minutes…[/dim]")
        result = await transport.generate_video(
            request=request,
            out_dir=out_dir,
            download=True,
        )
    finally:
        await transport.teardown()

    if not result.status.succeeded:
        reasons = (
            ", ".join(result.status.failure_reasons)
            or result.status.error_message
            or "unknown reason"
        )
        console.print(f"[red]Video generation failed:[/red] {reasons}")
        raise SystemExit(1)

    console.print(f"[bold green]Saved:[/bold green] {result.local_path}")


async def _run_i2v(**kwargs: Any) -> None:  # pragma: no cover
    console.print(_I2V_UNAVAILABLE)
    raise SystemExit(1)


async def _run_batch(**kwargs: Any) -> None:  # pragma: no cover
    console.print(_BATCH_UNAVAILABLE)
    raise SystemExit(1)


@click.group()
def video() -> None:
    """Generate and manage videos via Google Flow Veo."""


@video.command(
    "t2v",
    short_help="Generate a video from a text prompt.",
    help=(
        "Generate a video from a text prompt using Google Flow's Veo model.\n\n"
        "\b\n"
        "Examples:\n"
        '  gflow video t2v "a golden sunset over mountains"\n'
        '  gflow video t2v "timelapse of a city" --aspect 16:9\n'
        '  gflow video t2v "portrait of a dancer" --out-dir ./videos\n'
    ),
)
@click.argument("prompt")
@click.option(
    "--aspect",
    default="9:16",
    show_default=True,
    type=click.Choice(["9:16", "16:9"]),
    help="Video aspect ratio (portrait 9:16 or landscape 16:9).",
)
@click.option("--profile", default=None, help="Profile name (overrides default).")
@click.option(
    "--out-dir",
    "out_dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to save the generated mp4. Defaults to tmp/.",
)
def t2v(prompt: str, aspect: str, profile: str | None, out_dir: Path | None) -> None:
    """Generate a video from PROMPT."""
    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    run_with_handlers(
        lambda: _run_t2v(
            profile_dir=provider_dir,
            prompt=prompt,
            aspect=aspect,
            out_dir=out_dir,
        ),
        cli_command="video t2v",
    )


@video.command("i2v")
@click.argument("image", required=False)
@click.argument("prompt", required=False)
def i2v(image: str | None, prompt: str | None) -> None:
    """Generate a video from a start image + prompt (not yet available)."""
    profile_name = _resolve_profile(None)
    provider_dir = _make_provider_dir(profile_name)
    run_with_handlers(
        lambda: _run_i2v(image=image, prompt=prompt, provider_dir=provider_dir),
        cli_command="video i2v",
    )


@video.command("batch")
@click.argument("manifest", required=False)
def batch(manifest: str | None) -> None:
    """Run a manifest of video generations (not yet available)."""
    profile_name = _resolve_profile(None)
    provider_dir = _make_provider_dir(profile_name)
    run_with_handlers(
        lambda: _run_batch(manifest=manifest, provider_dir=provider_dir),
        cli_command="video batch",
    )
