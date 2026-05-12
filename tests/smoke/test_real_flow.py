"""Opt-in real-Flow smoke test for :class:`UiAutomationTransport`.

This test is gated by the ``GFLOW_E2E=1`` environment variable. It launches
a real Playwright Chromium against a pre-authenticated Flow profile dir
and verifies the strategy produces an image PNG on disk. The whole flow
is the same one that ``scripts/smoke_worker_style.py`` ran on
2026-05-12 to validate D.2.4.

Required environment variables when ``GFLOW_E2E=1`` is set:

- ``GFLOW_E2E_PROFILE`` — name of a Playwright user-data-dir already
  signed in to Flow on a Pro or Ultra Google account. The directory
  must exist at the path returned by ``profile_store.profile_dir(name)``.
- ``GFLOW_E2E_PROMPT`` (optional) — prompt text to submit. Defaults to a
  generic placeholder so the test does not depend on a particular
  account's content policy.

The default suite skips this test. CI / contributors who want to run it
provide the env vars explicitly.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("GFLOW_E2E") != "1",
    reason="Real-Flow E2E — set GFLOW_E2E=1 to opt in.",
)


_DEFAULT_PROMPT = "a quiet mountain lake at dawn, cinematic photography"


@pytest.mark.asyncio
async def test_ui_automation_ships_one_image(tmp_path: Path) -> None:
    """End-to-end: open Flow, submit one prompt, save the resulting PNG.

    Asserts the PNG was written and is at least 100 kB — Flow's generated
    images are routinely > 500 kB; the 100 kB floor catches the case
    where ``batchGenerateImages`` returned 200 but the download stream
    truncated.
    """
    from gflow_cli import auth as auth_mod
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.image import Aspect, GenerateImageRequest, Model

    profile_name = os.getenv("GFLOW_E2E_PROFILE")
    if not profile_name:
        pytest.fail(
            "GFLOW_E2E_PROFILE must be set when GFLOW_E2E=1 — name of a "
            "Playwright user-data-dir already signed in to Flow."
        )

    profile_dir = auth_mod.profile_dir(profile_name)
    if not profile_dir.exists():
        pytest.fail(
            f"Profile dir does not exist: {profile_dir}. Run "
            f"`gflow auth login --profile {profile_name}` first."
        )

    prompt_text = os.getenv("GFLOW_E2E_PROMPT", _DEFAULT_PROMPT)
    req = GenerateImageRequest(
        prompt=prompt_text,
        aspect=Aspect.PORTRAIT,
        model=Model.NARWHAL,
    )

    # Pass the transport key (string) — FlowApiClient resolves via the
    # factory and owns the setup/teardown lifecycle. Passing a pre-built
    # instance would put lifecycle ownership on the caller, which is the
    # advanced-use path and not what the smoke needs.
    async with FlowApiClient(
        profile_dir=profile_dir, headless=False, transport="ui_automation"
    ) as client:
        project = await client.create_project(title="gflow-cli e2e smoke")
        image = await client.generate_image(project_id=project.project_id, req=req)
        target = tmp_path / "smoke_output.png"
        saved = await client.download_image(image, target)

    assert saved.exists(), f"Expected PNG at {saved}, none written."
    size = saved.stat().st_size
    assert size >= 100_000, (
        f"PNG at {saved} is suspiciously small: {size} bytes. Possible truncated stream."
    )
