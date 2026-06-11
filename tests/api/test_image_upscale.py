"""Tests for gflow_cli.api.image_upscale — value objects + body builder (issue #171).

Wire facts (docs/IMAGE_UPSCALE_RECON.md):
    POST /v1/flow/upsampleImage
    {mediaId, targetResolution: UPSAMPLE_IMAGE_RESOLUTION_2K|_4K,
     clientContext: {recaptchaContext: {token}}}
"""

from __future__ import annotations

import dataclasses

import pytest

from gflow_cli.api.image_upscale import (
    TargetResolution,
    UpsampleImageRequest,
    build_upsample_image_body,
)

_VALID_MEDIA_ID = "3a56bb5e-92a2-44f4-9992-3c6a9bf0cd14"


# ---------- TargetResolution.from_cli ----------


@pytest.mark.parametrize(
    ("cli", "expected"),
    [
        ("2k", TargetResolution.RES_2K),
        ("4k", TargetResolution.RES_4K),
        ("2K", TargetResolution.RES_2K),
        (" 4K ", TargetResolution.RES_4K),
    ],
)
def test_target_resolution_from_cli(cli: str, expected: TargetResolution) -> None:
    assert TargetResolution.from_cli(cli) is expected


def test_target_resolution_wire_values() -> None:
    assert TargetResolution.RES_2K.value == "UPSAMPLE_IMAGE_RESOLUTION_2K"
    assert TargetResolution.RES_4K.value == "UPSAMPLE_IMAGE_RESOLUTION_4K"


def test_target_resolution_rejects_1k() -> None:
    """1K is the original — there is no upscale call for it. Reject with a hint."""
    with pytest.raises(ValueError, match="1k"):
        TargetResolution.from_cli("1k")


@pytest.mark.parametrize("bad", ["8k", "", "  ", "hd", "2"])
def test_target_resolution_rejects_unknown(bad: str) -> None:
    with pytest.raises(ValueError, match="resolution"):
        TargetResolution.from_cli(bad)


# ---------- UpsampleImageRequest ----------


def test_request_is_frozen() -> None:
    req = UpsampleImageRequest(media_id=_VALID_MEDIA_ID, target_resolution=TargetResolution.RES_2K)
    with pytest.raises(dataclasses.FrozenInstanceError):
        req.media_id = "x"  # type: ignore[misc]


def test_request_recaptcha_token_defaults_unminted() -> None:
    req = UpsampleImageRequest(media_id=_VALID_MEDIA_ID, target_resolution=TargetResolution.RES_4K)
    assert req.recaptcha_token == ""


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "   ",
        "not-a-uuid",
        f" {_VALID_MEDIA_ID} ",  # whitespace-padded
        "3a56bb5e92a244f499923c6a9bf0cd14",  # missing hyphens
        "zzzzzzzz-92a2-44f4-9992-3c6a9bf0cd14",  # non-hex
    ],
)
def test_request_rejects_malformed_media_id(bad_id: str) -> None:
    with pytest.raises(ValueError, match="mediaId|media_id|UUID"):
        UpsampleImageRequest(media_id=bad_id, target_resolution=TargetResolution.RES_2K)


def test_request_accepts_valid_uuid() -> None:
    req = UpsampleImageRequest(media_id=_VALID_MEDIA_ID, target_resolution=TargetResolution.RES_2K)
    assert req.media_id == _VALID_MEDIA_ID


# ---------- build_upsample_image_body ----------


def test_body_shape() -> None:
    req = dataclasses.replace(
        UpsampleImageRequest(media_id=_VALID_MEDIA_ID, target_resolution=TargetResolution.RES_2K),
        recaptcha_token="tok-123",
    )
    body = build_upsample_image_body(req)
    assert body["mediaId"] == _VALID_MEDIA_ID
    assert body["targetResolution"] == "UPSAMPLE_IMAGE_RESOLUTION_2K"
    assert body["clientContext"]["recaptchaContext"]["token"] == "tok-123"


def test_body_carries_4k_resolution() -> None:
    req = dataclasses.replace(
        UpsampleImageRequest(media_id=_VALID_MEDIA_ID, target_resolution=TargetResolution.RES_4K),
        recaptcha_token="tok",
    )
    body = build_upsample_image_body(req)
    assert body["targetResolution"] == "UPSAMPLE_IMAGE_RESOLUTION_4K"
