"""Unit tests for gflow_cli.chain — sequential last-frame I2V orchestrator (Task 1, RED).

================================================================================
run_chain SIGNATURE CONTRACT (Task 7 MUST conform to this — keep it stable)
================================================================================

    async def run_chain(
        *,
        client: FlowApiClient,                 # mocked here; async generate_video(req=...)
        links: list[ChainLinkSpec],            # ordered per-link prompts/overrides
        out_dir: Path,                         # where clips + seed frames land
        model: VideoModel,                     # MUST support i2v; else reject up front
        extractor: FrameExtractor = ...,       # (src, dst, *, offset_ms=0) -> Path
        recorder: ChainRecorder | None = None, # record_chain_link(...) BEFORE extraction
        aspect: Aspect = Aspect.PORTRAIT,
        seed_offset_ms: int = 0,
        jitter: float = 0.0,
    ) -> list[ChainLinkResult]

Behavioural contract asserted below:
  * Links run STRICTLY sequentially (concurrency=1); generate_video is awaited
    once per link in order.
  * Link 0 is T2V (no start_image); link N>0 is I2V whose ``start_image`` is the
    extracted last frame of link N-1.
  * A per-link ``WireFormatError`` (i2v silently routed to t2v backstop) ABORTS
    the chain and raises ``ChainPartialError(partial_results=[...])`` carrying the
    Paths of the links completed BEFORE the failure.
  * RECORD-BEFORE-EXTRACT: for each completed link the recorder is invoked
    (clip persisted) BEFORE the extractor runs on that clip — so a crash in the
    download->extract gap resumes at extraction, never re-generates.
  * A non-interpolation model (e.g. omni_flash) is REJECTED up front
    (``ModelModeIncompatibilityError``) before any generate_video call fires.

Types referenced (defined by Task 7 in chain.py): ``ChainLinkSpec`` (prompt +
optional model/duration/aspect override) and ``ChainLinkResult`` (carries
``local_path: Path`` + ``media_id``). The tests below are permissive about the
exact extra fields and only pin the load-bearing ones.

Until Task 7 lands ``src/gflow_cli/chain.py`` this module fails at import /
collection — that is the EXPECTED red state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.video import (
    Aspect,
    GenerateVideoRequest,
    Mode,
    VideoModel,
    VideoResult,
    VideoStatus,
)
from gflow_cli.chain import ChainLinkSpec, run_chain
from gflow_cli.errors import (
    ChainPartialError,
    ModelModeIncompatibilityError,
    WireFormatError,
)


# --------------------------------------------------------------------------- #
# helpers / fixtures
# --------------------------------------------------------------------------- #
def _ok_result(media_id: str, local_path: Path) -> VideoResult:
    """A successful, downloaded VideoResult for one link."""
    return VideoResult(
        status=VideoStatus(media_id=media_id, status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
        local_path=local_path,
        project_id=f"proj-{media_id}",
        flow_operation_id=f"op-{media_id}",
    )


def _make_client(results: list[VideoResult]) -> MagicMock:
    """A mock FlowApiClient whose ``generate_video`` yields ``results`` in order.

    Each call writes its ``local_path`` to disk so a real extractor (if used)
    would have a file — but tests inject a fake extractor, so the file content
    is irrelevant; we still ``touch`` it to mimic a completed download.
    """
    client = MagicMock(name="FlowApiClient")
    results_iter = iter(results)

    async def _gen(*, req: GenerateVideoRequest, **_: Any) -> VideoResult:
        result = next(results_iter)
        if result.local_path is not None:
            result.local_path.parent.mkdir(parents=True, exist_ok=True)
            result.local_path.write_bytes(b"\x00\x00\x00\x18ftypmp42fake-clip")
        return result

    client.generate_video = AsyncMock(side_effect=_gen)
    return client


def _fake_extractor(written: list[Path]) -> Any:
    """A drop-in for ``extract_last_frame``: writes a JPEG-ish file to ``dst``
    and records the call order in ``written``."""

    def _extract(src: Path, dst: Path, *, offset_ms: int = 0) -> Path:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"\xff\xd8\xff\xd9")  # minimal JPEG sentinel
        written.append(dst)
        return dst

    return _extract


def _two_link_specs() -> list[ChainLinkSpec]:
    return [
        ChainLinkSpec(prompt="a cat wakes up"),
        ChainLinkSpec(prompt="the cat stretches and walks off"),
    ]


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #
async def test_links_run_strictly_sequentially(tmp_path: Path) -> None:
    """generate_video is awaited exactly once per link, in link order."""
    results = [
        _ok_result("m0", tmp_path / "link0.mp4"),
        _ok_result("m1", tmp_path / "link1.mp4"),
        _ok_result("m2", tmp_path / "link2.mp4"),
    ]
    client = _make_client(results)
    specs = [
        ChainLinkSpec(prompt="p0"),
        ChainLinkSpec(prompt="p1"),
        ChainLinkSpec(prompt="p2"),
    ]

    out = await run_chain(
        client=client,
        links=specs,
        out_dir=tmp_path,
        model=VideoModel.VEO_3_1_LITE,
        extractor=_fake_extractor([]),
    )

    assert client.generate_video.await_count == 3
    assert len(out) == 3
    # The prompts went out in order.
    sent_prompts = [c.kwargs["req"].prompt for c in client.generate_video.await_args_list]
    assert sent_prompts == ["p0", "p1", "p2"]


async def test_link0_is_t2v_and_subsequent_links_chain_last_frame(tmp_path: Path) -> None:
    """Link 0 is T2V (no start_image); link N's start_image is the extracted
    last frame of link N-1 (the file the extractor wrote after link N-1)."""
    results = [
        _ok_result("m0", tmp_path / "link0.mp4"),
        _ok_result("m1", tmp_path / "link1.mp4"),
    ]
    client = _make_client(results)
    written: list[Path] = []

    await run_chain(
        client=client,
        links=_two_link_specs(),
        out_dir=tmp_path,
        model=VideoModel.VEO_3_1_LITE,
        extractor=_fake_extractor(written),
    )

    reqs = [c.kwargs["req"] for c in client.generate_video.await_args_list]
    assert len(reqs) == 2

    # Link 0: text-to-video, no seed frame.
    assert reqs[0].mode is Mode.T2V
    assert reqs[0].start_image is None

    # Link 1: image-to-video seeded by the frame the extractor produced for link 0.
    assert reqs[1].mode is Mode.I2V
    assert reqs[1].start_image is not None
    assert written, "extractor must have run on link 0 before link 1 generated"
    assert reqs[1].start_image == written[0], (
        "link 1 start_image must be the extracted last frame of link 0"
    )


async def test_record_before_extract_ordering(tmp_path: Path) -> None:
    """RECORD-BEFORE-EXTRACT: each link's clip is persisted via the recorder
    BEFORE the extractor is invoked on that clip.

    A shared ``MagicMock`` (manager) records both the recorder call and the
    extractor call, so ``mock_calls`` preserves their relative ordering.
    """
    results = [
        _ok_result("m0", tmp_path / "link0.mp4"),
        _ok_result("m1", tmp_path / "link1.mp4"),
    ]
    client = _make_client(results)

    manager = MagicMock()
    recorder = MagicMock(name="ChainRecorder")
    recorder.record_chain_link = manager.record  # funnel into the shared manager

    def _spy_extract(src: Path, dst: Path, *, offset_ms: int = 0) -> Path:
        manager.extract(src, dst)
        dst.write_bytes(b"\xff\xd8\xff\xd9")
        return dst

    await run_chain(
        client=client,
        links=_two_link_specs(),
        out_dir=tmp_path,
        model=VideoModel.VEO_3_1_LITE,
        extractor=_spy_extract,
        recorder=recorder,
    )

    # Reduce mock_calls to the ordered sequence of method names.
    order = [c[0] for c in manager.mock_calls if c[0] in ("record", "extract")]
    # For link 0: record MUST precede extract.
    assert order[0] == "record", f"expected record-before-extract, got order={order}"
    assert "extract" in order
    assert order.index("record") < order.index("extract"), (
        f"clip must be persisted before extraction; order={order}"
    )


async def test_aborts_on_wire_format_error_preserving_partial_results(tmp_path: Path) -> None:
    """A per-link WireFormatError (i2v routed to t2v backstop) aborts the chain
    and raises ChainPartialError carrying the Paths of the links completed BEFORE
    the failure. The failing link and any later links are NOT generated."""
    good = _ok_result("m0", tmp_path / "link0.mp4")
    client = MagicMock(name="FlowApiClient")
    calls = 0

    async def _gen(*, req: GenerateVideoRequest, **_: Any) -> VideoResult:
        nonlocal calls
        idx = calls
        calls += 1
        if idx == 0:
            assert good.local_path is not None
            good.local_path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
            return good
        # link 1 silently routed to the text endpoint -> backstop fires.
        raise WireFormatError(
            detail="i2v_routed_to_t2v",
            discovery={"route_name": "batchAsyncGenerateVideoText"},
        )

    client.generate_video = AsyncMock(side_effect=_gen)

    specs = [
        ChainLinkSpec(prompt="p0"),
        ChainLinkSpec(prompt="p1"),
        ChainLinkSpec(prompt="p2"),
    ]

    with pytest.raises(ChainPartialError) as excinfo:
        await run_chain(
            client=client,
            links=specs,
            out_dir=tmp_path,
            model=VideoModel.VEO_3_1_LITE,
            extractor=_fake_extractor([]),
        )

    # Only the first link generated + the failing link were attempted: 2 calls.
    assert client.generate_video.await_count == 2, "must NOT generate link 2 after abort"

    partials = excinfo.value.partial_results
    assert isinstance(partials, list)
    assert len(partials) == 1, "exactly the one completed link is preserved"
    assert all(isinstance(p, Path) for p in partials)
    assert partials[0] == good.local_path


async def test_rejects_non_interpolation_model_up_front(tmp_path: Path) -> None:
    """A non-interpolation model (omni_flash) is rejected BEFORE any spend:
    ModelModeIncompatibilityError, and generate_video is never awaited."""
    client = _make_client([])

    with pytest.raises(ModelModeIncompatibilityError):
        await run_chain(
            client=client,
            links=_two_link_specs(),
            out_dir=tmp_path,
            model=VideoModel.OMNI_FLASH,
            extractor=_fake_extractor([]),
        )

    client.generate_video.assert_not_awaited()


async def test_aspect_propagates_to_every_link(tmp_path: Path) -> None:
    """The chain-level aspect is applied to each generated link (continuity
    requires a uniform aspect across the sequence)."""
    results = [
        _ok_result("m0", tmp_path / "link0.mp4"),
        _ok_result("m1", tmp_path / "link1.mp4"),
    ]
    client = _make_client(results)

    await run_chain(
        client=client,
        links=_two_link_specs(),
        out_dir=tmp_path,
        model=VideoModel.VEO_3_1_LITE,
        aspect=Aspect.LANDSCAPE,
        extractor=_fake_extractor([]),
    )

    aspects = {c.kwargs["req"].aspect for c in client.generate_video.await_args_list}
    assert aspects == {Aspect.LANDSCAPE}
