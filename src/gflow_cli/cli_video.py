"""`gflow video` command group.

`t2v` and `i2v` drive `UiAutomationTransport.generate_video` with auto-download.
`batch` remains stubbed pending a manifest-driven runner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
import structlog
from rich.console import Console

from gflow_cli._cli_helpers import (
    _make_provider_dir,
    _resolve_profile,
    run_with_handlers,
)
from gflow_cli.config import get_settings
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.errors import DataStoreError

console = Console()
logger = structlog.get_logger(__name__)


def _warn_persistence_failed_after_success(
    *,
    exc: Exception,
    flow_media_id: str | None,
    local_path: Path | None,
) -> None:
    logger.warning(
        "data.persistence_failed_after_success",
        error_class=type(exc).__name__,
        flow_media_id=flow_media_id,
        local_path=str(local_path) if local_path is not None else None,
    )


_BATCH_UNAVAILABLE = (
    "[yellow]`gflow video batch` is not yet available.[/yellow]\n"
    "Batch video on UiAutomationTransport lands in a later release."
)


async def _generate_and_report(
    request: Any,
    *,
    profile_name: str,
    profile_dir: Path,
    out_dir: Path | None,
) -> None:
    """Drive FlowApiClient for a single GenerateVideoRequest and print the
    result (or fail with a non-zero exit). Shared by t2v, i2v, and r2v."""
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.video import VideoStarted

    console.print("[dim]Generating video — this takes ~2 minutes…[/dim]")
    settings = get_settings()
    recorder = OperationRecorder.open(settings)
    try:
        async with FlowApiClient(profile_dir=profile_dir, out_dir=out_dir) as client:

            def on_started(started: VideoStarted) -> None:
                try:
                    recorder.record_started_video(
                        profile_name=profile_name,
                        profile_dir=profile_dir,
                        request=request,
                        started=started,
                    )
                except DataStoreError as exc:
                    _warn_persistence_failed_after_success(
                        exc=exc,
                        flow_media_id=started.media_id,
                        local_path=None,
                    )

            result = await client.generate_video(
                req=request,
                out_dir=out_dir,
                download=True,
                on_started=on_started,
            )

        try:
            recorder.record_completed_video(
                profile_name=profile_name,
                _profile_dir=profile_dir,
                request=request,
                result=result,
            )
        except DataStoreError as exc:
            _warn_persistence_failed_after_success(
                exc=exc,
                flow_media_id=result.status.media_id,
                local_path=result.local_path,
            )
    finally:
        recorder.close()

    if not result.status.succeeded:
        reasons = (
            ", ".join(result.status.failure_reasons)
            or result.status.error_message
            or "unknown reason"
        )
        console.print(f"[red]Video generation failed:[/red] {reasons}")
        raise SystemExit(1)

    console.print(f"[bold green]Saved:[/bold green] {result.local_path}")


async def _run_t2v(
    *,
    profile_name: str,
    profile_dir: Path,
    prompt: str,
    aspect: str,
    out_dir: Path | None,
    model: str | None = None,
    duration: int | None = None,
    count: int = 1,
) -> None:
    from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel

    request = GenerateVideoRequest(
        prompt=prompt,
        mode=Mode.T2V,
        aspect=Aspect.from_cli(aspect),
        model=VideoModel.from_cli(model),
        duration=duration,
        count=count,
    )
    await _generate_and_report(
        request, profile_name=profile_name, profile_dir=profile_dir, out_dir=out_dir
    )


async def _run_i2v(
    *,
    profile_name: str,
    profile_dir: Path,
    image: str,
    prompt: str,
    aspect: str,
    out_dir: Path | None,
    end_image: str | None = None,
    model: str | None = None,
    duration: int | None = None,
    count: int = 1,
) -> None:
    from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel

    request = GenerateVideoRequest(
        prompt=prompt,
        mode=Mode.I2V,
        aspect=Aspect.from_cli(aspect),
        model=VideoModel.from_cli(model),
        duration=duration,
        count=count,
        start_image=Path(image),
        end_image=Path(end_image) if end_image else None,
    )
    await _generate_and_report(
        request, profile_name=profile_name, profile_dir=profile_dir, out_dir=out_dir
    )


async def _run_r2v(
    *,
    profile_name: str,
    profile_dir: Path,
    prompt: str,
    refs: tuple[str, ...],
    aspect: str,
    out_dir: Path | None,
    model: str | None = None,
    duration: int | None = None,
    count: int = 1,
) -> None:
    from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel

    request = GenerateVideoRequest(
        prompt=prompt,
        mode=Mode.R2V,
        aspect=Aspect.from_cli(aspect),
        model=VideoModel.from_cli(model),
        duration=duration,
        count=count,
        reference_images=tuple(Path(r) for r in refs),
    )
    await _generate_and_report(
        request, profile_name=profile_name, profile_dir=profile_dir, out_dir=out_dir
    )


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
@click.option(
    "--model",
    default=None,
    type=click.Choice(["omni-flash", "veo-lite", "veo-fast", "veo-quality", "veo-lite-lp"]),
    help="Veo model. Omit to use Flow's current default. Only omni-flash supports --duration 10.",
)
@click.option(
    "--duration",
    default=None,
    type=click.Choice(["4", "6", "8", "10"]),
    help="Clip length in seconds. 10 requires --model omni-flash. Omit for Flow's default.",
)
@click.option(
    "--count",
    default=1,
    show_default=True,
    type=click.IntRange(1, 4),
    help="How many videos to generate (1-4). >1 multiplies credit cost.",
)
@click.option("--profile", default=None, help="Profile name (overrides default).")
@click.option(
    "--out-dir",
    "out_dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to save the generated mp4. Defaults to tmp/.",
)
def t2v(
    prompt: str,
    aspect: str,
    model: str | None,
    duration: str | None,
    count: int,
    profile: str | None,
    out_dir: Path | None,
) -> None:
    """Generate a video from PROMPT."""
    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    run_with_handlers(
        lambda: _run_t2v(
            profile_name=profile_name,
            profile_dir=provider_dir,
            prompt=prompt,
            aspect=aspect,
            out_dir=out_dir,
            model=model,
            duration=int(duration) if duration is not None else None,
            count=count,
        ),
        cli_command="video t2v",
    )


@video.command(
    "i2v",
    short_help="Generate a video from a start image + motion prompt.",
    help=(
        "Image-to-video: animate a start image with a motion prompt (Veo).\n\n"
        "\b\n"
        "Examples:\n"
        '  gflow video i2v hero.png "slow cinematic push-in"\n'
        '  gflow video i2v hero.png "pan left" --end-image last.png --aspect 16:9\n'
        '  gflow video i2v cat.png "it leaps" --model veo-quality --duration 8\n'
    ),
)
@click.argument("image")
@click.argument("prompt")
@click.option(
    "--end-image",
    "end_image",
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    help="Optional end frame — Flow interpolates start -> end.",
)
@click.option(
    "--aspect",
    default="9:16",
    show_default=True,
    type=click.Choice(["9:16", "16:9"]),
    help="Video aspect ratio.",
)
@click.option(
    "--model",
    default=None,
    type=click.Choice(["omni-flash", "veo-lite", "veo-fast", "veo-quality", "veo-lite-lp"]),
    help="Veo model. Omit to use Flow's current default.",
)
@click.option(
    "--duration",
    default=None,
    type=click.Choice(["4", "6", "8", "10"]),
    help="Clip length in seconds. 10 requires --model omni-flash.",
)
@click.option(
    "--count",
    default=1,
    show_default=True,
    type=click.IntRange(1, 4),
    help="How many videos to generate (1-4).",
)
@click.option("--profile", default=None, help="Profile name (overrides default).")
@click.option(
    "--out-dir",
    "out_dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to save the generated mp4. Defaults to tmp/.",
)
def i2v(
    image: str,
    prompt: str,
    end_image: str | None,
    aspect: str,
    model: str | None,
    duration: str | None,
    count: int,
    profile: str | None,
    out_dir: Path | None,
) -> None:
    """Generate a video from a start IMAGE + motion PROMPT."""
    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    run_with_handlers(
        lambda: _run_i2v(
            profile_name=profile_name,
            profile_dir=provider_dir,
            image=image,
            prompt=prompt,
            end_image=end_image,
            aspect=aspect,
            model=model,
            duration=int(duration) if duration is not None else None,
            count=count,
            out_dir=out_dir,
        ),
        cli_command="video i2v",
    )


@video.command(
    "r2v",
    short_help="Generate a video from reference images + prompt (ingredients).",
    help=(
        "Reference-to-video: condition a generation on 1-3 reference images "
        "(Flow's 'ingredients' / Elementos).\n\n"
        "\b\n"
        "Examples:\n"
        '  gflow video r2v "a knight in this armor walks forward" --ref armor.png\n'
        '  gflow video r2v "they meet" --ref a.png --ref b.png --aspect 16:9\n'
    ),
)
@click.argument("prompt")
@click.option(
    "--ref",
    "refs",
    multiple=True,
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    help="Reference image (repeat for up to 3).",
)
@click.option(
    "--aspect",
    default="9:16",
    show_default=True,
    type=click.Choice(["9:16", "16:9"]),
    help="Video aspect ratio.",
)
@click.option(
    "--model",
    default=None,
    type=click.Choice(["omni-flash", "veo-lite", "veo-fast", "veo-quality", "veo-lite-lp"]),
    help="Veo model. Omit to use Flow's current default.",
)
@click.option(
    "--duration",
    default=None,
    type=click.Choice(["4", "6", "8", "10"]),
    help="Clip length in seconds. 10 requires --model omni-flash.",
)
@click.option(
    "--count",
    default=1,
    show_default=True,
    type=click.IntRange(1, 4),
    help="How many videos to generate (1-4).",
)
@click.option("--profile", default=None, help="Profile name (overrides default).")
@click.option(
    "--out-dir",
    "out_dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to save the generated mp4. Defaults to tmp/.",
)
def r2v(
    prompt: str,
    refs: tuple[str, ...],
    aspect: str,
    model: str | None,
    duration: str | None,
    count: int,
    profile: str | None,
    out_dir: Path | None,
) -> None:
    """Generate a video from reference images (--ref) + PROMPT."""
    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    run_with_handlers(
        lambda: _run_r2v(
            profile_name=profile_name,
            profile_dir=provider_dir,
            prompt=prompt,
            refs=refs,
            aspect=aspect,
            model=model,
            duration=int(duration) if duration is not None else None,
            count=count,
            out_dir=out_dir,
        ),
        cli_command="video r2v",
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
