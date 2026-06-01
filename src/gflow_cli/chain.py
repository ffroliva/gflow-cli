"""Sequential last-frame I2V chain orchestrator.

A *chain* renders a list of links into one continuous sequence: link 0 is a
text-to-video (T2V) generation, and every later link is an image-to-video (I2V)
generation seeded by the extracted last frame of the previous link's clip. The
result is visual continuity across links without any server-side stitching.

This module is the pure orchestration core (Task 7). CLI concerns — cost gate,
``--dry-run``, ``--max-links``, output naming policy — live in the command layer
(Task 8) and the DTO/transport layers it depends on. The orchestrator drives an
injected ``client`` (an async ``generate_video``), an injected ``extractor``
(defaulting to :func:`gflow_cli.media.extract_last_frame`), and an optional
``recorder`` for crash-safe persistence.

Key invariants:

* **Concurrency = 1.** Links run strictly sequentially; each I2V link depends on
  the previous link's output, so there is nothing to parallelise.
* **Reject-up-front.** A model that cannot do i2v interpolation (``omni_flash``)
  is rejected with :class:`ModelModeIncompatibilityError` BEFORE any spend.
* **Record-before-extract.** Once a link's clip is downloaded, the recorder is
  invoked BEFORE the frame extractor runs. A crash in the download->extract gap
  resumes at extraction, never re-generates the (already paid-for) clip.
* **Abort-preserves-partials.** A per-link :class:`WireFormatError` (i2v silently
  routed to the t2v backstop) or :class:`WafRejectionError` (HTTP 403) aborts the
  chain and raises :class:`ChainPartialError` carrying the ``Path`` of every link
  completed BEFORE the failure. The failing link's successors are never generated.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import structlog

from gflow_cli.api.video import (
    Aspect,
    GenerateVideoRequest,
    Mode,
    VideoModel,
)
from gflow_cli.errors import (
    ChainPartialError,
    ModelModeIncompatibilityError,
    WafRejectionError,
    WireFormatError,
)
from gflow_cli.media import extract_last_frame

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.video import VideoResult

__all__ = [
    "ChainLinkResult",
    "ChainLinkSpec",
    "ChainRecorder",
    "FrameExtractor",
    "run_chain",
]

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ChainLinkSpec:
    """One link's inputs: a prompt plus optional per-link overrides.

    ``model``/``duration``/``aspect`` override the chain-level defaults for this
    link only when set; ``None`` means "inherit the chain default". The chain
    enforces a UNIFORM aspect across links for continuity, so a per-link
    ``aspect`` override is reserved for future use and currently informational —
    :func:`run_chain` applies the chain-level aspect to every link.
    """

    prompt: str
    model: VideoModel | None = None
    duration: int | None = None
    aspect: Aspect | None = None


@dataclass(frozen=True)
class ChainLinkResult:
    """Outcome of one completed link.

    ``local_path`` is the downloaded clip; ``frame_path`` is the JPEG extracted
    from it to seed the next link (``None`` for the final link, which seeds
    nothing). ``media_id`` / ``project_id`` / ``flow_operation_id`` mirror the
    transport's :class:`~gflow_cli.api.video.VideoResult` for the recorder.
    """

    index: int
    prompt: str
    local_path: Path
    media_id: str
    frame_path: Path | None = None
    project_id: str | None = None
    flow_operation_id: str | None = None


class FrameExtractor(Protocol):
    """Callable shape of :func:`gflow_cli.media.extract_last_frame`.

    The orchestrator runs it via ``asyncio.to_thread`` (decoding is blocking).
    """

    def __call__(self, src: Path, dst: Path, *, offset_ms: int = 0) -> Path: ...


class ChainRecorder(Protocol):
    """Persistence hook invoked once per completed link, BEFORE extraction.

    Implementations record the just-downloaded clip so a crash before the next
    link does not lose the (already paid-for) result. The orchestrator never
    inspects the return value.
    """

    def record_chain_link(self, result: ChainLinkResult) -> None: ...


async def run_chain(
    *,
    client: FlowApiClient,
    links: Sequence[ChainLinkSpec],
    out_dir: Path,
    model: VideoModel,
    extractor: FrameExtractor = extract_last_frame,
    recorder: ChainRecorder | None = None,
    aspect: Aspect = Aspect.PORTRAIT,
    seed_offset_ms: int = 0,
    jitter: float = 0.0,
) -> list[ChainLinkResult]:
    """Render ``links`` as a sequential last-frame I2V chain.

    Args:
        client: A ``FlowApiClient`` (or mock) exposing
            ``async generate_video(*, req: GenerateVideoRequest) -> VideoResult``.
        links: Ordered per-link specs. Link 0 is T2V; links 1..N are I2V seeded
            by the previous link's extracted last frame.
        out_dir: Directory for clips and seed frames.
        model: The video model. MUST support i2v interpolation (every link after
            the first is I2V); otherwise raises ``ModelModeIncompatibilityError``
            before any generation.
        extractor: Last-frame extractor (defaults to ``extract_last_frame``),
            run off the event loop via ``asyncio.to_thread``.
        recorder: Optional persistence hook called BEFORE extraction per link.
        aspect: Uniform aspect applied to every link (continuity requirement).
        seed_offset_ms: Passed to the extractor — select a frame this many ms
            before EOF (the fade-to-black mitigation).
        jitter: When > 0, sleep a random ``[0, jitter)`` seconds BETWEEN links
            (anti-bot cadence); never before link 0.

    Returns:
        One :class:`ChainLinkResult` per link, in order.

    Raises:
        ModelModeIncompatibilityError: ``model`` cannot do i2v interpolation.
        ChainPartialError: A link failed with a ``WireFormatError`` (i2v routed
            to the t2v backstop) or ``WafRejectionError`` (403). Carries the
            ``Path`` of every link completed before the failure.
    """
    if not model.supports_i2v_interpolation():
        msg = (
            f"model {model.value!r} does not support i2v interpolation; a chain "
            f"needs start-frame seeding for every link after the first"
        )
        raise ModelModeIncompatibilityError(msg)

    results: list[ChainLinkResult] = []
    completed_paths: list[Path] = []
    prev_frame: Path | None = None

    for index, spec in enumerate(links):
        if index > 0 and jitter > 0:
            await asyncio.sleep(random.uniform(0.0, jitter))  # noqa: S311 — cadence, not crypto

        _log.info("chain_link_started", index=index, total_links=len(links))
        link_model = spec.model if spec.model is not None else model
        is_i2v = index > 0
        req = GenerateVideoRequest(
            prompt=spec.prompt,
            mode=Mode.I2V if is_i2v else Mode.T2V,
            aspect=aspect,
            model=link_model,
            duration=spec.duration,
            start_image=prev_frame if is_i2v else None,
        )

        try:
            result: VideoResult = await client.generate_video(req=req)
        except (WireFormatError, WafRejectionError) as exc:
            _log.warning(
                "chain_link_aborted",
                index=index,
                error_class=type(exc).__name__,
                completed=len(completed_paths),
            )
            raise ChainPartialError(
                detail=f"chain aborted at link {index}: {exc}",
                partial_results=list(completed_paths),
                cause=exc,
            ) from exc

        local_path = result.local_path
        if local_path is None:
            msg = f"link {index} returned no local_path (download failed)"
            raise ChainPartialError(
                detail=msg,
                partial_results=list(completed_paths),
            )

        media_id = result.status.media_id
        is_last = index == len(links) - 1

        # RECORD-BEFORE-EXTRACT: persist the downloaded clip before decoding it.
        # The frame_path is the planned seed-frame destination; it is filled in
        # below for non-final links, but the clip itself is recorded first so a
        # crash in the download->extract gap resumes at extraction.
        frame_path = None if is_last else out_dir / f"link{index}_lastframe.jpg"
        link_result = ChainLinkResult(
            index=index,
            prompt=spec.prompt,
            local_path=local_path,
            media_id=media_id,
            frame_path=frame_path,
            project_id=result.project_id,
            flow_operation_id=result.flow_operation_id,
        )
        if recorder is not None:
            recorder.record_chain_link(link_result)

        if frame_path is not None:
            prev_frame = await asyncio.to_thread(
                extractor,
                src=local_path,
                dst=frame_path,
                offset_ms=seed_offset_ms,
            )

        results.append(link_result)
        completed_paths.append(local_path)
        _log.info(
            "chain_link_completed",
            index=index,
            media_id=media_id,
            mode=req.mode.value,
            seeded=is_i2v,
        )

    return results
