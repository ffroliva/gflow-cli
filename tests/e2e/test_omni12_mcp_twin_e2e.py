"""Live proof that the MCP queued path carries Omni 1.2 and its duration to the slider.

`tests/e2e/test_migrated_omni12_duration_bdd.py` drives the composer directly. This drives
the surface an agent calls: `gflow_generate_video` enqueues a payload, the worker decodes
it, and the real composer applies it on flow.google.com. A string-matched payload key or a
codec that drops `model`/`duration` type-checks and silently no-ops, which only a run shows.

**Cost: $0 on any account.** The real `apply_video_settings` runs, the live slider is read
back, then the job is aborted before upload or Send. 9s is offered with or without a
Google AI plan. Opt-in: ``-m e2e_auth`` with ``GFLOW_CLI_E2E_PROFILE`` and
``GFLOW_CLI_E2E_PROJECT`` (an existing migrated project).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.api.video import VideoModel

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]


class _StopBeforeUploadError(RuntimeError):
    """Raised after the settings are applied, so nothing is uploaded or sent."""


async def test_mcp_r2v_omni12_duration_reaches_the_live_slider(
    e2e_profile_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    from gflow_cli.mcp import tools

    project = os.environ.get("GFLOW_CLI_E2E_PROJECT", "")
    if not project:
        pytest.skip("GFLOW_CLI_E2E_PROJECT must name an existing migrated project")
    ref = tmp_path / "ref.png"
    Image.new("RGB", (64, 64), (40, 120, 200)).save(ref)

    seen: dict[str, Any] = {}
    real_apply = MigratedComposer.apply_video_settings

    async def apply_then_stop(self: MigratedComposer, page: Any, request: Any) -> None:
        await real_apply(self, page, request)
        seen["model"], seen["duration"] = request.model, request.duration
        pane = await self._open_pane(page)  # pyright: ignore[reportPrivateUsage]
        try:
            slider = pane.locator("flow-duration-slider input[type='range']")
            seen["index"] = await slider.input_value()
        finally:
            await self._close_pane(page, strict=True)  # pyright: ignore[reportPrivateUsage]
        raise _StopBeforeUploadError("settings applied; aborting before upload or Send")

    monkeypatch.setattr(MigratedComposer, "apply_video_settings", apply_then_stop)

    result = await tools.gflow_generate_video(
        prompt="unused: never submitted",
        mode="r2v",
        reference_images=[str(ref)],
        model="omni_1_2_flash",
        duration=9,
        profile=os.environ["GFLOW_CLI_E2E_PROFILE"].strip(),
        project=project,
        wait=True,
    )

    assert seen, f"the worker never reached the composer settings: {result}"
    assert seen["model"] is VideoModel.OMNI_1_2_FLASH
    assert seen["duration"] == 9
    assert seen["index"] == "6", f"slider bound index {seen['index']}, wanted 6 (9s)"
    assert result.get("status") != "completed", result
