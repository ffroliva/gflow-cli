"""E2E tests for video generation: T2V (text-to-video) and I2V (image-to-video).

These tests hit the **real Flow API** and therefore:
  - Are NOT collected by default ``pytest`` runs.
  - Opt-in: ``GFLOW_CLI_E2E_PROFILE=<profile_name> pytest -m e2e -k video``
  - Require the named Chromium profile to be logged-in (a Pro/Ultra account).

Criteria covered:
  CV1 — T2V: submitting a text prompt returns a pending VideoOperation
  CV2 — I2V: uploading an image then submitting a prompt returns a pending
             VideoOperation whose model key reflects i2v mode
  CV3 — upload: upload_image() returns an AssetInfo with a non-empty UUID
               and correct pixel dimensions

All three tests use the same strategy set as the image e2e suite.  Their
primary purpose is to tell us definitively whether the REST transport routes
(batchAsyncGenerateVideoText, uploadImage) are 401-blocked or working — the
UiAutomationTransport path for video will be built next once we know.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Tier

# ---------------------------------------------------------------------------
# Module-level marker — every test in this file inherits e2e
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.e2e

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STRATEGIES = ["evaluate_fetch", "bearer", "sapisidhash"]

_E2E_PROFILE_ENV = "GFLOW_CLI_E2E_PROFILE"

# Committed PNG used as the reference image for I2V tests.
_TEST_IMAGE = Path(__file__).parent.parent.parent / "test_assets" / "image_00.png"


# ---------------------------------------------------------------------------
# Helpers (mirrors test_transports_e2e.py)
# ---------------------------------------------------------------------------


def _profile_dir() -> Path:
    name = os.environ.get(_E2E_PROFILE_ENV, "")
    if not name:
        pytest.skip(
            f"E2E tests require {_E2E_PROFILE_ENV} env var — "
            "set it to a logged-in Chromium profile name and re-run with -m e2e"
        )
    from gflow_cli.auth import profile_dir as _resolve

    candidate = _resolve(name)
    if not candidate.exists():
        pytest.skip(
            f"Profile directory not found: {candidate}. "
            f"Run `gflow auth login --profile {name}` to create it."
        )
    return candidate


def _make_client(strategy: str, profile: Path) -> FlowApiClient:
    return FlowApiClient(profile_dir=profile, transport=strategy)


# ---------------------------------------------------------------------------
# CV1 — T2V: text prompt → pending VideoOperation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
async def test_e2e_t2v_returns_pending_operation(strategy: str) -> None:
    """CV1: generate_video() with a text-only request returns a VideoOperation.

    Asserts:
    - operation_name is non-empty (server accepted the job)
    - media_name is non-empty (asset slot created in the project)
    - model key in the request body reflects t2v mode (checked indirectly via
      no exception being raised — the client validates the mode internally)

    If this test returns HTTP 401 the test will raise AuthExpiredError or
    WireFormatError, confirming the video route is also 401-blocked.
    """
    profile = _profile_dir()
    req = GenerateVideoRequest(
        prompt="slow cinematic zoom over a foggy mountain lake at dawn",
        aspect=Aspect.PORTRAIT,
        tier=Tier.FAST,
    )
    async with _make_client(strategy, profile) as client:
        project = await client.create_project(title=f"e2e-cv1-{strategy}")
        op = await client.generate_video(project_id=project.project_id, req=req)

    assert op.operation_name, "operation_name must be non-empty (server must have accepted job)"
    assert op.media_name, "media_name must be non-empty"
    assert op.project_id == project.project_id


# ---------------------------------------------------------------------------
# CV2 — I2V: upload image → generate video with start_asset_uuid
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
async def test_e2e_i2v_upload_then_generate(strategy: str) -> None:
    """CV2: upload_image() → generate_video(start_asset_uuid=...) returns a
    pending VideoOperation in I2V mode.

    This is the core verification: does Flow accept an imageInput referencing
    the uploaded asset UUID and return a valid operation?

    Step 1 — upload the committed test PNG into the project.
    Step 2 — submit a GenerateVideoRequest with start_asset_uuid = asset.name.
    Step 3 — assert we receive a pending VideoOperation.

    The test does NOT poll for completion — generation can take 2-3 minutes
    and e2e tests should complete within seconds. Pending status is sufficient
    evidence that the route is functional.
    """
    if not _TEST_IMAGE.exists():
        pytest.skip(f"Test image not found: {_TEST_IMAGE}")

    profile = _profile_dir()
    async with _make_client(strategy, profile) as client:
        project = await client.create_project(title=f"e2e-cv2-{strategy}")

        # Step 1: upload reference image
        asset = await client.upload_image(project.project_id, _TEST_IMAGE)

        assert asset.name, "upload_image() must return a non-empty asset UUID"
        assert asset.project_id == project.project_id
        assert asset.width > 0 and asset.height > 0, (
            f"upload must return valid dimensions, got {asset.width}x{asset.height}"
        )

        # Step 2: I2V generation using the uploaded asset
        req = GenerateVideoRequest(
            prompt="gentle camera pan revealing the scene",
            aspect=Aspect.PORTRAIT,
            tier=Tier.FAST,
            start_asset_uuid=asset.name,  # triggers Mode.I2V
        )
        assert req.mode.value == "i2v", "start_asset_uuid must set mode to I2V"

        op = await client.generate_video(project_id=project.project_id, req=req)

    # Step 3: verify operation is pending
    assert op.operation_name, "operation_name must be non-empty"
    assert op.media_name, "media_name must be non-empty"
    assert op.project_id == project.project_id


# ---------------------------------------------------------------------------
# CV3 — upload-only: verify upload route is independently reachable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
async def test_e2e_upload_image_returns_asset_info(strategy: str) -> None:
    """CV3: upload_image() alone — confirms the upload route is reachable before
    the video generation route is exercised.

    Useful for isolating failures: if CV2 fails but CV3 passes, the problem
    is in the video generation route, not in upload.  If CV3 also fails we
    know upload is 401-blocked too.
    """
    if not _TEST_IMAGE.exists():
        pytest.skip(f"Test image not found: {_TEST_IMAGE}")

    profile = _profile_dir()
    async with _make_client(strategy, profile) as client:
        project = await client.create_project(title=f"e2e-cv3-{strategy}")
        asset = await client.upload_image(project.project_id, _TEST_IMAGE)

    assert asset.name, "asset UUID must be non-empty"
    assert asset.project_id == project.project_id
    assert asset.workflow_id, "workflow_id must be non-empty"
    assert asset.width > 0 and asset.height > 0
