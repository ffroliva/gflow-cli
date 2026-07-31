"""Live e2e regression test for issue #404: Flow renamed the classic
composer's count-tab labels from ``1x``/``x2``/… to ``x1``/``x2``/…, which
broke ``_set_count``'s label filter — ``-n 1`` (the CLI default) clicked the
wrong tab, logged per-click success, and the run died with an opaque
``UnexpectedError`` before any generation was submitted.

Spends Imagen quota (0 Flow credits — only video costs credits). Skipped by
default; opt in with ``GFLOW_CLI_E2E_PROFILE=<profile>`` and ``-m e2e_image``.

The test mirrors the reporter's invocation: classic UI mode, real t2i path,
then asserts exactly the requested count comes back. A fresh project starts
at Flow's default of 2 displayed images, so ``count=1`` reproduces the
requested != displayed precondition of #404 (the renamed ``x1`` tab must be
found and clicked); ``count=2`` covers the already-matching early-exit.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import structlog

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import Aspect, GenerateImageRequest, Model

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_image]

_E2E_PROFILE_ENV = "GFLOW_CLI_E2E_PROFILE"


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 2])
async def test_classic_t2i_generates_exactly_the_requested_count(
    count: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """LIVE (#404): classic-mode t2i with ``-n <count>`` must generate exactly
    ``count`` images. count=1 is the regression case — Flow displays 2 by
    default, so the count setter must locate and click the renamed x1 tab."""
    from gflow_cli.config import reset_settings

    monkeypatch.delenv("GFLOW_CLI_HOME", raising=False)
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(tmp_path / f"e2e_404_{count}.db"))
    # The reporter's invocation ran --ui-mode classic; force it deterministically
    # regardless of the server-side A/B cohort flap.
    monkeypatch.setenv("GFLOW_CLI_UI_MODE", "classic")
    reset_settings()

    name = os.environ.get(_E2E_PROFILE_ENV, "").strip()
    if not name:
        pytest.skip(f"set {_E2E_PROFILE_ENV} to a logged-in profile, run with -m e2e_image")
    from gflow_cli.auth import profile_dir as _resolve_profile_dir

    profile = _resolve_profile_dir(name)
    if not profile.exists():
        pytest.skip(f"profile not found: {profile} — run `gflow auth login --profile {name}`")

    # Mirror the reporter's environment: 9:16 aspect, nano2 (NARWHAL) model.
    req = GenerateImageRequest(
        prompt=f"a small ceramic bowl on a wooden table, e2e variant count-{count}",
        aspect=Aspect.PORTRAIT,
        model=Model.NARWHAL,
    )

    async with FlowApiClient(profile_dir=profile, out_dir=tmp_path) as client:
        project = await client.create_project(title=f"gflow-cli e2e 404 count-{count}")
        images = await client.generate_images_batch(
            project_id=project.project_id, req=req, count=count
        )

    assert len(images) == count, (
        f"requested {count} images but got {len(images)} back — the classic "
        f"count setter failed to converge on the live composer (#404)"
    )

    completed = [
        e
        for e in install_log_capture.entries
        if e.get("event") == "ui_automation.count_setter_completed"
    ]
    assert completed, "expected the classic count setter to run and log completion"
    final = completed[-1]
    assert final.get("success") is True, (
        f"count setter did not converge on the live composer: {final}"
    )
