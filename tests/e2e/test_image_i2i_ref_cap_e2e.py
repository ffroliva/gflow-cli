"""E2E test for per-model i2i reference-image cap.

Verifies that ``Model.NARWHAL`` (nano-banana-2) accepts the full 10-ref
boundary against the real Flow API — Flow silently keeps only the first
N refs when more are attached, so a "wrong cap" bug shows up as: the
generate call succeeds but ``ui_automation_video.reference_attached``
fires fewer than the requested N times. Asserting on the structured
event closes that false-positive class. Runs 1 image generation (zero credits).

Skipped by default; opt in with::

    GFLOW_CLI_E2E_PROFILE=<profile-name> uv run pytest -m e2e_image -v \\
        tests/e2e/test_image_i2i_ref_cap_e2e.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest
import structlog

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import GenerateImageRequest, Model, reference_cap_for

pytestmark = pytest.mark.e2e

_PROMPT = "place all references in a single colorful collage"


def _tiny_png(path: Path, color: tuple[int, int, int]) -> Path:
    """Write a valid 8x8 RGBA PNG of the given color. Lifted from
    ``test_transports_e2e._tiny_png`` so this file is self-contained."""

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
@pytest.mark.e2e_image
async def test_e2e_i2i_at_cap_nano2_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """`nano2` at its 10-ref cap returns a valid image AND Flow actually
    consumes all 10 refs (one `reference_attached` event per ref).

    If Flow's actual cap is lower than ``reference_cap_for(NARWHAL)`` (10),
    fewer events fire and the test FAILS — a tripwire for cap drift on the
    Flow side. The cap-reject path (11 refs) is covered by unit + CLI tests
    (`tests/api/test_image.py`, `tests/cli/test_cli_image.py`) and does not
    spend a credit.
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

    cap = reference_cap_for(Model.NARWHAL)
    refs = tuple(
        _tiny_png(tmp_path / f"r{i}.png", color=(40 + 20 * i, 80, 160 - 10 * i)) for i in range(cap)
    )
    req = GenerateImageRequest(prompt=_PROMPT, model=Model.NARWHAL, ref_paths=refs)

    async with FlowApiClient(profile_dir=e2e_profile_dir) as client:
        project = await client.create_project(title=f"e2e-i2i-cap-{cap}")
        image = await client.generate_image(project_id=project.project_id, req=req)

    assert image.media_name, "i2i at-cap returned no image"
    assert image.fife_url.startswith("https://"), (
        f"fife_url must be an https:// URL, got: {image.fife_url!r}"
    )

    # The cap-drift tripwire: Flow must report `reference_attached` for ALL
    # `cap` refs. If only N < cap fire, Flow silently truncated and our cap
    # value is too generous — bumping `reference_cap_for(NARWHAL)` would
    # invite re-introduction of the bug this PR closes.
    attached = [
        e
        for e in install_log_capture.entries
        if e["event"] == "ui_automation_video.reference_attached"
    ]
    assert len(attached) == cap, (
        f"expected {cap} reference_attached events at the at-cap boundary; "
        f"got {len(attached)}. Flow likely truncated silently — verify the "
        f"`reference_cap_for(NARWHAL)` value matches Flow's actual cap."
    )
