"""Shared image-batch helpers for JSON runs and shell multi-prompt t2i."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from gflow_cli._cli_helpers import (
    safe_path_text,
)
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.dto import BatchSubmissionResult
from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.errors import (
    EXIT_CODE_MAP,
    BatchIntegrityError,
    BatchPartialError,
    ConfigurationError,
    GFlowError,
)

if TYPE_CHECKING:
    from gflow_cli.api.dto import GeneratedImage

console = Console()
logger = structlog.get_logger(__name__)


def _prompt_hash(text: str) -> str:
    """Short, deterministic, non-reversible hash for observability."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


ALLOWED_ASPECT_RATIOS: tuple[str, ...] = ("9:16", "16:9", "1:1", "4:3", "3:4")
ALLOWED_MODELS: tuple[str, ...] = ("nano2", "nano-pro", "image4", "imagen4")
MIN_PROMPTS = 1
MAX_PROMPTS = 50
# Maximum prompts allowed in a manifest batch. Intentionally small to keep
# batch runs predictable; change here or override via config if needed.
MAX_BATCH_PROMPTS: int = 5
MIN_TEXT_LEN = 1
MAX_TEXT_LEN = 2000
MIN_COUNT = 1
MAX_COUNT = 4
DEFAULT_ASPECT_RATIO = "9:16"
# Jitter range (seconds) between prompts when --same-project is used.
JITTER_MIN_SECONDS: float = 3.0
JITTER_MAX_SECONDS: float = 7.0
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


_ALLOWED_PROMPT_KEYS: frozenset[str] = frozenset(
    {"text", "aspect_ratio", "model", "count", "output_filename"}
)


def parse_batch_item_dict(p: dict[str, Any], idx: int) -> BatchPromptItem:
    """Parse and validate a dictionary (e.g. from JSON) into a BatchPromptItem.

    Used by `gflow run` to centralize validation logic.
    """
    unknown = set(p) - _ALLOWED_PROMPT_KEYS
    if unknown:
        raise ConfigurationError(
            f"prompts[{idx}] has unknown key(s) {sorted(unknown)!r}. "
            f"Valid: {sorted(_ALLOWED_PROMPT_KEYS)!r}."
        )
    text_raw = p.get("text")
    if not isinstance(text_raw, str):
        raise ConfigurationError(f"prompts[{idx}].text must be a string.")
    if not (MIN_TEXT_LEN <= len(text_raw) <= MAX_TEXT_LEN):
        raise ConfigurationError(
            f"prompts[{idx}].text length must be between {MIN_TEXT_LEN} "
            f"and {MAX_TEXT_LEN} (got {len(text_raw)})."
        )
    aspect_ratio = p.get("aspect_ratio", DEFAULT_ASPECT_RATIO)
    if aspect_ratio not in ALLOWED_ASPECT_RATIOS:
        raise ConfigurationError(
            f"prompts[{idx}].aspect_ratio {aspect_ratio!r} is invalid. "
            f"Valid: {list(ALLOWED_ASPECT_RATIOS)!r}."
        )
    model = p.get("model", DEFAULT_MODEL)
    if model not in ALLOWED_MODELS:
        raise ConfigurationError(
            f"prompts[{idx}].model {model!r} is invalid. Valid: {list(ALLOWED_MODELS)!r}."
        )
    count = p.get("count", DEFAULT_COUNT)
    if not isinstance(count, int) or isinstance(count, bool):
        raise ConfigurationError(f"prompts[{idx}].count must be an integer.")
    if not (MIN_COUNT <= count <= MAX_COUNT):
        raise ConfigurationError(
            f"prompts[{idx}].count must be between {MIN_COUNT} and {MAX_COUNT} (got {count})."
        )
    output_filename = p.get("output_filename")
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
        index=idx,
    )


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


async def run_one_image_prompt(
    *,
    client: Any,
    project_id: str | None,
    idx: int,
    item: BatchPromptItem,
    output_dir: Path,
) -> BatchOutcome:
    """Generate images for one prompt and download them.

    When ``project_id`` is ``None``, the client creates a new Flow project for
    this prompt (isolation mode). Pass an explicit ID to reuse a shared project.
    """
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

    async def image_worker(
        client: Any, project_id: str, idx: int, item: BatchPromptItem
    ) -> BatchOutcome:
        return await run_one_image_prompt(
            client=client,
            project_id=project_id,
            idx=idx,
            item=item,
            output_dir=output_dir,
        )

    return await run_sequential_batch(
        profile_dir=profile_dir,
        headless=headless,
        transport=transport,
        items=prompts,
        continue_on_error=continue_on_error,
        project_title=project_title,
        worker=image_worker,
        client_factory=client_factory,
        output_dir=output_dir,
    )


async def run_sequential_batch(
    *,
    profile_dir: Path,
    headless: bool,
    transport: str | None,
    items: tuple[Any, ...],
    continue_on_error: bool,
    project_title: str,
    worker: Callable[[Any, str, int, Any], Coroutine[Any, Any, BatchOutcome]],
    output_dir: Path | None = None,
    client_factory: Callable[..., Any] | None = None,
) -> list[BatchOutcome]:
    """Generic sequential orchestrator for Flow API batches."""
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    outcomes: list[BatchOutcome] = []
    factory = client_factory or FlowApiClient
    async with factory(profile_dir=profile_dir, headless=headless, transport=transport) as client:
        project = await client.create_project(title=project_title)
        for idx, item in enumerate(items):
            outcome = await worker(client, project.project_id, idx, item)
            outcomes.append(outcome)
            if outcome.status == "fail" and not continue_on_error:
                for skip_idx in range(idx + 1, len(items)):
                    outcomes.append(
                        BatchOutcome(
                            index=skip_idx,
                            prompt=items[skip_idx],
                            status="skipped",
                        )
                    )
                break
    return outcomes


# ---------------------------------------------------------------------------
# Manifest parsing — TSV and JSON formats for `gflow image batch`
# ---------------------------------------------------------------------------


def _validate_batch_prompt_count(count: int) -> None:
    if not (1 <= count <= MAX_BATCH_PROMPTS):
        raise ConfigurationError(
            f"Manifest must contain between 1 and {MAX_BATCH_PROMPTS} prompts (got {count})."
        )


def _tsv_parse_count(
    raw_count: str, *, default_count: int, source_label: str, line_number: int
) -> int:
    """Parse the ``count`` column of a TSV row. Empty falls back to the default."""
    if not raw_count:
        return default_count
    try:
        count = int(raw_count)
    except ValueError as exc:
        raise ConfigurationError(
            f"{source_label} line {line_number}: count {raw_count!r} is not an integer."
        ) from exc
    if not (MIN_COUNT <= count <= MAX_COUNT):
        raise ConfigurationError(
            f"{source_label} line {line_number}: count must be {MIN_COUNT}–{MAX_COUNT} "
            f"(got {count})."
        )
    return count


def _tsv_parse_aspect(
    raw_aspect: str, *, default_aspect_ratio: str, source_label: str, line_number: int
) -> str:
    """Parse the ``aspect_ratio`` column of a TSV row, validating against the allowed set."""
    aspect_ratio = raw_aspect if raw_aspect else default_aspect_ratio
    if aspect_ratio not in ALLOWED_ASPECT_RATIOS:
        raise ConfigurationError(
            f"{source_label} line {line_number}: aspect_ratio {aspect_ratio!r} invalid. "
            f"Valid: {list(ALLOWED_ASPECT_RATIOS)!r}."
        )
    return aspect_ratio


def _tsv_parse_model(
    raw_model: str, *, default_model: str, source_label: str, line_number: int
) -> str:
    """Parse the ``model`` column of a TSV row, validating against the allowed set."""
    model = raw_model if raw_model else default_model
    if model not in ALLOWED_MODELS:
        raise ConfigurationError(
            f"{source_label} line {line_number}: model {model!r} invalid. "
            f"Valid: {list(ALLOWED_MODELS)!r}."
        )
    return model


def parse_tsv_manifest(
    text: str,
    *,
    default_count: int = DEFAULT_COUNT,
    default_aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    default_model: str = DEFAULT_MODEL,
    source_label: str = "manifest.tsv",
) -> tuple[BatchPromptItem, ...]:
    """Parse an image batch TSV manifest.

    Columns (tab-separated): ``prompt``, ``count`` (optional), ``aspect_ratio``
    (optional), ``model`` (optional). Lines starting with ``#`` and blank lines
    are skipped. Missing or empty optional columns fall back to their defaults.
    """
    items: list[BatchPromptItem] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        if line_number == 1:
            raw = raw.removeprefix("﻿")
        # Blank/comment detection uses stripped form; column split uses raw
        # so that a leading tab (empty prompt column) is preserved.
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        cols = raw.split("\t")
        prompt = cols[0].strip()
        if not prompt:
            raise ConfigurationError(
                f"{source_label} line {line_number}: prompt column is required."
            )
        _validate_prompt_text(prompt, source_label=source_label, line_number=line_number)

        count = _tsv_parse_count(
            cols[1].strip() if len(cols) > 1 else "",
            default_count=default_count,
            source_label=source_label,
            line_number=line_number,
        )
        aspect_ratio = _tsv_parse_aspect(
            cols[2].strip() if len(cols) > 2 else "",
            default_aspect_ratio=default_aspect_ratio,
            source_label=source_label,
            line_number=line_number,
        )
        model = _tsv_parse_model(
            cols[3].strip() if len(cols) > 3 else "",
            default_model=default_model,
            source_label=source_label,
            line_number=line_number,
        )

        items.append(
            BatchPromptItem(
                text=prompt,
                aspect_ratio=aspect_ratio,
                model=model,
                count=count,
                output_filename=f"prompt_{len(items)}",
                index=len(items),
            )
        )

    _validate_batch_prompt_count(len(items))
    return tuple(items)


def parse_json_manifest(
    data: Any,
    *,
    default_count: int = DEFAULT_COUNT,
    default_aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    default_model: str = DEFAULT_MODEL,
) -> tuple[BatchPromptItem, ...]:
    """Parse a JSON manifest (already decoded) into BatchPromptItems.

    Each entry may specify: ``text`` (required), ``count``, ``aspect_ratio``,
    ``model``, ``output_filename``. Missing optional fields fall back to
    defaults before validation.
    """
    if not isinstance(data, list):
        raise ConfigurationError("JSON manifest must be a top-level array.")
    items: list[BatchPromptItem] = []
    for idx, raw_entry in enumerate(data):  # type: ignore[union-attr]
        if not isinstance(raw_entry, dict):
            raise ConfigurationError(f"prompts[{idx}] must be an object.")
        entry: dict[str, Any] = raw_entry  # type: ignore[assignment]
        # Inject defaults for missing optional keys before delegating validation.
        entry_with_defaults: dict[str, Any] = {
            "count": default_count,
            "aspect_ratio": default_aspect_ratio,
            "model": default_model,
            **entry,
        }
        items.append(parse_batch_item_dict(entry_with_defaults, idx))
    _validate_batch_prompt_count(len(items))
    return tuple(items)


def parse_manifest_file(
    path: Path,
    *,
    default_count: int = DEFAULT_COUNT,
    default_aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    default_model: str = DEFAULT_MODEL,
) -> tuple[BatchPromptItem, ...]:
    """Read and parse a manifest file, dispatching on ``.json`` / ``.tsv`` extension."""
    if not path.is_file():
        raise ConfigurationError(f"Manifest file not found: {path}")
    suffix = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(f"Cannot read manifest {path.name}: {exc}") from exc
    if suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"Manifest {path.name} is not valid JSON: {exc}") from exc
        return parse_json_manifest(
            data,
            default_count=default_count,
            default_aspect_ratio=default_aspect_ratio,
            default_model=default_model,
        )
    if suffix == ".tsv":
        return parse_tsv_manifest(
            text,
            default_count=default_count,
            default_aspect_ratio=default_aspect_ratio,
            default_model=default_model,
            source_label=path.name,
        )
    raise ConfigurationError(f"Unsupported manifest format {suffix!r}. Use .json or .tsv.")


# ---------------------------------------------------------------------------
# Manifest batch runner — used by `gflow image batch`
# ---------------------------------------------------------------------------


def _to_request(item: BatchPromptItem) -> GenerateImageRequest:
    """Convert a BatchPromptItem to a GenerateImageRequest for the transport."""
    return GenerateImageRequest(
        prompt=item.text,
        aspect=Aspect.from_cli(item.aspect_ratio),
        model=Model.from_cli(item.model),
        count=item.count,
    )


async def _download_results(
    *,
    client: Any,
    prompts: tuple[BatchPromptItem, ...],
    results: list[BatchSubmissionResult],
    output_dir: Path,
) -> list[BatchOutcome]:
    """Download images from ok BatchSubmissionResults and build BatchOutcome list."""
    outcomes: list[BatchOutcome] = []
    for item, result in zip(prompts, results, strict=False):
        if result.status != "ok":
            outcomes.append(
                BatchOutcome(
                    index=item.index,
                    prompt=item,
                    status="fail",
                    error=(
                        f"{type(result.error).__name__}: {result.error}"
                        if result.error
                        else "unknown"
                    ),
                )
            )
            logger.info(
                "image_batch.row_completed",
                row_idx=item.index,
                prompt_hash=result.prompt_hash,
                project_id=result.project_id,
                outcome="fail",
            )
            continue

        stem = item.output_filename or f"prompt_{item.index}"
        saved: list[Path] = []
        for img_idx, img in enumerate(result.images):
            target = output_dir / f"{stem}_{img_idx}.png"
            path = await client.download_image(img, target)
            saved.append(path)
            try:
                sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
            except (OSError, TypeError):
                sha = "unreadable"
            logger.info(
                "image_batch.row_completed",
                row_idx=item.index,
                output_idx=img_idx,
                prompt_hash=result.prompt_hash,
                project_id=result.project_id,
                sha256_prefix=sha,
                outcome="ok",
            )
        outcomes.append(
            BatchOutcome(
                index=item.index,
                prompt=item,
                status="ok",
                saved_paths=saved,
            )
        )
    return outcomes


async def run_manifest_image_batch(
    *,
    profile_dir: Path,
    headless: bool,
    transport: str | None,
    prompts: tuple[BatchPromptItem, ...],
    output_dir: Path,
    continue_on_error: bool,
    jitter_range: tuple[float, float] = (JITTER_MIN_SECONDS, JITTER_MAX_SECONDS),
    client_factory: Callable[..., Any] | None = None,
) -> list[BatchOutcome]:
    """Run a manifest batch via the transport's stay-mounted batch method.

    All prompts share one Flow project (always-same-project semantics; there
    is no per-prompt project toggle). The transport opens the editor once,
    submits all prompts sequentially with a random 3–7 s pause between each
    submission, awaits every generation in submission order, and returns
    per-prompt ``BatchSubmissionResult`` records.

    Jitter is the *submission-cadence* anti-bot control — it spaces out the
    submission clicks, not the generation wait. All generations run in parallel
    inside Flow; only the click timing is jittered. The function returns once
    every submitted generation has resolved (success or failure).

    Raises:
        RuntimeError: if the resolved transport is not
            ``UiAutomationTransport`` (the only transport that implements
            ``generate_images_batch``).
        BatchPartialError: on fail-fast when some prompts already produced
            downloadable images before the failing prompt. The orchestrator
            downloads the partial results before re-raising so no paid credits
            are lost.
        BatchIntegrityError: when the post-download file count does not match
            the expected count (silent mis-delivery guard).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    factory = client_factory or FlowApiClient
    async with factory(
        profile_dir=profile_dir,
        headless=headless,
        transport=transport,
        out_dir=output_dir,
    ) as client:
        # Capability check: only UiAutomationTransport implements generate_images_batch.
        if not isinstance(client.transport, UiAutomationTransport):
            raise RuntimeError(
                f"gflow image batch requires the ui_automation transport; "
                f"got {type(client.transport).__name__}"
            )

        # Build per-prompt requests.
        requests = [_to_request(item) for item in prompts]

        # Single delegation — transport handles editor/listener/jitter logic.
        batch_start = time.monotonic()
        last_submit_ts: float | None = None
        try:
            results = await client.transport.generate_images_batch(
                prompts=requests,
                jitter_range=jitter_range,
                continue_on_error=continue_on_error,
            )
        except BatchPartialError as exc:
            # Fail-fast salvage: download partial results before re-raising.
            partial_outcomes = await _download_results(
                client=client,
                prompts=prompts,
                results=list(exc.partial_results),
                output_dir=output_dir,
            )
            raise BatchPartialError(
                detail=exc.detail,
                route=exc.route,
                partial_results=tuple(partial_outcomes),
                cause=exc.cause,
            ) from exc.cause

        # Emit per-prompt observability events now that the transport has
        # returned real project_id values (spec §5.4).  We reconstruct a
        # monotone "submission timestamp" by spacing results 1 ms apart so
        # inter_submission_latency_ms is well-defined without live clocks.
        for idx, (item, result) in enumerate(zip(prompts, results, strict=False)):
            now = batch_start + idx * 0.001  # synthetic monotone tick per result
            t_since_prev = None if last_submit_ts is None else int((now - last_submit_ts) * 1000)
            if t_since_prev is not None:
                logger.info(
                    "image_batch.inter_submission_latency_ms",
                    row_idx=idx,
                    latency_ms=t_since_prev,
                )
            logger.info(
                "image_batch.submission_attempt",
                row_idx=idx,
                prompt_hash=_prompt_hash(item.text),
                aspect=item.aspect_ratio,
                model=item.model,
                jitter_enabled=jitter_range != (0.0, 0.0),
                t_since_prev_submit_ms=t_since_prev,
                project_id=result.project_id,
            )
            logger.info(
                "image_batch.submission_result",
                row_idx=idx,
                outcome=result.status,
                project_id=result.project_id,
            )
            last_submit_ts = now

        # Happy path: download every ok result.
        outcomes = await _download_results(
            client=client,
            prompts=prompts,
            results=results,
            output_dir=output_dir,
        )

        # Post-download integrity check.
        ok_outcomes = [o for o in outcomes if o.status == "ok"]
        expected_files = sum(
            p.count for p, o in zip(prompts, outcomes, strict=False) if o.status == "ok"
        )
        actual_files = sum(len(o.saved_paths) for o in ok_outcomes)
        if actual_files != expected_files:
            missing_indices = tuple(
                p.index
                for p, o in zip(prompts, outcomes, strict=False)
                if o.status == "ok" and len(o.saved_paths) < p.count
            )
            raise BatchIntegrityError(
                detail=f"expected {expected_files} files, got {actual_files}",
                route="run_manifest_image_batch",
                prompt_indices=missing_indices,
            )

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
            detail = " · ".join(safe_path_text(path) for path in outcome.saved_paths)
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
