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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import click
import structlog
from rich.console import Console
from rich.table import Table

from gflow_cli._cli_helpers import _make_provider_dir, _resolve_profile
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.api.transports import EXPERIMENTAL_TRANSPORTS
from gflow_cli.config import get_settings
from gflow_cli.errors import EXIT_CODE_MAP, ConfigurationError, GFlowError

if TYPE_CHECKING:
    from gflow_cli.api.dto import GeneratedImage

log = structlog.get_logger(__name__)
console = Console()


# Schema enums — mirror the spec D.1 schema. Source of truth for both
# validation and the user-facing error messages.
_ALLOWED_ASPECT_RATIOS: tuple[str, ...] = ("9:16", "16:9", "1:1", "4:3", "3:4")
_ALLOWED_MODELS: tuple[str, ...] = ("nano2", "nano-pro", "imagen4")
_ALLOWED_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {"profile", "transport", "output_dir", "prompts"}
)
_ALLOWED_PROMPT_KEYS: frozenset[str] = frozenset(
    {"text", "aspect_ratio", "model", "count", "output_filename"}
)
_MIN_PROMPTS = 1
_MAX_PROMPTS = 50
_MIN_TEXT_LEN = 1
_MAX_TEXT_LEN = 2000
_MIN_COUNT = 1
_MAX_COUNT = 4
_DEFAULT_ASPECT_RATIO = "9:16"
_DEFAULT_MODEL = "nano2"
_DEFAULT_COUNT = 1


def _resolve_exit_code(exc: GFlowError) -> int:
    """Map ``exc`` to its CLI exit code via isinstance walk on EXIT_CODE_MAP.

    Most-specific subclass wins (the dict is ordered most-specific-first
    in errors.py). Falls back to 1 for unmapped exception classes.
    """
    for cls, code in EXIT_CODE_MAP.items():
        if isinstance(exc, cls):
            return code
    return 1


# ---------------------------------------------------------------------------
# Dataclasses — validated config + per-prompt outcome.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BatchPromptItem:
    """One prompt entry from the batch config."""

    text: str
    aspect_ratio: str = _DEFAULT_ASPECT_RATIO
    model: str = _DEFAULT_MODEL
    count: int = _DEFAULT_COUNT
    output_filename: str | None = None


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
            raise ConfigurationError(f"Config file not found: {path}")
        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise ConfigurationError(f"Failed to read {path}: {e}") from e
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as e:
            raise ConfigurationError(
                f"Failed to parse {path}: line {e.lineno}:{e.colno} {e.msg}"
            ) from e
        if not isinstance(data, dict):
            raise ConfigurationError(
                f"Config root must be a JSON object, got {type(data).__name__}."
            )
        return cls._from_dict(cast("dict[str, Any]", data))

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> BatchConfig:
        unknown = set(data) - _ALLOWED_TOP_LEVEL_KEYS
        if unknown:
            raise ConfigurationError(
                f"Unknown key(s) {sorted(unknown)!r} in config. "
                f"Valid: {sorted(_ALLOWED_TOP_LEVEL_KEYS)!r}."
            )
        prompts_raw_obj = data.get("prompts")
        if not isinstance(prompts_raw_obj, list):
            raise ConfigurationError("'prompts' must be a JSON array.")
        prompts_raw = cast("list[Any]", prompts_raw_obj)
        if not (_MIN_PROMPTS <= len(prompts_raw) <= _MAX_PROMPTS):
            raise ConfigurationError(
                f"'prompts' must have between {_MIN_PROMPTS} and "
                f"{_MAX_PROMPTS} entries (got {len(prompts_raw)})."
            )
        prompts: list[BatchPromptItem] = []
        for idx, p in enumerate(prompts_raw):
            prompts.append(cls._parse_prompt(p, idx))

        profile = data.get("profile")
        if profile is not None and (not isinstance(profile, str) or not profile):
            raise ConfigurationError("'profile' must be a non-empty string.")
        transport = data.get("transport")
        if transport is not None and (not isinstance(transport, str) or not transport):
            raise ConfigurationError("'transport' must be a non-empty string.")
        output_dir = data.get("output_dir")
        if output_dir is not None and (not isinstance(output_dir, str) or not output_dir):
            raise ConfigurationError("'output_dir' must be a non-empty string.")

        return cls(
            prompts=tuple(prompts),
            profile=profile,
            transport=transport,
            output_dir=output_dir,
        )

    @staticmethod
    def _parse_prompt(p: object, idx: int) -> BatchPromptItem:
        if not isinstance(p, dict):
            raise ConfigurationError(f"prompts[{idx}] must be a JSON object.")
        item = cast("dict[str, Any]", p)
        unknown = set(item) - _ALLOWED_PROMPT_KEYS
        if unknown:
            raise ConfigurationError(
                f"prompts[{idx}] has unknown key(s) {sorted(unknown)!r}. "
                f"Valid: {sorted(_ALLOWED_PROMPT_KEYS)!r}."
            )
        text_raw = item.get("text")
        if not isinstance(text_raw, str):
            raise ConfigurationError(f"prompts[{idx}].text must be a string.")
        if not (_MIN_TEXT_LEN <= len(text_raw) <= _MAX_TEXT_LEN):
            raise ConfigurationError(
                f"prompts[{idx}].text length must be between {_MIN_TEXT_LEN} "
                f"and {_MAX_TEXT_LEN} (got {len(text_raw)})."
            )
        aspect_ratio = item.get("aspect_ratio", _DEFAULT_ASPECT_RATIO)
        if aspect_ratio not in _ALLOWED_ASPECT_RATIOS:
            raise ConfigurationError(
                f"prompts[{idx}].aspect_ratio {aspect_ratio!r} is invalid. "
                f"Valid: {list(_ALLOWED_ASPECT_RATIOS)!r}."
            )
        model = item.get("model", _DEFAULT_MODEL)
        if model not in _ALLOWED_MODELS:
            raise ConfigurationError(
                f"prompts[{idx}].model {model!r} is invalid. Valid: {list(_ALLOWED_MODELS)!r}."
            )
        count = item.get("count", _DEFAULT_COUNT)
        if not isinstance(count, int) or isinstance(count, bool):
            raise ConfigurationError(f"prompts[{idx}].count must be an integer.")
        if not (_MIN_COUNT <= count <= _MAX_COUNT):
            raise ConfigurationError(
                f"prompts[{idx}].count must be between {_MIN_COUNT} and {_MAX_COUNT} (got {count})."
            )
        output_filename = item.get("output_filename")
        if output_filename is not None and (
            not isinstance(output_filename, str) or not output_filename
        ):
            raise ConfigurationError(f"prompts[{idx}].output_filename must be a non-empty string.")
        return BatchPromptItem(
            text=text_raw,
            aspect_ratio=aspect_ratio,
            model=model,
            count=count,
            output_filename=output_filename,
        )


@dataclass
class _PromptOutcome:
    """Single-prompt outcome tracked in the run loop."""

    index: int
    prompt: BatchPromptItem
    status: str  # "ok" | "fail" | "skipped"
    saved_paths: list[Path] = field(default_factory=lambda: [])
    error: str | None = None
    exit_code: int = 0


# ---------------------------------------------------------------------------
# Helpers — output dir resolution + experimental-transport gating.
# ---------------------------------------------------------------------------


def _resolve_output_dir(*, cli_override: Path | None, config_value: str | None) -> Path:
    """CLI flag > config value > default (``out/<UTC-timestamp>/``)."""
    if cli_override is not None:
        return cli_override
    if config_value is not None:
        return Path(config_value)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return Path("out") / stamp


def _check_transport_gated(transport: str | None) -> None:
    """Per spec D.1 rule 5: experimental transports require env var.

    Raises :class:`ConfigurationError` if the named transport is in
    :data:`EXPERIMENTAL_TRANSPORTS` and ``GFLOW_CLI_EXPERIMENTAL_TRANSPORTS``
    is unset.
    """
    if transport is None or transport not in EXPERIMENTAL_TRANSPORTS:
        return
    import os  # noqa: PLC0415 — only needed on the rejection path

    if os.getenv("GFLOW_CLI_EXPERIMENTAL_TRANSPORTS") != "1":
        raise ConfigurationError(
            f"Transport {transport!r} is experimental. "
            f"Set GFLOW_CLI_EXPERIMENTAL_TRANSPORTS=1 to enable."
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
) -> list[_PromptOutcome]:
    """Run ``prompts`` sequentially through ONE FlowApiClient session.

    Per AUDIT_E1 D.2: a single ``async with FlowApiClient(...)`` block
    wraps the whole loop so the browser/page/project context persists
    across iterations. reCAPTCHA tokens mint fresh on each
    ``generate_image`` call.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    outcomes: list[_PromptOutcome] = []
    async with FlowApiClient(
        profile_dir=profile_dir, headless=headless, transport=transport
    ) as client:
        project = await client.create_project(title="gflow-cli run")
        log.info(
            "cli_run.project_created",
            project_id=project.project_id,
            n_prompts=len(prompts),
        )
        for idx, item in enumerate(prompts):
            outcome = await _run_one_prompt(
                client=client,
                project_id=project.project_id,
                idx=idx,
                item=item,
                output_dir=output_dir,
            )
            outcomes.append(outcome)
            if outcome.status == "fail" and not continue_on_error:
                # Fail-fast: append a "skipped" marker for the remaining
                # prompts so the summary table is complete and the caller
                # can see how many were not attempted.
                for skip_idx in range(idx + 1, len(prompts)):
                    outcomes.append(
                        _PromptOutcome(
                            index=skip_idx,
                            prompt=prompts[skip_idx],
                            status="skipped",
                        )
                    )
                break
    return outcomes


async def _run_one_prompt(
    *,
    client: FlowApiClient,
    project_id: str,
    idx: int,
    item: BatchPromptItem,
    output_dir: Path,
) -> _PromptOutcome:
    """Generate ``count`` image(s) for one prompt + download each."""
    req = GenerateImageRequest(
        prompt=item.text,
        aspect=Aspect.from_cli(item.aspect_ratio),
        model=Model.from_cli(item.model),
    )
    stem = item.output_filename or f"prompt_{idx}"
    try:
        if item.count == 1:
            img = await client.generate_image(project_id=project_id, req=req)
            images: list[GeneratedImage] = [img]
        else:
            images = await client.generate_images_batch(
                project_id=project_id, req=req, count=item.count
            )
        saved: list[Path] = []
        for img_idx, img in enumerate(images):
            target = output_dir / f"{stem}_{img_idx}.png"
            path = await client.download_image(img, target)
            saved.append(path)
        return _PromptOutcome(index=idx, prompt=item, status="ok", saved_paths=saved)
    except GFlowError as e:
        return _PromptOutcome(
            index=idx,
            prompt=item,
            status="fail",
            error=f"{type(e).__name__}: {e}",
            exit_code=_resolve_exit_code(e),
        )


def _render_summary(outcomes: list[_PromptOutcome]) -> int:
    """Print a Rich table + return the aggregate exit code."""
    table = Table(title="gflow run")
    table.add_column("#", justify="right")
    table.add_column("prompt", overflow="fold")
    table.add_column("ratio")
    table.add_column("status")
    table.add_column("detail", overflow="fold")
    for o in outcomes:
        if o.status == "ok":
            detail = " · ".join(str(p) for p in o.saved_paths)
            status_str = "[green]OK[/green]"
        elif o.status == "fail":
            detail = o.error or ""
            status_str = "[red]FAIL[/red]"
        else:
            detail = "(not attempted)"
            status_str = "[yellow]SKIPPED[/yellow]"
        # Truncate the prompt to keep the row readable.
        text_preview = o.prompt.text if len(o.prompt.text) <= 60 else o.prompt.text[:57] + "..."
        table.add_row(
            str(o.index),
            text_preview,
            o.prompt.aspect_ratio,
            status_str,
            detail,
        )
    console.print(table)
    succeeded = sum(1 for o in outcomes if o.status == "ok")
    failed = sum(1 for o in outcomes if o.status == "fail")
    skipped = sum(1 for o in outcomes if o.status == "skipped")
    console.print(
        f"\n{succeeded}/{len(outcomes)} succeeded · {failed} failure(s) · {skipped} skipped"
    )
    return max((o.exit_code for o in outcomes), default=0)


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
        sys.exit(_resolve_exit_code(e))

    profile_name = _resolve_profile(profile or cfg.profile)
    provider_dir = _make_provider_dir(profile_name)
    settings = get_settings()
    output_dir = _resolve_output_dir(cli_override=output_dir_override, config_value=cfg.output_dir)

    console.print(
        f"\n[bold]gflow run[/bold] · profile=[bold]{profile_name}[/bold] "
        f"· transport=[bold]{cfg.transport or '(default)'}[/bold] "
        f"· {len(cfg.prompts)} prompt(s)"
    )
    console.print(f"  output_dir: [dim]{output_dir}[/dim]")
    if not continue_on_error:
        console.print("  mode: [yellow]fail-fast[/yellow]")

    outcomes = asyncio.run(
        _run_batch(
            profile_dir=provider_dir,
            headless=settings.headless,
            transport=cfg.transport,
            prompts=cfg.prompts,
            output_dir=output_dir,
            continue_on_error=continue_on_error,
        )
    )
    exit_code = _render_summary(outcomes)
    if exit_code != 0:
        sys.exit(exit_code)
