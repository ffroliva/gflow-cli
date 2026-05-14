"""Shared image-batch helpers for JSON runs and shell multi-prompt t2i."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.errors import EXIT_CODE_MAP, ConfigurationError, GFlowError

if TYPE_CHECKING:
    from gflow_cli.api.dto import GeneratedImage

console = Console()

ALLOWED_ASPECT_RATIOS: tuple[str, ...] = ("9:16", "16:9", "1:1", "4:3", "3:4")
ALLOWED_MODELS: tuple[str, ...] = ("nano2", "nano-pro", "image4", "imagen4")
MIN_PROMPTS = 1
MAX_PROMPTS = 50
MIN_TEXT_LEN = 1
MAX_TEXT_LEN = 2000
MIN_COUNT = 1
MAX_COUNT = 4
DEFAULT_ASPECT_RATIO = "9:16"
DEFAULT_MODEL = "nano2"
DEFAULT_COUNT = 1
MAX_PROMPT_FILE_BYTES = 512 * 1024

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


@dataclass(frozen=True)
class BatchPromptItem:
    """One prompt entry for image batch execution."""

    text: str
    aspect_ratio: str = DEFAULT_ASPECT_RATIO
    model: str = DEFAULT_MODEL
    count: int = DEFAULT_COUNT
    output_filename: str | None = None
    index: int = 0


@dataclass(frozen=True)
class ParsedPromptLine:
    """A prompt line with source metadata for diagnostics."""

    text: str
    source_label: str
    line_number: int
    prompt_index: int


@dataclass
class BatchOutcome:
    """Single-prompt outcome tracked in the run loop."""

    index: int
    prompt: BatchPromptItem
    status: str
    saved_paths: list[Path] = field(default_factory=lambda: [])
    error: str | None = None
    exit_code: int = 0


def resolve_exit_code(exc: GFlowError) -> int:
    for cls, code in EXIT_CODE_MAP.items():
        if isinstance(exc, cls):
            return code
    return 1


def safe_terminal_text(value: str) -> str:
    """Strip ANSI controls and escape Rich markup before terminal rendering."""
    return escape(_ANSI_RE.sub("", value))


def safe_prompt_preview(prompt: str, *, max_chars: int = 60) -> str:
    """Display-safe, bounded prompt preview. Does not mutate request prompts."""
    clean = safe_terminal_text(prompt)
    return clean if len(clean) <= max_chars else clean[: max_chars - 3] + "..."


def _prompt_file_label(path: Path) -> str:
    return f"--prompts-file {safe_terminal_text(path.name)}"


def _validate_prompt_count(count: int) -> None:
    if not (MIN_PROMPTS <= count <= MAX_PROMPTS):
        raise ConfigurationError(
            f"Prompt source must contain between {MIN_PROMPTS} and {MAX_PROMPTS} "
            f"prompts (got {count})."
        )


def _validate_prompt_text(text: str, *, source_label: str, line_number: int | None) -> None:
    if not (MIN_TEXT_LEN <= len(text) <= MAX_TEXT_LEN):
        location = source_label
        if line_number is not None:
            location += f" line {line_number}"
        raise ConfigurationError(
            f"{location}: prompt length must be between {MIN_TEXT_LEN} and "
            f"{MAX_TEXT_LEN} characters (got {len(text)})."
        )


def parse_prompt_lines(text: str, *, source_label: str) -> tuple[ParsedPromptLine, ...]:
    """Parse shell prompt text: one prompt per line, blanks/comments skipped."""
    parsed: list[ParsedPromptLine] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if line_number == 1:
            raw_line = raw_line.removeprefix("\ufeff")
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        _validate_prompt_text(line, source_label=source_label, line_number=line_number)
        parsed.append(
            ParsedPromptLine(
                text=line,
                source_label=source_label,
                line_number=line_number,
                prompt_index=len(parsed),
            )
        )
    _validate_prompt_count(len(parsed))
    return tuple(parsed)


def read_prompt_file(path: Path) -> tuple[ParsedPromptLine, ...]:
    """Read and parse a prompt file using basename-only diagnostics."""
    label = _prompt_file_label(path)
    try:
        stat = path.stat()
    except OSError as exc:
        raise ConfigurationError(f"{label}: file not found or is not readable.") from exc
    if not path.is_file():
        raise ConfigurationError(f"{label}: must be a regular file.")
    if stat.st_size > MAX_PROMPT_FILE_BYTES:
        raise ConfigurationError(f"{label}: file must be at most 512 KiB.")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigurationError(f"{label}: file must be valid UTF-8.") from exc
    except OSError as exc:
        raise ConfigurationError(f"{label}: failed to read file.") from exc
    return parse_prompt_lines(text, source_label=label)


def _validate_item_values(
    *,
    aspect_ratio: str,
    model: str,
    count: object,
    label: str,
) -> None:
    if aspect_ratio not in ALLOWED_ASPECT_RATIOS:
        raise ConfigurationError(
            f"{label}.aspect_ratio {aspect_ratio!r} is invalid. "
            f"Valid: {list(ALLOWED_ASPECT_RATIOS)!r}."
        )
    if model not in ALLOWED_MODELS:
        raise ConfigurationError(
            f"{label}.model {model!r} is invalid. Valid: {list(ALLOWED_MODELS)!r}."
        )
    if not isinstance(count, int) or isinstance(count, bool):
        raise ConfigurationError(f"{label}.count must be an integer.")
    if not (MIN_COUNT <= count <= MAX_COUNT):
        raise ConfigurationError(
            f"{label}.count must be between {MIN_COUNT} and {MAX_COUNT} (got {count})."
        )


def prompt_items_from_parsed(
    parsed: tuple[ParsedPromptLine, ...],
    *,
    aspect_ratio: str,
    model: str,
    count: int,
) -> tuple[BatchPromptItem, ...]:
    _validate_item_values(
        aspect_ratio=aspect_ratio,
        model=model,
        count=count,
        label="prompt",
    )
    return tuple(
        BatchPromptItem(
            text=item.text,
            aspect_ratio=aspect_ratio,
            model=model,
            count=count,
            output_filename=f"prompt_{item.prompt_index}",
            index=item.prompt_index,
        )
        for item in parsed
    )


def prompt_items_from_texts(
    prompts: tuple[str, ...],
    *,
    aspect_ratio: str,
    model: str,
    count: int,
    source_label: str,
) -> tuple[BatchPromptItem, ...]:
    _validate_prompt_count(len(prompts))
    _validate_item_values(
        aspect_ratio=aspect_ratio,
        model=model,
        count=count,
        label="prompt",
    )
    items: list[BatchPromptItem] = []
    for index, text in enumerate(prompts):
        _validate_prompt_text(text, source_label=source_label, line_number=None)
        items.append(
            BatchPromptItem(
                text=text,
                aspect_ratio=aspect_ratio,
                model=model,
                count=count,
                output_filename=f"prompt_{index}",
                index=index,
            )
        )
    return tuple(items)


def resolve_t2i_batch_output_dir(*, out: Path | None, output_root: Path) -> Path:
    if out is not None:
        return out
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return output_root / "images" / stamp


async def run_one_image_prompt(
    *,
    client: Any,
    project_id: str,
    idx: int,
    item: BatchPromptItem,
    output_dir: Path,
) -> BatchOutcome:
    """Generate images for one prompt and download them."""
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
        return BatchOutcome(index=idx, prompt=item, status="ok", saved_paths=saved)
    except GFlowError as exc:
        return BatchOutcome(
            index=idx,
            prompt=item,
            status="fail",
            error=f"{type(exc).__name__}: {exc}",
            exit_code=resolve_exit_code(exc),
        )


async def run_image_batch(
    *,
    profile_dir: Path,
    headless: bool,
    transport: str | None,
    prompts: tuple[BatchPromptItem, ...],
    output_dir: Path,
    continue_on_error: bool,
    project_title: str,
    client_factory: Callable[..., Any] | None = None,
) -> list[BatchOutcome]:
    """Run prompts sequentially through one FlowApiClient session."""
    output_dir.mkdir(parents=True, exist_ok=True)
    outcomes: list[BatchOutcome] = []
    factory = client_factory or FlowApiClient
    async with factory(profile_dir=profile_dir, headless=headless, transport=transport) as client:
        project = await client.create_project(title=project_title)
        for idx, item in enumerate(prompts):
            outcome = await run_one_image_prompt(
                client=client,
                project_id=project.project_id,
                idx=idx,
                item=item,
                output_dir=output_dir,
            )
            outcomes.append(outcome)
            if outcome.status == "fail" and not continue_on_error:
                for skip_idx in range(idx + 1, len(prompts)):
                    outcomes.append(
                        BatchOutcome(
                            index=skip_idx,
                            prompt=prompts[skip_idx],
                            status="skipped",
                        )
                    )
                break
    return outcomes


def render_image_batch_summary(outcomes: list[BatchOutcome], *, title: str) -> int:
    """Print a Rich table + return the aggregate exit code."""
    table = Table(title=title)
    table.add_column("#", justify="right")
    table.add_column("prompt", overflow="fold")
    table.add_column("ratio")
    table.add_column("status")
    table.add_column("detail", overflow="fold")
    for outcome in outcomes:
        if outcome.status == "ok":
            detail = " · ".join(str(path) for path in outcome.saved_paths)
            status_str = "[green]OK[/green]"
        elif outcome.status == "fail":
            detail = outcome.error or ""
            status_str = "[red]FAIL[/red]"
        else:
            detail = "(not attempted)"
            status_str = "[yellow]SKIPPED[/yellow]"
        table.add_row(
            str(outcome.index),
            safe_prompt_preview(outcome.prompt.text),
            outcome.prompt.aspect_ratio,
            status_str,
            safe_terminal_text(detail),
        )
    console.print(table)
    succeeded = sum(1 for outcome in outcomes if outcome.status == "ok")
    failed = sum(1 for outcome in outcomes if outcome.status == "fail")
    skipped = sum(1 for outcome in outcomes if outcome.status == "skipped")
    console.print(
        f"\n{succeeded}/{len(outcomes)} succeeded · {failed} failure(s) · {skipped} skipped"
    )
    return max((outcome.exit_code for outcome in outcomes), default=0)
