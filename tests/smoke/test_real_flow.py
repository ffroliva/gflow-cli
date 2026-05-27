"""Golden-path smoke test for :class:`UiAutomationTransport`.

Opts in via ``GFLOW_CLI_E2E_PROFILE`` (the same env var as the full e2e suite)
and the ``smoke`` marker. Costs ~1 Imagen credit per run.

Required environment variable:
  GFLOW_CLI_E2E_PROFILE — name of a Playwright user-data-dir already signed in
                          to Flow on a Pro or Ultra Google account. The directory
                          must exist at the path returned by
                          ``profile_store.profile_dir(name)``.

Optional environment variable:
  GFLOW_CLI_E2E_PROMPT — prompt text to submit. Defaults to a safe placeholder
                         that passes content-policy without being account-specific.

Run with::

    GFLOW_CLI_E2E_PROFILE=<name> pytest -m smoke tests/smoke/ -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from structlog.testing import LogCapture

pytestmark = pytest.mark.smoke

_E2E_PROFILE_ENV = "GFLOW_CLI_E2E_PROFILE"
_DEFAULT_PROMPT = "a quiet mountain lake at dawn, cinematic photography"


@pytest.fixture
def smoke_profile_dir() -> Path:
    """Resolve the Chromium profile directory from ``GFLOW_CLI_E2E_PROFILE``.

    Skips the test when the env var is unset or the profile directory is absent.
    """
    name = os.environ.get(_E2E_PROFILE_ENV, "")
    if not name:
        pytest.skip(
            f"Smoke tests require {_E2E_PROFILE_ENV} — set it to a logged-in "
            "profile name and re-run with -m smoke"
        )
    from gflow_cli import auth as auth_mod

    profile_dir = auth_mod.profile_dir(name)
    if not profile_dir.exists():
        pytest.skip(
            f"Profile directory not found: {profile_dir}. "
            f"Run `gflow auth login --profile {name}` first."
        )
    return profile_dir


@pytest.mark.asyncio
async def test_ui_automation_ships_one_image(
    smoke_profile_dir: Path,
    tmp_path: Path,
    install_log_capture: LogCapture,
) -> None:
    """Golden path: open Flow, submit one prompt, save the resulting PNG.

    Asserts:
    1. The PNG was written to disk.
    2. The file is at least 100 kB — Flow's generated images are routinely
       > 500 kB; the 100 kB floor catches a truncated download stream.
    3. The PNG has valid magic bytes (\\x89PNG).
    4. The ``ui_automation.image_mode_entered`` structlog event was emitted,
       confirming the transport navigated into image mode before generation.
    """
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.image import Aspect, GenerateImageRequest, Model

    prompt_text = os.environ.get("GFLOW_CLI_E2E_PROMPT", _DEFAULT_PROMPT)
    req = GenerateImageRequest(
        prompt=prompt_text,
        aspect=Aspect.PORTRAIT,
        model=Model.NARWHAL,
    )

    async with FlowApiClient(
        profile_dir=smoke_profile_dir, headless=False, transport="ui_automation"
    ) as client:
        project = await client.create_project(title="gflow-cli smoke")
        image = await client.generate_image(project_id=project.project_id, req=req)
        target = tmp_path / "smoke_output.png"
        saved = await client.download_image(image, target)

    assert saved.exists(), f"Expected PNG at {saved}, none written."
    size = saved.stat().st_size
    assert size >= 100_000, (
        f"PNG at {saved} is suspiciously small: {size} bytes. Possible truncated stream."
    )
    assert saved.read_bytes()[:4] == b"\x89PNG", (
        f"File at {saved} does not start with PNG magic bytes."
    )

    mode_events = [
        e for e in install_log_capture.entries if e["event"] == "ui_automation.image_mode_entered"
    ]
    assert mode_events, (
        "Expected at least one 'ui_automation.image_mode_entered' structlog event; "
        "none found. The transport may have skipped image-mode navigation."
    )
