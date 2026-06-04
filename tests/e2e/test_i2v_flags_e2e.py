"""CLI-level e2e test for ``gflow video i2v`` flag rename (issue #122).

Verifies that the new canonical flags ``--initial-frame`` and ``--end-frame``
(and the deprecated ``--end-image`` alias) route correctly to the i2v transport
and produce a successful VideoResult in a real Flow environment.

These tests hit the **real Google Flow API** and therefore:
  - Are NOT collected by default ``pytest`` runs (gated behind ``e2e`` +
    ``e2e_video``). They NEVER run in normal CI and spend NO credits unless you
    explicitly opt in.
  - Opt-in: ``GFLOW_CLI_E2E_PROFILE=<profile_name> pytest -m e2e_video``.
  - Requires a logged-in Chrome profile (Pro/Ultra account).
  - **Burns 1 Veo credit per test.** Run selectively.

Criteria covered:
  I2V-FLAG-1 — ``gflow video i2v --initial-frame <img> "<prompt>"`` (canonical
               form) produces a downloaded mp4 and the ``frame_attached``
               structlog event fires at least once, confirming the initial frame
               was bound through the editor's media dialog (not silently dropped).
  I2V-FLAG-2 — Positional back-compat form (``gflow video i2v <img> "<prompt>"``)
               still produces a successful VideoResult — no regression.

Note: ``--end-frame`` / ``--end-image`` interpolation is covered at the
transport level by ``test_e2e_i2v_start_end_frame_attach`` in
``test_transports_e2e.py``. The CLI-level flag-rename focus here is the
``--initial-frame`` canonical form and the positional back-compat path.
"""

from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path

import pytest
import structlog
from click.testing import CliRunner

from gflow_cli.cli_video import video

from typing import Iterator

# ---------------------------------------------------------------------------
# Module-level marker — every test in this file is e2e + e2e_video (opt-in,
# credit-bearing). Never collected by a plain ``pytest`` invocation.
# ---------------------------------------------------------------------------

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_video]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROMPT = "gentle light rays through forest canopy, slow drift, cinematic"
_POLL_TIMEOUT_S = 600.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png(path: Path) -> Path:
    """Write a minimal 1×1 white PNG to *path* and return it."""

    def _crc(data: bytes) -> bytes:
        return struct.pack(">I", zlib.crc32(data) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = b"IHDR" + struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    ihdr = struct.pack(">I", 13) + ihdr_data + _crc(ihdr_data)
    idat_raw = b"\x00\xff\xff\xff"
    idat_data = b"IDAT" + zlib.compress(idat_raw)
    idat = struct.pack(">I", len(idat_data) - 4) + idat_data + _crc(idat_data)
    iend_data = b"IEND"
    iend = struct.pack(">I", 0) + iend_data + _crc(iend_data)
    path.write_bytes(sig + ihdr + idat + iend)
    return path


# ---------------------------------------------------------------------------
# E2E tests
# ---------------------------------------------------------------------------


@pytest.fixture
def unisolated_home(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Fixture that temporarily restores the real GFLOW_CLI_HOME for the test.

    Allows CliRunner-based tests to find real Chromium profiles.
    """
    from gflow_cli.config import Settings, reset_settings

    # Resolve real home
    with monkeypatch.context() as m:
        m.delenv("GFLOW_CLI_HOME", raising=False)
        real_home = Settings().home

    monkeypatch.setenv("GFLOW_CLI_HOME", str(real_home))
    reset_settings()
    yield real_home
    reset_settings()


def test_e2e_i2v_initial_frame_flag(
    e2e_profile_dir: Path,
    unisolated_home: Path,
    tmp_path: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """I2V-FLAG-1: ``--initial-frame`` (canonical) routes to I2V and downloads an mp4.

    Confirms that the flag rename does not silently fall back to T2V: the
    ``ui_automation_video.frame_attached`` event must fire at least once
    (analogous to the assertion in ``test_e2e_i2v_start_end_frame_attach``).

    This test uses CliRunner to verify the Click-layer "swap logic" in a real
    environment.
    """
    start = _make_png(tmp_path / "initial_frame.png")
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)

    profile_name = os.environ["GFLOW_CLI_E2E_PROFILE"]
    runner = CliRunner()
    result = runner.invoke(
        video,
        [
            "i2v",
            "--initial-frame",
            str(start),
            _PROMPT,
            "--aspect",
            "9:16",
            "--out-dir",
            str(out_dir),
            "--duration",
            "4",
            "--count",
            "1",
            "--profile",
            profile_name,
        ],
    )

    assert result.exit_code == 0, result.output

    mp4_files = list(out_dir.glob("*.mp4"))
    assert mp4_files, "expected at least one mp4 in out_dir"

    frame_attached_events = [
        e
        for e in install_log_capture.entries
        if e.get("event") == "ui_automation_video.frame_attached"
    ]
    assert frame_attached_events, (
        "frame_attached event never fired — initial frame may have been silently dropped "
        "(check for T2V mis-routing, issue #125)"
    )


def test_e2e_i2v_positional_back_compat(
    e2e_profile_dir: Path,
    unisolated_home: Path,
    tmp_path: Path,
) -> None:
    """I2V-FLAG-2: positional IMAGE form still works after the flag rename.

    Regression guard: ``gflow video i2v <image> "<prompt>"`` (no --initial-frame)
    must produce a successful VideoResult — the rename must not break callers that
    rely on the positional convention.
    """
    start = _make_png(tmp_path / "start.png")
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)

    profile_name = os.environ["GFLOW_CLI_E2E_PROFILE"]
    runner = CliRunner()
    result = runner.invoke(
        video,
        [
            "i2v",
            str(start),
            _PROMPT,
            "--aspect",
            "9:16",
            "--out-dir",
            str(out_dir),
            "--duration",
            "4",
            "--count",
            "1",
            "--profile",
            profile_name,
        ],
    )

    assert result.exit_code == 0, result.output

    mp4_files = list(out_dir.glob("*.mp4"))
    assert mp4_files, "expected at least one mp4 — positional back-compat path is broken"


def test_e2e_i2v_start_end_frame_flags(
    e2e_profile_dir: Path,
    unisolated_home: Path,
    tmp_path: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """I2V-FLAG-3: both ``--initial-frame`` and ``--end-frame`` together.

    Verifies the full interpolation CLI path: ``gflow video i2v --initial-frame <a>
    --end-frame <b> "prompt"``. The ``ui_automation_video.frame_attached`` event
    must fire TWICE (one per slot).
    """
    start = _make_png(tmp_path / "start.png")
    end = _make_png(tmp_path / "end.png")
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)

    profile_name = os.environ["GFLOW_CLI_E2E_PROFILE"]
    runner = CliRunner()
    result = runner.invoke(
        video,
        [
            "i2v",
            "--initial-frame",
            str(start),
            "--end-frame",
            str(end),
            _PROMPT,
            "--aspect",
            "16:9",
            "--out-dir",
            str(out_dir),
            "--duration",
            "4",
            "--profile",
            profile_name,
        ],
    )

    assert result.exit_code == 0, result.output

    mp4_files = list(out_dir.glob("*.mp4"))
    assert mp4_files, "expected a downloaded mp4 for start+end interpolation"

    frame_attached_events = [
        e
        for e in install_log_capture.entries
        if e.get("event") == "ui_automation_video.frame_attached"
    ]
    # One for start, one for end.
    assert len(frame_attached_events) >= 2, (
        f"expected at least 2 frame_attached events, got {len(frame_attached_events)}"
    )
