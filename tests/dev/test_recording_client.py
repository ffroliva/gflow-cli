"""Tests for the dev-scoped ``RecordingFlowApiClient`` subclass.

The subclass lives under ``scripts/dev`` (NOT in the ``gflow_cli`` package) and
adds Playwright video recording to the client's persistent context purely via
the core ``_persistent_context_kwargs()`` seam — so no recording concern leaks
into core.
"""

from __future__ import annotations

import sys
from pathlib import Path

_DEV = Path(__file__).resolve().parents[2] / "scripts" / "dev"
if str(_DEV) not in sys.path:
    sys.path.insert(0, str(_DEV))

from _recording_client import RecordingFlowApiClient  # noqa: E402


def test_recording_client_injects_video_and_preserves_base(tmp_path: Path) -> None:
    rec_dir = tmp_path / "rec"
    client = RecordingFlowApiClient(profile_dir=tmp_path, headless=True, record_video_dir=rec_dir)
    kwargs = client._persistent_context_kwargs()  # noqa: SLF001
    # Recording kwargs injected:
    assert kwargs["record_video_dir"] == str(rec_dir)
    assert kwargs["record_video_size"] == {"width": 1280, "height": 720}
    # Base kwargs preserved untouched:
    assert kwargs["user_data_dir"] == str(tmp_path)
    assert kwargs["headless"] is True
    assert kwargs["args"] == ["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"]
