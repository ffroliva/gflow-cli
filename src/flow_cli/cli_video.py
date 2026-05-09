"""`gflow video` command group — t2v subcommand (text-to-video).

Helper functions `_resolve_profile` and `_make_provider_dir` are thin wrappers
over the same profile/auth machinery used by the rest of cli.py, kept as
named module-level functions so the test suite can patch them cleanly.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
from rich.console import Console

from flow_cli import auth as auth_mod
from flow_cli import profile_store
from flow_cli.api.client import FlowApiClient
from flow_cli.api.video import Aspect, GenerateVideoRequest
from flow_cli.paths import video_output_path

console = Console()

# Terminal statuses that end the polling loop.
_TERMINAL = frozenset(
    [
        "MEDIA_GENERATION_STATUS_COMPLETED",
        "MEDIA_GENERATION_STATUS_FAILED",
    ]
)


def _resolve_profile(profile: str | None) -> str:
    """Return the active profile name or exit with a friendly message."""
    if profile:
        return profile
    try:
        return profile_store.resolve_profile(None)
    except profile_store.NoProfilesError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        sys.exit(2)
    except profile_store.NoDefaultProfileError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        sys.exit(2)


def _make_provider_dir(profile_name: str) -> Path:
    """Return the Playwright profile dir for *profile_name*, or exit if absent."""
    pdir = auth_mod.profile_dir(profile_name)
    if not pdir.exists():
        console.print(
            f"[red]No session for profile '{profile_name}'.[/red] "
            "Run [bold]gflow auth login[/bold] first."
        )
        sys.exit(2)
    return pdir


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@click.group()
def video() -> None:
    """Generate and manage videos via Google Flow Veo."""


# ---------------------------------------------------------------------------
# t2v subcommand
# ---------------------------------------------------------------------------


@video.command("t2v")
@click.argument("prompt")
@click.option(
    "-o",
    "--output",
    "output",
    default=None,
    type=click.Path(path_type=Path),
    help="Where to save the mp4. Defaults to <output_dir>/videos/<date>/<media>.mp4.",
)
@click.option(
    "--aspect",
    default="9:16",
    show_default=True,
    type=click.Choice(["9:16", "16:9", "1:1"]),
    help="Video aspect ratio.",
)
@click.option("--seed", default=None, type=int, help="RNG seed for reproducibility.")
@click.option("--profile", default=None, help="Profile name (overrides default).")
@click.option(
    "--poll-interval",
    default=5,
    show_default=True,
    type=int,
    help="Seconds between status polls.",
)
def t2v(
    prompt: str,
    output: Path | None,
    aspect: str,
    seed: int | None,
    profile: str | None,
    poll_interval: int,
) -> None:
    """Generate a video from PROMPT (text-to-video)."""

    async def _run() -> None:
        profile_name = _resolve_profile(profile)
        provider_dir = _make_provider_dir(profile_name)

        aspect_vo = Aspect.from_cli(aspect)
        req = GenerateVideoRequest(prompt=prompt, aspect=aspect_vo)

        async with FlowApiClient(provider_dir) as client:
            project = await client.create_project()
            console.print(f"  Project: {project.project_id}")

            op = await client.generate_video(project_id=project.project_id, req=req, seed=seed)
            console.print(f"  Operation: {op.operation_name}")

            # Poll until terminal.
            while True:
                statuses = await client.get_video_status(project.project_id, [op.media_name])
                if not statuses:
                    console.print("[red]No status returned from API.[/red]")
                    sys.exit(1)
                st = statuses[0]
                console.print(f"  Status: {st.status}")
                if st.status in _TERMINAL:
                    break
                await asyncio.sleep(poll_interval)

            if not st.succeeded:
                console.print(f"[red]Generation failed (status={st.status}).[/red]")
                sys.exit(1)

            # Resolve output path.
            if output is None:
                from flow_cli.config import get_settings

                out_path = video_output_path(get_settings().output_dir, job_id=op.media_name)
            else:
                out_path = output

            saved = await client.download(op.media_name, out_path)
            console.print(f"[green]Saved[/green] {saved}")

    asyncio.run(_run())
