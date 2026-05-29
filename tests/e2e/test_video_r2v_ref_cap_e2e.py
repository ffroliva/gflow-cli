"""E2E test for per-model r2v reference-image cap.

Verifies that ``VideoModel.VEO_3_1_LITE`` accepts the full 3-ref boundary
against the real Flow API — Flow silently keeps only the first N refs when
more are attached, so a "wrong cap" bug shows up as: the generate call
succeeds but ``ui_automation_video.reference_attached`` fires fewer than
the requested N times. Asserting on the structured event closes that
false-positive class. Costs 1 Veo credit.

Skipped by default; opt in with::

    GFLOW_CLI_E2E_PROFILE=<profile-name> uv run pytest -m e2e_video -v \\
        tests/e2e/test_video_r2v_ref_cap_e2e.py

The cap-reject paths (4 refs against veo-lite, 1 ref against veo-quality
where cap=0 means R2V is unsupported) are covered by unit + CLI tests
(`tests/api/test_video.py`, `tests/cli/test_cli_video.py`) and spend
zero credits.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest
import structlog

from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.video import (
    Aspect,
    GenerateVideoRequest,
    Mode,
    VideoModel,
    VideoResult,
    reference_cap_for,
)

pytestmark = pytest.mark.e2e

_PROMPT = "a colorful scene with the references combined, cinematic"
_R2V_POLL_TIMEOUT_S = 600.0


def _tiny_png(path: Path, color: tuple[int, int, int]) -> Path:
    """Write a valid 8x8 RGBA PNG of the given color."""

    def _chunk(typ: bytes, data: bytes) -> bytes:
        body = typ + data
        crc = zlib.crc32(body) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + body + struct.pack(">I", crc)

    w = h = 8
    r, g, b = color
    raw = b"".join(b"\x00" + bytes((r, g, b, 0xFF)) * w for _ in range(h))
    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += _chunk(b"IDAT", zlib.compress(raw))
    png += _chunk(b"IEND", b"")
    path.write_bytes(png)
    return path


@pytest.mark.asyncio
@pytest.mark.e2e_video
async def test_e2e_r2v_at_cap_veo_lite_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """`veo-lite` at its 3-ref cap returns a SUCCESSFUL VideoResult AND Flow
    actually consumes all 3 refs (one `reference_attached` event per ref).

    If Flow's actual cap is lower than ``reference_cap_for(VEO_3_1_LITE)``
    (3), fewer events fire and the test FAILS — a tripwire for cap drift on
    the Flow side. Picking `veo-lite` over `veo-fast` to keep the credit
    burn on the cheaper tier; the cap (3) is identical across
    lite/fast/lite_lp on the Flow wire.
    """
    # Undo the autouse `_isolate_settings` fixture (tests/conftest.py) so
    # profile lookup resolves to the user's real platformdirs path (where the
    # live Chrome session was planted by `gflow auth login`). Isolation is the
    # right default for non-e2e but breaks e2e session-dependent tests.
    import os

    from gflow_cli.auth import profile_dir as _resolve_profile_dir
    from gflow_cli.config import reset_settings

    monkeypatch.delenv("GFLOW_CLI_HOME", raising=False)
    monkeypatch.delenv("GFLOW_CLI_DB_PATH", raising=False)
    reset_settings()

    name = os.environ.get("GFLOW_CLI_E2E_PROFILE", "").strip()
    if not name:
        pytest.skip("set GFLOW_CLI_E2E_PROFILE to a logged-in profile name")
    e2e_profile_dir = _resolve_profile_dir(name)
    if not e2e_profile_dir.exists():
        pytest.skip(f"profile dir not found: {e2e_profile_dir}")

    cap = reference_cap_for(VideoModel.VEO_3_1_LITE)
    assert cap == 3, f"unexpected veo-lite cap {cap}; e2e wired for 3"
    refs = tuple(
        _tiny_png(tmp_path / f"r{i}.png", color=(40 + 60 * i, 80 + 40 * i, 160 - 30 * i))
        for i in range(cap)
    )

    req = GenerateVideoRequest(
        prompt=_PROMPT,
        mode=Mode.R2V,
        aspect=Aspect.PORTRAIT,
        model=VideoModel.VEO_3_1_LITE,
        duration=8,  # veo_3_1_lite only supports 8s
        count=1,
        reference_images=refs,
    )

    transport = UiAutomationTransport()
    try:
        await transport.setup(e2e_profile_dir)
        result: VideoResult = await transport.generate_video(
            request=req,
            out_dir=tmp_path,
            poll_timeout_s=_R2V_POLL_TIMEOUT_S,
        )
    finally:
        await transport.teardown()

    # 1. Terminal-success contract.
    assert isinstance(result, VideoResult), (
        f"generate_video() must return a VideoResult, got {type(result)!r}"
    )
    assert result.status.is_terminal and result.status.succeeded, (
        f"Expected SUCCESSFUL terminal status, got {result.status.status!r}; "
        f"failure_reasons={result.status.failure_reasons!r}"
    )
    assert result.status.media_id, "VideoStatus.media_id must be non-empty"

    # 2. File-on-disk contract (per [[verification-ledger-5-layer]]).
    assert result.local_path is not None and result.local_path.exists(), (
        f"VideoResult.local_path must point to a downloaded mp4; got {result.local_path!r}"
    )
    head = result.local_path.read_bytes()[:32]
    assert b"ftyp" in head, (
        f"mp4 magic bytes not found in first 32 bytes of {result.local_path}: {head!r}"
    )

    # 3. Cap-drift tripwire: Flow must report `reference_attached` for ALL
    # `cap` refs. If only N < cap fire, Flow silently truncated and our cap
    # value is too generous — bumping `reference_cap_for(VEO_3_1_LITE)` would
    # invite re-introduction of the bug this PR closes.
    attached = [
        e
        for e in install_log_capture.entries
        if e["event"] == "ui_automation_video.reference_attached"
    ]
    assert len(attached) == cap, (
        f"expected {cap} reference_attached events at the at-cap boundary; "
        f"got {len(attached)}. Flow likely truncated silently — verify the "
        f"`reference_cap_for(VEO_3_1_LITE)` value matches Flow's actual cap."
    )
