import json

import pytest

from gflow_cli.api.client import _raise_for_non_retryable
from gflow_cli.errors import (
    AuthExpiredError,
    ContentPolicyError,
    WafRejectionError,
    WireFormatError,
    classify_content_safety,
)


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status


def test_403_maps_to_waf_rejection() -> None:
    with pytest.raises(WafRejectionError):
        _raise_for_non_retryable(_Resp(403), "{}", route="batchGenerateImages")


def test_401_still_maps_to_auth_expired() -> None:
    with pytest.raises(AuthExpiredError):
        _raise_for_non_retryable(_Resp(401), "{}", route="createEntity")


# ---------- 400 content-safety classification (issue #342) ----------


def _flow_400_body(reason: str) -> str:
    """Build a realistic Flow HTTP 400 error body with a content-safety reason."""
    return json.dumps(
        {
            "error": {
                "code": 400,
                "message": "Request contains an invalid argument.",
                "status": "INVALID_ARGUMENT",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": reason,
                    }
                ],
            }
        }
    )


def test_400_unsafe_generation_maps_to_content_policy() -> None:
    body = _flow_400_body("PUBLIC_ERROR_UNSAFE_GENERATION")
    with pytest.raises(ContentPolicyError, match="content-safety"):
        _raise_for_non_retryable(_Resp(400), body, route="batchGenerateImages")


def test_400_unsafe_content_maps_to_content_policy() -> None:
    body = _flow_400_body("PUBLIC_ERROR_UNSAFE_CONTENT")
    with pytest.raises(ContentPolicyError, match="content-safety"):
        _raise_for_non_retryable(_Resp(400), body, route="batchGenerateImages")


def test_400_unsafe_face_maps_to_content_policy() -> None:
    body = _flow_400_body("PUBLIC_ERROR_UNSAFE_FACE")
    with pytest.raises(ContentPolicyError, match="content-safety"):
        _raise_for_non_retryable(_Resp(400), body, route="batchGenerateImages")


def test_400_unsafe_identity_maps_to_content_policy() -> None:
    body = _flow_400_body("PUBLIC_ERROR_UNSAFE_IDENTITY")
    with pytest.raises(ContentPolicyError, match="content-safety"):
        _raise_for_non_retryable(_Resp(400), body, route="batchGenerateImages")


def test_400_content_policy_error_carries_reason_in_remediation() -> None:
    body = _flow_400_body("PUBLIC_ERROR_UNSAFE_GENERATION")
    with pytest.raises(ContentPolicyError) as exc_info:
        _raise_for_non_retryable(_Resp(400), body, route="batchGenerateImages")
    assert "PUBLIC_ERROR_UNSAFE_GENERATION" in exc_info.value.remediation_hint
    assert "face" in exc_info.value.remediation_hint.lower()


def test_400_unknown_reason_still_maps_to_wire_format() -> None:
    """A 400 with an unknown reason (not in CONTENT_SAFETY_REASONS) falls
    through to WireFormatError — the safety net still works."""
    body = _flow_400_body("SOME_OTHER_REASON")
    with pytest.raises(WireFormatError):
        _raise_for_non_retryable(_Resp(400), body, route="batchGenerateImages")


def test_400_non_json_body_still_maps_to_wire_format() -> None:
    with pytest.raises(WireFormatError):
        _raise_for_non_retryable(_Resp(400), "not json at all", route="batchGenerateImages")


def test_400_empty_body_still_maps_to_wire_format() -> None:
    with pytest.raises(WireFormatError):
        _raise_for_non_retryable(_Resp(400), "", route="batchGenerateImages")


def test_400_no_details_field_still_maps_to_wire_format() -> None:
    body = json.dumps({"error": {"code": 400, "status": "INVALID_ARGUMENT"}})
    with pytest.raises(WireFormatError):
        _raise_for_non_retryable(_Resp(400), body, route="batchGenerateImages")


def test_400_empty_details_still_maps_to_wire_format() -> None:
    body = json.dumps({"error": {"code": 400, "status": "INVALID_ARGUMENT", "details": []}})
    with pytest.raises(WireFormatError):
        _raise_for_non_retryable(_Resp(400), body, route="batchGenerateImages")


# ---------- _classify_content_safety unit tests ----------


def test_classify_content_safety_returns_reason_for_valid_body() -> None:
    body = _flow_400_body("PUBLIC_ERROR_UNSAFE_GENERATION")
    assert classify_content_safety(body) == "PUBLIC_ERROR_UNSAFE_GENERATION"


def test_classify_content_safety_returns_none_for_non_content_safety_reason() -> None:
    body = _flow_400_body("SOME_OTHER_REASON")
    assert classify_content_safety(body) is None


def test_classify_content_safety_returns_none_for_non_json() -> None:
    assert classify_content_safety("not json") is None


def test_classify_content_safety_returns_none_for_empty_string() -> None:
    assert classify_content_safety("") is None


def test_classify_content_safety_returns_none_for_array_body() -> None:
    assert classify_content_safety("[]") is None


def test_classify_content_safety_handles_multiple_details() -> None:
    body = json.dumps(
        {
            "error": {
                "code": 400,
                "status": "INVALID_ARGUMENT",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "SOME_OTHER",
                    },
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "PUBLIC_ERROR_UNSAFE_GENERATION",
                    },
                ],
            }
        }
    )
    assert classify_content_safety(body) == "PUBLIC_ERROR_UNSAFE_GENERATION"
