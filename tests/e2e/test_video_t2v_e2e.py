"""E2E smoke test for video T2V (text-to-video) via UiAutomationTransport.

Hits the **real Google Flow API** and therefore:
  - Is NOT collected by default ``pytest`` runs.
  - Opt-in: ``GFLOW_CLI_E2E_PROFILE=<profile_name> pytest -m e2e``
  - Requires a logged-in Chrome profile (Pro/Ultra account).
  - Burns one Flow credit per run — do not run in CI without gating.

Criterion covered:
  T2V-1 — generate_video(T2V) returns a terminal SUCCESSFUL VideoStatus
           with a non-empty media_id. Aspect is selected from the
           ``GFLOW_CLI_E2E_VIDEO_ASPECT`` env var (default: ``landscape``).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import pytest_asyncio  # noqa: F401 — ensures asyncio mode is registered

from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoResult, VideoStatus

# ---------------------------------------------------------------------------
# Module-level marker — every test in this file is e2e (opt-in only)
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.e2e

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_E2E_PROFILE_ENV = "GFLOW_CLI_E2E_PROFILE"
_E2E_ASPECT_ENV = "GFLOW_CLI_E2E_VIDEO_ASPECT"

# Short, safe prompt — generic enough to pass content-policy, visual enough
# to confirm T2V is actually generating footage.
_PROMPT = "a calm forest at dawn, cinematic"

# Poll timeout generous for real Flow T2V latency (typically 60-180 s,
# up to 600 s in the worst case).
_POLL_TIMEOUT_S = 600.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _profile_dir() -> Path:
    """Resolve the Chromium profile directory from the environment variable.

    Uses gflow-cli's real profile-dir resolver (``gflow_cli.auth.profile_dir``)
    which is ``platformdirs``-based — on Windows this is
    ``%LOCALAPPDATA%\\<author>\\gflow-cli\\profile_<name>``.

    Raises ``pytest.skip`` when the env var is absent or the profile dir
    does not exist (e.g. the user has not yet run ``gflow auth login --profile``).
    """
    name = os.environ.get(_E2E_PROFILE_ENV, "")
    if not name:
        pytest.skip(
            f"E2E tests require {_E2E_PROFILE_ENV} env var — "
            "set it to a logged-in Chromium profile name and re-run with -m e2e"
        )
    from gflow_cli.auth import profile_dir as _resolve_profile_dir

    candidate = _resolve_profile_dir(name)
    if not candidate.exists():
        pytest.skip(
            f"Profile directory not found: {candidate}. "
            f"Run `gflow auth login --profile {name}` to create it."
        )
    return candidate


def _aspect() -> Aspect:
    """Resolve the requested aspect from the environment variable.

    Defaults to LANDSCAPE. Skips on unrecognised values so a typo doesn't
    silently fall back to the default and burn a credit on the wrong ratio.
    """
    raw = os.environ.get(_E2E_ASPECT_ENV, "landscape").strip().lower()
    if raw == "landscape":
        return Aspect.LANDSCAPE
    if raw == "portrait":
        return Aspect.PORTRAIT
    pytest.skip(f"Unsupported {_E2E_ASPECT_ENV}={raw!r} — set to 'landscape' or 'portrait'")


# ---------------------------------------------------------------------------
# Criterion T2V-1 — T2V generation returns SUCCESSFUL VideoStatus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2v_generates_video(tmp_path: Path) -> None:
    """T2V-1: generate_video() with T2V returns a VideoResult whose nested
    VideoStatus is terminal SUCCESSFUL with a non-empty media_id, and whose
    local_path points to a downloaded mp4.

    Aspect is selected via ``$GFLOW_CLI_E2E_VIDEO_ASPECT`` (default
    ``landscape``). ``tmp_path`` is used as ``out_dir`` so any debug
    screenshots land in pytest's temp directory.
    """
    profile = _profile_dir()
    aspect = _aspect()

    req = GenerateVideoRequest(
        prompt=_PROMPT,
        mode=Mode.T2V,
        aspect=aspect,
    )

    transport = UiAutomationTransport()
    try:
        await transport.setup(profile)
        result: VideoResult = await transport.generate_video(
            request=req,
            out_dir=tmp_path,
            poll_timeout_s=_POLL_TIMEOUT_S,
        )
    finally:
        await transport.teardown()

    assert isinstance(result, VideoResult), (
        f"generate_video() must return a VideoResult, got {type(result)!r}"
    )
    assert isinstance(result.status, VideoStatus), (
        f"VideoResult.status must be a VideoStatus, got {type(result.status)!r}"
    )
    assert result.status.is_terminal, (
        f"VideoStatus must be terminal after generate_video() returns; "
        f"status={result.status.status!r}"
    )
    assert result.status.succeeded, (
        f"Expected SUCCESSFUL terminal status, got {result.status.status!r}. "
        f"failure_reasons={result.status.failure_reasons!r}, "
        f"error_message={result.status.error_message!r}"
    )
    assert result.status.media_id, (
        "VideoStatus.media_id must be non-empty for a successful generation"
    )
    assert result.local_path is not None and result.local_path.exists(), (
        f"VideoResult.local_path must point to a downloaded mp4; got {result.local_path!r}"
    )
