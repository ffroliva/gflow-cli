"""Tests for FlowApiClient.upsample_image — image upscale transport (issue #171).

`_post_json` and `_mint_recaptcha_token` are monkey-patched so no Playwright
context / real reCAPTCHA is needed. Covers the must-cover scenarios from
/gflow:scenario: happy path, oversized/missing/undecodable/non-image response
guards, the 4K-403 -> UpscaleUnavailableError disambiguation (no auto-retry),
the 2K-403 -> WafRejectionError passthrough, and the base64-never-logged mandate.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs

from gflow_cli.api.client import MAX_UPSAMPLE_B64_LEN, FlowApiClient
from gflow_cli.api.image_upscale import TargetResolution
from gflow_cli.errors import UpscaleUnavailableError, WafRejectionError, WireFormatError

if TYPE_CHECKING:
    from pathlib import Path

_MEDIA_ID = "3a56bb5e-92a2-44f4-9992-3c6a9bf0cd14"
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_PNG_B64 = base64.b64encode(_PNG).decode()


def _client(tmp_path: Path, *, post_return=None, post_side_effect=None) -> FlowApiClient:
    c = FlowApiClient(profile_dir=tmp_path / "prof")
    c._mint_recaptcha_token = AsyncMock(return_value="tok-abc")  # type: ignore[method-assign]
    c._post_json = AsyncMock(  # type: ignore[method-assign]
        return_value=post_return, side_effect=post_side_effect
    )
    return c


@pytest.mark.asyncio
async def test_upsample_happy_2k_writes_png(tmp_path: Path) -> None:
    out = tmp_path / "out" / f"{_MEDIA_ID}_2k.png"
    c = _client(tmp_path, post_return={"encodedImage": _PNG_B64})

    target = await c.upsample_image(
        media_id=_MEDIA_ID, target_resolution=TargetResolution.RES_2K, out_path=out
    )

    assert target.read_bytes() == _PNG
    c._post_json.assert_awaited_once()  # type: ignore[attr-defined]
    # The minted token rides the body's recaptchaContext.
    body = c._post_json.call_args.args[1]  # type: ignore[attr-defined]
    assert body["mediaId"] == _MEDIA_ID
    assert body["targetResolution"] == "UPSAMPLE_IMAGE_RESOLUTION_2K"
    assert body["clientContext"]["recaptchaContext"]["token"] == "tok-abc"


@pytest.mark.asyncio
async def test_upsample_oversized_rejected_before_decode(tmp_path: Path) -> None:
    out = tmp_path / "big.png"
    oversized = "A" * (MAX_UPSAMPLE_B64_LEN + 4)
    c = _client(tmp_path, post_return={"encodedImage": oversized})

    with pytest.raises(WireFormatError, match="size cap"):
        await c.upsample_image(
            media_id=_MEDIA_ID, target_resolution=TargetResolution.RES_2K, out_path=out
        )
    assert not out.exists()  # rejected before any write


@pytest.mark.asyncio
async def test_upsample_missing_encoded_image(tmp_path: Path) -> None:
    c = _client(tmp_path, post_return={"somethingElse": 1})

    with pytest.raises(WireFormatError, match="missing encodedImage"):
        await c.upsample_image(
            media_id=_MEDIA_ID,
            target_resolution=TargetResolution.RES_2K,
            out_path=tmp_path / "x.png",
        )


@pytest.mark.asyncio
async def test_upsample_undecodable_base64(tmp_path: Path) -> None:
    c = _client(tmp_path, post_return={"encodedImage": "A"})  # invalid base64 length

    with pytest.raises(WireFormatError, match="undecodable"):
        await c.upsample_image(
            media_id=_MEDIA_ID,
            target_resolution=TargetResolution.RES_2K,
            out_path=tmp_path / "x.png",
        )


@pytest.mark.asyncio
async def test_upsample_non_image_bytes_rejected(tmp_path: Path) -> None:
    garbage = base64.b64encode(b"not an image at all").decode()
    out = tmp_path / "x.png"
    c = _client(tmp_path, post_return={"encodedImage": garbage})

    with pytest.raises(WireFormatError, match="not a valid PNG/JPEG"):
        await c.upsample_image(
            media_id=_MEDIA_ID, target_resolution=TargetResolution.RES_2K, out_path=out
        )
    assert not out.exists()


@pytest.mark.asyncio
async def test_upsample_4k_403_maps_to_unavailable(tmp_path: Path) -> None:
    """4K + HTTP 403 -> UpscaleUnavailableError (exit 22), NOT WafRejectionError."""
    c = _client(tmp_path, post_side_effect=WafRejectionError(detail="HTTP 403", status=403))

    with pytest.raises(UpscaleUnavailableError, match="Ultra"):
        await c.upsample_image(
            media_id=_MEDIA_ID,
            target_resolution=TargetResolution.RES_4K,
            out_path=tmp_path / "x.png",
        )
    # Exactly one POST — the tier 403 is never auto-retried.
    c._post_json.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_upsample_2k_403_stays_waf(tmp_path: Path) -> None:
    """A 403 on a 2K request is a genuine WAF rejection — propagated as-is."""
    c = _client(tmp_path, post_side_effect=WafRejectionError(detail="HTTP 403", status=403))

    with pytest.raises(WafRejectionError):
        await c.upsample_image(
            media_id=_MEDIA_ID,
            target_resolution=TargetResolution.RES_2K,
            out_path=tmp_path / "x.png",
        )


@pytest.mark.asyncio
async def test_upsample_encoded_image_never_logged(tmp_path: Path) -> None:
    """The multi-MB base64 must never appear in any structlog event (mitigation)."""
    out = tmp_path / "x.png"
    c = _client(tmp_path, post_return={"encodedImage": _PNG_B64})

    with capture_logs() as logs:
        await c.upsample_image(
            media_id=_MEDIA_ID, target_resolution=TargetResolution.RES_2K, out_path=out
        )

    for entry in logs:
        for value in entry.values():
            assert _PNG_B64 not in str(value), f"base64 leaked into log: {entry}"
    # The completion event reports size as an int, not the payload.
    completed = [e for e in logs if e.get("event") == "image.upscale_completed"]
    assert completed and completed[0]["bytes"] == len(_PNG)
