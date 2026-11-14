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
* **Reject-up-front.** A model chains cannot use (``omni_flash`` — single-clip
  start-frame i2v only, not proven at chain scale; refs #125) is rejected with
  :class:`ModelModeIncompatibilityError` BEFORE any spend.
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
    validate_duration_for_model,
)
from gflow_cli.errors import (
    ChainPartialError,
    GFlowError,
    ModelModeIncompatibilityError,
    WafRejectionError,
    WireFormatError,
)
from gflow_cli.media import extract_last_frame

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.video import VideoResult, VideoStartedCallback
    from gflow_cli.tools.invocation import AppliedTool

__all__ = [
    "ChainLinkResult",
    "ChainLinkSpec",
    "ChainRecorder",
    "FrameExtractor",
    "LinkCompletedHook",
    "LinkFailedHook",
    "LinkStartedHook",
    "reject_unusable_links",
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
    # Tool provenance — set when a ``--tool`` rewrote this link's prompt (PR2).
    # ``prompt`` is then the rewritten text; these are recorded, not sent.
    original_prompt: str | None = None
    tool: AppliedTool | None = None


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


class LinkStartedHook(Protocol):
    """Factory producing the per-link ``on_started`` forwarded into the transport.

    Called with the link's :class:`GenerateVideoRequest` BEFORE generation; the
    returned :data:`~gflow_cli.api.video.VideoStartedCallback` (or ``None``) is
    handed to ``generate_video(on_started=...)`` so the CLI can record the link
    in the ``videos`` catalog the moment the transport reports it started. The
    per-link ``request`` is threaded so the catalog row matches the link's mode
    (link 0 T2V, links N I2V).
    """

    def __call__(self, request: GenerateVideoRequest) -> VideoStartedCallback | None: ...


class LinkCompletedHook(Protocol):
    """Hook called AFTER each link's clip is downloaded.

    Receives the link's own request + result so the CLI can finalize the
    ``videos`` catalog row. The implementation MUST absorb its own persistence
    failures (a post-success error must never abort the already-paid chain); the
    orchestrator does not guard the call.
    """

    def __call__(self, request: GenerateVideoRequest, result: VideoResult) -> None: ...


class LinkFailedHook(Protocol):
    """Hook called when a link's ``generate_video`` raises (#341).

    Receives the link's own request + the exception BEFORE the orchestrator
    wraps/re-raises it, so the CLI can persist a FAILED catalog row with full
    request context. The implementation MUST absorb its own persistence
    failures — it runs on the error path and must never mask the original
    exception; the orchestrator does not guard the call.
    """

    def __call__(self, request: GenerateVideoRequest, exc: BaseException) -> None: ...


def _build_link_request(
    *,
    spec: ChainLinkSpec,
    index: int,
    model: VideoModel,
    aspect: Aspect,
    prev_frame: Path | None,
) -> GenerateVideoRequest:
    """Construct the GenerateVideoRequest for one chain link."""
    link_model = spec.model if spec.model is not None else model
    is_i2v = index > 0
    return GenerateVideoRequest(
        prompt=spec.prompt,
        mode=Mode.I2V if is_i2v else Mode.T2V,
        aspect=aspect,
        model=link_model,
        duration=spec.duration,
        start_image=prev_frame if is_i2v else None,
        original_prompt=spec.original_prompt,
        tool=spec.tool,
    )


def reject_unusable_links(*, model: VideoModel, links: Sequence[ChainLinkSpec]) -> None:
    """Reject a chain that cannot succeed, BEFORE link 0 renders (#125, #634).

    "Too late" in a chain means *after money was spent*: a chain that dies at
    link 3 has already generated and billed links 0-2.

    Two things are checked per link, against the link's EFFECTIVE model:

    * **omni_flash.** The chain-level ``model`` is rejected first, but
      :func:`_build_link_request` prefers ``spec.model`` when set, so a per-link
      override walked straight past that check.
    * **duration.** Anything but 4, 6 or 8 is rejected: chains reject both Omni
      models, the only ones offering other lengths, and Veo 3.1 supports 4s/6s/8s.
    """
    if model in (VideoModel.OMNI_FLASH, VideoModel.OMNI_1_2_FLASH):
        msg = (
            f"model {model.value!r} is not supported for chains: a chain "
            f"renders N seeded i2v links back-to-back, and omni_flash i2v is "
            f"wire-verified for single generations only (start frame "
            f"2026-08-03, end frame 2026-09-02; refs #125, #626). Use a Veo "
            f"3.1 model."
        )
        raise ModelModeIncompatibilityError(msg)

    for index, spec in enumerate(links):
        effective = spec.model if spec.model is not None else model
        if effective in (VideoModel.OMNI_FLASH, VideoModel.OMNI_1_2_FLASH):
            msg = (
                f"links[{index}] overrides model to {effective.value!r}, which is "
                f"not supported for chains: omni_flash i2v is wire-verified for "
                f"single generations only (refs #125, #626). Drop the per-link "
                f"model override, or use a Veo 3.1 model."
            )
            raise ModelModeIncompatibilityError(msg)
        if spec.duration is not None:
            effective_model = spec.model or model
            try:
                validate_duration_for_model(effective_model, spec.duration)
            except ValueError as exc:
                msg = f"links[{index}].duration is invalid for {effective_model.value!r}: {exc}"
                raise ModelModeIncompatibilityError(msg) from exc


async def _generate_link(
    *,
    client: FlowApiClient,
    req: GenerateVideoRequest,
    index: int,
    completed_paths: list[Path],
    completed_media_ids: list[str],
    on_link_started: LinkStartedHook | None,
    on_link_failed: LinkFailedHook | None,
) -> VideoResult:
    """Call generate_video for one link; raise ChainPartialError on abort."""
    link_on_started = on_link_started(req) if on_link_started is not None else None
    try:
        return await client.generate_video(req=req, on_started=link_on_started)
    except Exception as exc:
        _notify_link_failed(on_link_failed, req, exc)
        wrap = isinstance(exc, WireFormatError | WafRejectionError) or (
            completed_paths and not isinstance(exc, GFlowError)
        )
        if not wrap:
            if completed_paths and isinstance(exc, GFlowError):
                # #1201: links 0..k are billed whatever this link's own class says.
                exc.charged = "yes"
                exc.next_action = "reconcile"
                exc.retryable = False
                if not exc.operation_id:  # an in-flight link's own id is the better handle
                    exc.operation_id = completed_media_ids[-1]
            raise
        _log.warning(
            "chain_link_aborted",
            index=index,
            error_class=type(exc).__name__,
            completed=len(completed_paths),
        )
        raise ChainPartialError(
            detail=f"chain aborted at link {index}: {exc}",
            partial_results=list(completed_paths),
            completed_media_ids=list(completed_media_ids),
            cause=exc,
        ) from exc


def _notify_link_failed(
    hook: LinkFailedHook | None,
    req: GenerateVideoRequest,
    exc: BaseException,
) -> None:
    """Invoke the failure hook, absorbing ITS failures (#341 review finding).

    The hook runs on the error path; letting a hook exception propagate would
    replace the original error and skip the ChainPartialError partial-results
    contract, so the orchestrator enforces the absorb rule at the seam instead
    of trusting every hook implementer.
    """
    if hook is None:
        return
    try:
        hook(req, exc)
    except Exception as hook_exc:  # noqa: BLE001 — double-fault guard, see docstring
        _log.warning("chain_link_failed_hook_error", error=str(hook_exc))


def _build_link_result(
    *,
    index: int,
    spec: ChainLinkSpec,
    result: VideoResult,
    is_last: bool,
    out_dir: Path,
) -> ChainLinkResult:
    """Assemble the ChainLinkResult after a successful download."""
    local_path = result.local_path
    frame_path = None if is_last else out_dir / f"link{index}_lastframe.jpg"
    return ChainLinkResult(
        index=index,
        prompt=spec.prompt,
        local_path=local_path,  # type: ignore[arg-type]
        media_id=result.status.media_id,
        frame_path=frame_path,
        project_id=result.project_id,
        flow_operation_id=result.flow_operation_id,
    )


async def _relocate_guarded(
    relocate: Callable[[int, VideoResult], VideoResult], index: int, result: VideoResult
) -> VideoResult:
    """Run the ``--out-file`` move off the loop; on any failure keep the clip where it is.

    The clip is already paid for, so it must reach the recorder whatever the hook does: a
    link without a chain row is billed again on ``--resume-from``. Same rule as
    :func:`_notify_link_failed`.
    """
    try:
        return await asyncio.to_thread(relocate, index, result)
    except Exception as exc:  # noqa: BLE001 — see docstring
        _log.warning(
            "chain_clip_kept_at_download_path",
            link=index + 1,
            media_id=result.status.media_id,
            kept_at=str(result.local_path),
            reason=str(exc),
        )
        return result


async def _resume_seed(
    extractor: FrameExtractor,
    *,
    start_index: int,
    seed_clip: Path | None,
    out_dir: Path,
    offset_ms: int,
) -> Path | None:
    """The seed frame for ``links[0]``: none at link 0, else re-extracted from ``seed_clip``."""
    if start_index == 0:
        return None
    if seed_clip is None:
        msg = f"link {start_index} continues a chain: pass seed_clip, the clip of the link before"
        raise ValueError(msg)
    dst = out_dir / f"link{start_index - 1}_lastframe.jpg"
    return await asyncio.to_thread(extractor, src=seed_clip, dst=dst, offset_ms=offset_ms)


async def run_chain(
    *,
    client: FlowApiClient,
    links: Sequence[ChainLinkSpec],
    out_dir: Path,
    model: VideoModel,
    extractor: FrameExtractor = extract_last_frame,
    recorder: ChainRecorder | None = None,
    on_link_started: LinkStartedHook | None = None,
    on_link_completed: LinkCompletedHook | None = None,
    on_link_failed: LinkFailedHook | None = None,
    aspect: Aspect = Aspect.PORTRAIT,
    seed_offset_ms: int = 0,
    jitter: float = 0.0,
    relocate: Callable[[int, VideoResult], VideoResult] | None = None,
    start_index: int = 0,
    seed_clip: Path | None = None,
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
        recorder: Optional chain-correlation persistence hook called BEFORE
            extraction per link (records the link into the chain table).
        on_link_started: Optional factory producing the per-link ``on_started``
            forwarded into ``generate_video``; lets the CLI record each link in
            the ``videos`` catalog the moment the transport reports it started.
            Threaded with the link's own request (link 0 T2V, links N I2V).
        on_link_completed: Optional hook called AFTER each link's clip is
            downloaded, with the link's own request + result, so the CLI can
            finalize the catalog row. The hook MUST absorb its own persistence
            failures; the orchestrator does not guard it.
        aspect: Uniform aspect applied to every link (continuity requirement).
        seed_offset_ms: Passed to the extractor — select a frame this many ms
            before EOF (the fade-to-black mitigation).
        jitter: When > 0, sleep a random ``[0, jitter)`` seconds BETWEEN links
            (anti-bot cadence); never before link 0.
        relocate: Optional ``(index, result) -> result`` that moves a downloaded
            clip (``--out-file``, #1293). It runs off the event loop before
            anything reads the path, so the recorders, the seed extraction and
            the return value all see where the clip ended up. If it raises, the
            clip stays at its download path and a warning says so. A result with
            no ``local_path`` must come back unchanged.
        start_index: The manifest index of ``links[0]`` (``--resume-from``, #1300). Request
            mode, ``ChainLinkResult.index`` and the recorded ``link_index`` all count from it.
        seed_clip: The clip of link ``start_index - 1``; its last frame seeds ``links[0]``.
            Required when ``start_index`` is past 0, so a resumed link is never sent as t2v.

    Returns:
        One :class:`ChainLinkResult` per link, in order.

    Raises:
        ModelModeIncompatibilityError: ``model`` is not accepted for chains
            (``omni_flash`` — wire-verified for SINGLE-clip i2v only, start
            frame and end frame alike; chain scale is what remains unproven.
            Refs #125, #626).
        ChainPartialError: A link failed with a ``WireFormatError`` (i2v routed
            to the t2v backstop) or ``WafRejectionError`` (403). Carries the
            ``Path`` of every link completed before the failure.
        ValueError: ``start_index`` is past 0 but no ``seed_clip`` was given.
    """
    reject_unusable_links(model=model, links=links)

    results: list[ChainLinkResult] = []
    completed_paths: list[Path] = []
    prev_frame = await _resume_seed(
        extractor,
        start_index=start_index,
        seed_clip=seed_clip,
        out_dir=out_dir,
        offset_ms=seed_offset_ms,
    )
    last_index = start_index + len(links) - 1

    for index, spec in enumerate(links, start=start_index):
        if index > start_index and jitter > 0:
            await asyncio.sleep(random.uniform(0.0, jitter))  # noqa: S311  # cadence, not crypto

        _log.info("chain_link_started", index=index, total_links=last_index + 1)
        req = _build_link_request(
            spec=spec, index=index, model=model, aspect=aspect, prev_frame=prev_frame
        )

        result = await _generate_link(
            client=client,
            req=req,
            index=index,
            completed_paths=completed_paths,
            completed_media_ids=[r.media_id for r in results],
            on_link_started=on_link_started,
            on_link_failed=on_link_failed,
        )
        if relocate is not None:  # a missing clip is left as is and refused just below
            result = await _relocate_guarded(relocate, index, result)

        if result.local_path is None:
            msg = f"link {index} returned no local_path (download failed)"
            exc = ChainPartialError(
                detail=msg,
                partial_results=list(completed_paths),
                completed_media_ids=[r.media_id for r in results],
            )
            # #341: this abort path must also reach the failure hook, else the
            # link's STARTED catalog row is stranded and the abort is invisible
            # to `gflow data list errors`.
            _notify_link_failed(on_link_failed, req, exc)
            raise exc

        is_last = index == last_index

        # RECORD-BEFORE-EXTRACT: persist the downloaded clip before decoding it.
        # The frame_path is the planned seed-frame destination; it is filled in
        # below for non-final links, but the clip itself is recorded first so a
        # crash in the download->extract gap resumes at extraction.
        link_result = _build_link_result(
            index=index, spec=spec, result=result, is_last=is_last, out_dir=out_dir
        )
        if recorder is not None:
            recorder.record_chain_link(link_result)

        # Catalog the link in the `videos` table (parity with t2v/i2v). Uses the
        # link's OWN request (link 0 T2V, links N I2V) + result so the row mode
        # matches. The hook absorbs its own DataStoreError so a post-success
        # persistence failure never aborts the already-paid chain.
        if on_link_completed is not None:
            on_link_completed(req, result)

        if link_result.frame_path is not None:
            prev_frame = await asyncio.to_thread(
                extractor,
                src=result.local_path,
                dst=link_result.frame_path,
                offset_ms=seed_offset_ms,
            )

        results.append(link_result)
        completed_paths.append(result.local_path)
        _log.info(
            "chain_link_completed",
            index=index,
            media_id=link_result.media_id,
            mode=req.mode.value,
            seeded=(index > 0),
        )

    return results
