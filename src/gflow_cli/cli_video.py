"""`gflow video` command group — temporarily stubbed.

The HTTP video-generation path (the aisandbox-pa `video:*` routes) returns
HTTP 401 and has been retired. Video generation is being rebuilt on
`UiAutomationTransport`: Phase A delivers the T2V transport; Phase B rewires
these commands to it. See
docs/superpowers/specs/2026-05-18-ui-automation-video-generation-design.md.
"""

from __future__ import annotations

import click
from rich.console import Console

console = Console()

_UNAVAILABLE = (
    "[yellow]`gflow video` is temporarily unavailable.[/yellow]\n"
    "The HTTP video path returned HTTP 401 and was retired. Video generation "
    "is being rebuilt on the UI-automation transport; the `gflow video` "
    "commands return in a later release."
)


@click.group()
def video() -> None:
    """Generate and manage videos via Google Flow Veo (temporarily unavailable)."""


@video.command("t2v")
@click.argument("prompt", required=False)
def t2v(prompt: str | None) -> None:
    """Generate a video from a text prompt (temporarily unavailable)."""
    _ = prompt
    console.print(_UNAVAILABLE)
    raise SystemExit(1)


@video.command("i2v")
@click.argument("image", required=False)
@click.argument("prompt", required=False)
def i2v(image: str | None, prompt: str | None) -> None:
    """Generate a video from a start image + prompt (temporarily unavailable)."""
    _ = (image, prompt)
    console.print(_UNAVAILABLE)
    raise SystemExit(1)


@video.command("batch")
@click.argument("manifest", required=False)
def batch(manifest: str | None) -> None:
    """Run a manifest of video generations (temporarily unavailable)."""
    _ = manifest
    console.print(_UNAVAILABLE)
    raise SystemExit(1)
