"""Tests for gflow_cli.api.transports._common shared utilities.

RED phase: all tests fail with ModuleNotFoundError until _common.py is created.
"""
from __future__ import annotations

import json

import pytest
from unittest.mock import MagicMock

from gflow_cli.api.transports._common import (
    FLOW_URL,
    PER_CALL_TIMEOUT_S,
    BEARER_DEFAULT_TTL_S,
    REFRESH_SAFETY_MARGIN_S,
    mint_batch_id,
    interpret_response,
)
from gflow_cli.errors import (
    AuthExpiredError,
    ContentPolicyError,
    NetworkError,
    RateLimitError,
    WafRejectionError,
    WireFormatError,
)


# ---------------------------------------------------------------------------
# Helper — build a minimal valid wire-format media item
# ---------------------------------------------------------------------------

def _make_media_item(
    name: str = "asset-uuid-1",
    workflow_id: str = "wf-001",
    seed: int = 42,
    prompt: str = "a test prompt",
    model_name_type: str = "NARWHAL",
    aspect_ratio: str = "IMAGE_ASPECT_RATIO_PORTRAIT",
    fife_url: str = "https://lh3.example.com/img?foo=bar",
    width: int = 512,
    height: int = 512,
) -> dict:  # type: ignore[type-arg]
    return {
        "name": name,
        "workflowId": workflow_id,
        "image": {
            "generatedImage": {
                "seed": seed,
                "prompt": prompt,
                "modelNameType": model_name_type,
                "aspectRatio": aspect_ratio,
                "fifeUrl": fife_url,
            },
            "dimensions": {"width": width, "height": height},
        },
    }


def _resp(status: int, body: object) -> MagicMock:
    """Create a minimal httpx-like response mock."""
    text = body if isinstance(body, str) else json.dumps(body)
    return MagicMock(status_code=status, text=text)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_constants_are_canonical() -> None:
    assert FLOW_URL == "https://labs.google/fx/tools/flow?hl=en"
    assert PER_CALL_TIMEOUT_S == 30
    assert BEARER_DEFAULT_TTL_S == 3600
    assert REFRESH_SAFETY_MARGIN_S == 60


# ---------------------------------------------------------------------------
# mint_batch_id
# ---------------------------------------------------------------------------


def test_mint_batch_id_returns_uuid_string() -> None:
    bid = mint_batch_id()
    assert isinstance(bid, str)
    assert len(bid) == 36  # uuid4 canonical form: 8-4-4-4-12


def test_mint_batch_id_is_unique() -> None:
    assert mint_batch_id() != mint_batch_id()


# ---------------------------------------------------------------------------
# interpret_response — happy path
# ---------------------------------------------------------------------------


def test_interpret_response_200_returns_generated_images() -> None:
    payload = {"media": [_make_media_item()]}
    resp = _resp(200, payload)
    images = interpret_response("test_strategy", resp)
    assert len(images) == 1
    img = images[0]
    assert img.media_name == "asset-uuid-1"
    assert img.fife_url == "https://lh3.example.com/img?foo=bar"
    assert img.seed == 42
    assert img.dimensions == (512, 512)


def test_interpret_response_200_multiple_images() -> None:
    payload = {"media": [_make_media_item(name="a"), _make_media_item(name="b")]}
    resp = _resp(200, payload)
    images = interpret_response("test_strategy", resp)
    assert len(images) == 2
    assert {img.media_name for img in images} == {"a", "b"}


# ---------------------------------------------------------------------------
# interpret_response — error branches
# ---------------------------------------------------------------------------


def test_interpret_response_401_raises_auth_expired() -> None:
    resp = _resp(401, "Unauthorized")
    with pytest.raises(AuthExpiredError) as exc_info:
        interpret_response("test_strategy", resp)
    assert "test_strategy" in str(exc_info.value)


def test_interpret_response_403_raises_waf_rejection() -> None:
    resp = _resp(403, "Forbidden")
    with pytest.raises(WafRejectionError) as exc_info:
        interpret_response("test_strategy", resp)
    assert "test_strategy" in str(exc_info.value)


def test_interpret_response_429_raises_rate_limit() -> None:
    resp = _resp(429, "Too Many Requests")
    with pytest.raises(RateLimitError) as exc_info:
        interpret_response("test_strategy", resp)
    assert "test_strategy" in str(exc_info.value)


def test_interpret_response_500_raises_network_error() -> None:
    resp = _resp(500, "Internal Server Error")
    with pytest.raises(NetworkError) as exc_info:
        interpret_response("test_strategy", resp)
    assert "test_strategy" in str(exc_info.value)


def test_interpret_response_503_raises_network_error() -> None:
    resp = _resp(503, "Service Unavailable")
    with pytest.raises(NetworkError):
        interpret_response("test_strategy", resp)


def test_interpret_response_empty_media_raises_content_policy() -> None:
    resp = _resp(200, {"media": []})
    with pytest.raises(ContentPolicyError) as exc_info:
        interpret_response("test_strategy", resp)
    assert "test_strategy" in str(exc_info.value)


def test_interpret_response_missing_media_key_raises_wire_format() -> None:
    resp = _resp(200, {"not_media": []})
    with pytest.raises(WireFormatError) as exc_info:
        interpret_response("test_strategy", resp)
    assert "test_strategy" in str(exc_info.value)


def test_interpret_response_non_json_body_raises_wire_format() -> None:
    resp = _resp(200, "this is not json")
    # MagicMock auto-sets text; override with raw string
    resp.text = "this is not json"
    with pytest.raises(WireFormatError) as exc_info:
        interpret_response("test_strategy", resp)
    assert "test_strategy" in str(exc_info.value)


def test_interpret_response_unexpected_status_raises_wire_format() -> None:
    resp = _resp(302, "redirect")
    with pytest.raises(WireFormatError) as exc_info:
        interpret_response("test_strategy", resp)
    assert "test_strategy" in str(exc_info.value)


def test_interpret_response_strategy_name_in_403_message() -> None:
    """Strategy name must appear in error message for traceability."""
    resp = _resp(403, "denied")
    with pytest.raises(WafRejectionError) as exc_info:
        interpret_response("bearer_strategy", resp)
    assert "bearer_strategy" in str(exc_info.value)


def test_interpret_response_non_json_body_chained_from_json_decode_error() -> None:
    """WireFormatError for non-JSON must chain the original JSONDecodeError."""
    resp = _resp(200, "bad json {{")
    resp.text = "bad json {{"
    with pytest.raises(WireFormatError) as exc_info:
        interpret_response("s1", resp)
    assert exc_info.value.__cause__ is not None
    import json as _json
    assert isinstance(exc_info.value.__cause__, _json.JSONDecodeError)
