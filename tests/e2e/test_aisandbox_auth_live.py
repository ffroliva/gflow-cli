"""E2E smoke for the L0 SAPISIDHASH fix (Issue #15).

A REST ``uploadImage`` previously returned HTTP 401 to ``page.request``; with
the ``Authorization: SAPISIDHASH`` header attached by ``_post_json`` it must
now succeed. **Credit-free** — uploading an image asset spends no generation
credit. This is the empirical confirmation the redacted HARs could not give.

Opt-in: ``-m e2e`` (or ``-m e2e_auth``) + a verified profile via the
``e2e_profile_dir`` fixture (``GFLOW_CLI_E2E_PROFILE``). ``asyncio_mode=auto``
is set in pyproject.toml, so no ``@pytest.mark.asyncio`` is needed.

If this still 401s: the SAPISIDHASH origin or the cookie host filter is wrong.
Re-run with ``GFLOW_CLI_LOG_REQUEST_HEADERS=1`` and diff the (redacted-safe)
header set against a browser DevTools "Copy as cURL" of a working uploadImage.
Do NOT proceed to L1 until this is green.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from gflow_cli.api.client import FlowApiClient

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]

# A real, valid 1x1 transparent PNG (passes upload_image's magic-byte check and
# is well-formed enough for Flow's server to accept).
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4"
    "2mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


async def test_rest_upload_image_authenticates_after_sapisidhash(
    e2e_profile_dir: Path,
    tmp_path: Path,
) -> None:
    """Issue #15: REST uploadImage previously 401'd; with SAPISIDHASH it must 200."""
    img = tmp_path / "smoke.png"
    img.write_bytes(_PNG_1X1)
    async with FlowApiClient(
        profile_dir=e2e_profile_dir,
        transport="evaluate_fetch",
    ) as client:
        project = await client.create_project(title="L0 SAPISIDHASH smoke")
        asset = await client.upload_image(project.project_id, img)
        # A non-empty asset id => uploadImage authenticated (no 401 / AisandboxAuthError).
        assert asset.name, "uploadImage returned an asset id => SAPISIDHASH accepted"
