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


class TestPerInstanceRetryability:
    """`GFlowError.retryable` overrides the class answer for one raise site.

    It exists because `FlowAppError` (exit 31) now covers two shapes with different
    retry semantics: Flow's client-side crash page, where a retry genuinely works,
    and its `/about` redirect (#756), where retryability is UNMEASURED — the redirect
    stopped reproducing on `ci-probe` between 2026-09-08 and 2026-09-10
    (docs/superpowers/spikes/2026-09-10-about-redirect-stability.md). One flag for
    both would have made the class answer an assertion nobody checked.
    """

    def test_class_answer_is_unchanged_when_no_override(self) -> None:
        from gflow_cli.errors import FlowAppError, is_retryable

        assert is_retryable(FlowAppError(detail="the React error boundary rendered")) is True

    def test_instance_override_wins(self) -> None:
        from gflow_cli.errors import FlowAppError, is_retryable

        assert is_retryable(FlowAppError(detail="/about", retryable=False)) is False

    def test_override_can_also_opt_a_non_retryable_class_in(self) -> None:
        """Both directions, so the mechanism is not silently one-way."""
        from gflow_cli.errors import UiSelectorDriftError, is_retryable

        assert is_retryable(UiSelectorDriftError(detail="drift")) is False
        assert is_retryable(UiSelectorDriftError(detail="drift", retryable=True)) is True

    def test_the_about_landing_is_not_flagged_retryable(self) -> None:
        """The raise site itself, not just the constructor.

        Pins the non-claim: this shape raised exit 23 before (already non-retryable),
        so routing it to exit 31 must not quietly flip consumers into retrying it.
        """
        from gflow_cli.api.transports._common import raise_for_known_landing
        from gflow_cli.errors import EXIT_CODE_MAP, FlowAppError, is_retryable

        page = type("P", (), {"url": "https://flow.google.com/about"})()
        with pytest.raises(FlowAppError) as exc_info:
            raise_for_known_landing(page, requested="project abc", at="test")

        assert is_retryable(exc_info.value) is False
        assert EXIT_CODE_MAP[FlowAppError] == 31

    def test_a_mock_does_not_read_as_retryable(self) -> None:
        """A MagicMock answers every getattr with a truthy child mock, so a
        truthiness test here would report EVERY mocked error as retryable and no
        assertion in the suite would catch it (memory
        `magicmock-truthy-getattr-silences-guards`). `is_retryable` pins on
        `isinstance(..., bool)`; this proves that is load-bearing."""
        from unittest.mock import MagicMock

        from gflow_cli.errors import UiSelectorDriftError, is_retryable

        mock_exc = MagicMock(spec=UiSelectorDriftError)
        assert not isinstance(mock_exc.retryable, bool), (
            "precondition: a spec'd mock answers `.retryable` with a child mock, "
            "not a bool — which is exactly what would fool a truthiness test"
        )
        assert bool(mock_exc.retryable) is True, "precondition: that child mock IS truthy"
        assert is_retryable(mock_exc) is False
