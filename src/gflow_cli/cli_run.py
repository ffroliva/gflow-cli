"""``gflow run`` — execute a JSON-described batch of image generations.

A batch config is a JSON file with a top-level ``prompts`` list. Each prompt
produces one or more PNGs in ``output_dir``. Prompts run sequentially through
a single :class:`FlowApiClient` session so the Playwright browser and Flow
project context persist across the loop (per-prompt costs are essentially
just the per-call reCAPTCHA mint + ``batchGenerateImages`` round-trip).

See ``docs/superpowers/plans/2026-05-12-gflow-cli-d2-continuation/AUDIT_E1.md``
Section D for the formal schema.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import click
from rich.console import Console

from gflow_cli._cli_helpers import _make_provider_dir, _resolve_profile
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports import EXPERIMENTAL_TRANSPORTS
from gflow_cli.config import get_settings
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.errors import ConfigurationError
from gflow_cli.image_batch import (
    MAX_PROMPTS,
    MIN_PROMPTS,
    BatchOutcome,
    BatchPromptItem,
    parse_batch_item_dict,
    render_image_batch_summary,
    resolve_exit_code,
    run_image_batch,
)
from gflow_cli.paths import resolve_batch_output_dir

console = Console()


_ALLOWED_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {"profile", "transport", "output_dir", "prompts"},
)
# ---------------------------------------------------------------------------
# Dataclasses — validated config + per-prompt outcome.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BatchConfig:
    """Parsed + validated batch config."""

    prompts: tuple[BatchPromptItem, ...]
    profile: str | None = None
    transport: str | None = None
    output_dir: str | None = None

    @classmethod
    def from_json_path(cls, path: Path) -> BatchConfig:
        """Parse, validate, and return a BatchConfig.

        Raises :class:`ConfigurationError` on any validation failure with a
        message that points at the offending key.
        """
        if not path.exists():
            msg = f"Config file not found: {path}"
            raise ConfigurationError(msg)
        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError as e:
            msg = f"Failed to read {path}: {e}"
            raise ConfigurationError(msg) from e
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as e:
            msg = f"Failed to parse {path}: line {e.lineno}:{e.colno} {e.msg}"
            raise ConfigurationError(
                msg,
            ) from e
        if not isinstance(data, dict):
            msg = f"Config root must be a JSON object, got {type(data).__name__}."
            raise ConfigurationError(
                msg,
            )
        return cls._from_dict(cast("dict[str, Any]", data))

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> BatchConfig:
        unknown = set(data) - _ALLOWED_TOP_LEVEL_KEYS
        if unknown:
            msg = (
                f"Unknown key(s) {sorted(unknown)!r} in config. "
                f"Valid: {sorted(_ALLOWED_TOP_LEVEL_KEYS)!r}."
            )
            raise ConfigurationError(
                msg,
            )
        prompts_raw_obj = data.get("prompts")
        if not isinstance(prompts_raw_obj, list):
            msg = "'prompts' must be a JSON array."
            raise ConfigurationError(msg)
        prompts_raw = cast("list[Any]", prompts_raw_obj)

        if not (MIN_PROMPTS <= len(prompts_raw) <= MAX_PROMPTS):
            msg = (
                f"'prompts' must have between {MIN_PROMPTS} and "
                f"{MAX_PROMPTS} entries (got {len(prompts_raw)})."
            )
            raise ConfigurationError(
                msg,
            )
        prompts: list[BatchPromptItem] = []
        for idx, p in enumerate(prompts_raw):
            if not isinstance(p, dict):
                msg = f"prompts[{idx}] must be a JSON object."
                raise ConfigurationError(msg)
            prompts.append(parse_batch_item_dict(cast("dict[str, Any]", p), idx))

        profile = data.get("profile")
        if profile is not None and (not isinstance(profile, str) or not profile):
            msg = "'profile' must be a non-empty string."
            raise ConfigurationError(msg)
        transport = data.get("transport")
        if transport is not None and (not isinstance(transport, str) or not transport):
            msg = "'transport' must be a non-empty string."
            raise ConfigurationError(msg)
        output_dir = data.get("output_dir")
        if output_dir is not None and (not isinstance(output_dir, str) or not output_dir):
            msg = "'output_dir' must be a non-empty string."
            raise ConfigurationError(msg)

        return cls(
            prompts=tuple(prompts),
            profile=profile,
            transport=transport,
            output_dir=output_dir,
        )


# ---------------------------------------------------------------------------
# Helpers — experimental-transport gating.
# ---------------------------------------------------------------------------


def _check_transport_gated(transport: str | None) -> None:
    """Per spec D.1 rule 5: experimental transports require env var.

    Raises :class:`ConfigurationError` if the named transport is in
    :data:`EXPERIMENTAL_TRANSPORTS` and ``GFLOW_CLI_EXPERIMENTAL_TRANSPORTS``
    is unset.
    """
    if transport is None or transport not in EXPERIMENTAL_TRANSPORTS:
        return
    import os

    if os.getenv("GFLOW_CLI_EXPERIMENTAL_TRANSPORTS") != "1":
        msg = (
            f"Transport {transport!r} is experimental. "
            f"Set GFLOW_CLI_EXPERIMENTAL_TRANSPORTS=1 to enable."
        )
        raise ConfigurationError(
            msg,
        )


# ---------------------------------------------------------------------------
# Core orchestration — sequential per-prompt loop on one client session.
# ---------------------------------------------------------------------------


async def _run_batch(
    *,
    profile_dir: Path,
    headless: bool,
    transport: str | None,
    prompts: tuple[BatchPromptItem, ...],
    output_dir: Path,
    continue_on_error: bool,
    profile_name: str | None = None,
    recorder: OperationRecorder | None = None,
) -> list[BatchOutcome]:
    """Run ``prompts`` sequentially through ONE FlowApiClient session.

    Per AUDIT_E1 D.2: a single ``async with FlowApiClient(...)`` block
    wraps the whole loop so the browser/page/project context persists
    across iterations. reCAPTCHA tokens mint fresh on each
    ``generate_image`` call.
    """
    outcomes = await run_image_batch(
        profile_dir=profile_dir,
        headless=headless,
        transport=transport,
        prompts=prompts,
        output_dir=output_dir,
        continue_on_error=continue_on_error,
        project_title="gflow-cli run",
        client_factory=FlowApiClient,
        _profile_name=profile_name,
        _recorder=recorder,
        _command="run",
    )
    return outcomes


# ---------------------------------------------------------------------------
# Click command — `gflow run --config <file>`
# ---------------------------------------------------------------------------


@click.command("run")
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to a JSON batch config (see AUDIT_E1 § D for the schema).",
)
@click.option(
    "--output-dir",
    "output_dir_override",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Override output_dir from the config.",
)
@click.option(
    "--profile",
    default=None,
    help="Profile name (overrides default and any 'profile' in the config).",
)
@click.option(
    "--continue-on-error/--fail-fast",
    default=True,
    show_default=True,
    help=(
        "On per-prompt failure: --continue-on-error attempts the remaining "
        "prompts (default); --fail-fast halts the batch immediately."
    ),
)
def run(
    config_path: Path,
    output_dir_override: Path | None,
    profile: str | None,
    continue_on_error: bool,
) -> None:
    """Execute a JSON-described batch of image generations sequentially."""
    try:
        cfg = BatchConfig.from_json_path(config_path)
        _check_transport_gated(cfg.transport)
    except ConfigurationError as e:
        console.print(f"[red]Config error:[/red] {e}")
        sys.exit(resolve_exit_code(e))

    profile_name = _resolve_profile(profile or cfg.profile)
    provider_dir = _make_provider_dir(profile_name)
    settings = get_settings()
    output_dir = resolve_batch_output_dir(
        cli_override=output_dir_override,
        config_value=cfg.output_dir,
        output_root=settings.output_dir,
    )

    console.print(
        f"\n[bold]gflow run[/bold] · profile=[bold]{profile_name}[/bold] "
        f"· transport=[bold]{cfg.transport or '(default)'}[/bold] "
        f"· {len(cfg.prompts)} prompt(s)",
    )
    console.print(f"  output_dir: [dim]{output_dir}[/dim]")
    if not continue_on_error:
        console.print("  mode: [yellow]fail-fast[/yellow]")

    recorder = OperationRecorder.open(settings)
    try:
        outcomes = asyncio.run(
            _run_batch(
                profile_dir=provider_dir,
                headless=settings.headless,
                transport=cfg.transport,
                prompts=cfg.prompts,
                output_dir=output_dir,
                continue_on_error=continue_on_error,
                profile_name=profile_name,
                recorder=recorder,
            ),
        )
    finally:
        recorder.close()
    exit_code = render_image_batch_summary(outcomes, title="gflow run")
    if exit_code != 0:
        sys.exit(exit_code)
