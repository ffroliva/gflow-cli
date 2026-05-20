"""`gflow video` command group — temporarily stubbed.

The HTTP video-generation path (the aisandbox-pa `video:*` routes) returns
HTTP 401 and has been retired. Video generation is being rebuilt on
`UiAutomationTransport`: Phase A delivers the T2V transport; Phase B rewires
these commands to it. See
docs/superpowers/specs/2026-05-18-ui-automation-video-generation-design.md.

`_resolve_profile`, `_make_provider_dir`, and `run_with_handlers` are imported
(not defined here) so that test monkeypatching via
``monkeypatch.setattr("gflow_cli.cli_video._resolve_profile", ...)`` works
correctly — the same pattern used by `cli_image`.
"""

from __future__ import annotations

from typing import Any

import click
from rich.console import Console

from gflow_cli._cli_helpers import (
    _make_provider_dir,
    _resolve_profile,
    run_with_handlers,
)

console = Console()

_UNAVAILABLE = (
    "[yellow]`gflow video` is temporarily unavailable.[/yellow]\n"
    "The HTTP video path returned HTTP 401 and was retired. Video generation "
    "is being rebuilt on the UI-automation transport; the `gflow video` "
    "commands return in a later release."
)


async def _run_t2v(**kwargs: Any) -> None:  # pragma: no cover
    """Placeholder — the real implementation lands in Phase B (the cli_video rewire)."""
    console.print(_UNAVAILABLE)
    raise SystemExit(1)


async def _run_i2v(**kwargs: Any) -> None:  # pragma: no cover
    """Placeholder — the real implementation lands in Phase B (the cli_video rewire)."""
    console.print(_UNAVAILABLE)
    raise SystemExit(1)


async def _run_batch(**kwargs: Any) -> None:  # pragma: no cover
    """Placeholder — the real implementation lands in Phase B (the cli_video rewire)."""
    console.print(_UNAVAILABLE)
    raise SystemExit(1)


@click.group()
def video() -> None:
    """Generate and manage videos via Google Flow Veo (temporarily unavailable)."""


@video.command("t2v")
@click.argument("prompt", required=False)
def t2v(prompt: str | None) -> None:
    """Generate a video from a text prompt (temporarily unavailable)."""
    profile_name = _resolve_profile(None)
    provider_dir = _make_provider_dir(profile_name)
    run_with_handlers(
        lambda: _run_t2v(prompt=prompt, provider_dir=provider_dir),
        cli_command="video t2v",
    )


@video.command("i2v")
@click.argument("image", required=False)
@click.argument("prompt", required=False)
def i2v(image: str | None, prompt: str | None) -> None:
    """Generate a video from a start image + prompt (temporarily unavailable)."""
    profile_name = _resolve_profile(None)
    provider_dir = _make_provider_dir(profile_name)
    run_with_handlers(
        lambda: _run_i2v(image=image, prompt=prompt, provider_dir=provider_dir),
        cli_command="video i2v",
    )


@video.command("batch")
@click.argument("manifest", required=False)
def batch(manifest: str | None) -> None:
    """Run a manifest of video generations (temporarily unavailable)."""
    profile_name = _resolve_profile(None)
    provider_dir = _make_provider_dir(profile_name)
    run_with_handlers(
        lambda: _run_batch(manifest=manifest, provider_dir=provider_dir),
        cli_command="video batch",
    )
