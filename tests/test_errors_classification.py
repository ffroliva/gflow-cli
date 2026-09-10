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
    """`FlowAppError.retryable` overrides the class answer for one raise site.

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

    def test_only_flow_app_error_carries_the_override(self) -> None:
        """Scoped to the one class that needs it (council D14).

        A base-class field would sit on every error in the project to serve one
        raise site. `is_retryable` reads it by `getattr`, so narrowing costs nothing
        — and this test is what makes the narrowing visible if someone widens it back
        without a second producer to justify it.
        """
        from gflow_cli.errors import UiSelectorDriftError, is_retryable

        assert is_retryable(UiSelectorDriftError(detail="drift")) is False
        with pytest.raises(TypeError):
            UiSelectorDriftError(detail="drift", retryable=True)  # type: ignore[call-arg]

    def test_the_signin_landing_raises_auth_expired(self) -> None:
        """The `"signin"` arm, which only the e2e reached before — and `addopts`
        excludes that, so the offline suite never executed this branch (council D4).

        Also pins the redaction: the NextAuth family includes the OAuth callback,
        whose query carries `code=` and `state=`. This message is what users paste
        into issues (council D3).
        """
        from gflow_cli.api.transports._common import raise_if_known_landing
        from gflow_cli.errors import AuthExpiredError

        url = "https://labs.google/fx/api/auth/callback/google?state=s3cr3t&code=4/0Aabc"
        page = type("P", (), {"url": url})()
        with pytest.raises(AuthExpiredError) as exc_info:
            raise_if_known_landing(page, requested="the Flow gallery", at="test")

        detail = str(exc_info.value)
        assert "https://labs.google/fx/api/auth/callback/google" in detail
        assert "code=" not in detail and "state=" not in detail and "s3cr3t" not in detail
        assert "sign-in page" not in detail, "the family includes callback and /session"

    def test_a_midrun_chooser_hop_raises_the_chooser_error_not_drift(self) -> None:
        """Measured live, not imagined (2026-09-10, `denon82`): a session can land on
        `accounts.google.com` AFTER bootstrap, where `client._handle_account_chooser`
        no longer runs — and the labs gallery sweep then reported a missing
        "+ New project" CTA on Google's sign-in page, with the OAuth `state` and
        `code_challenge` interpolated into the message.
        """
        from gflow_cli.api.transports._common import raise_if_known_landing
        from gflow_cli.errors import EXIT_CODE_MAP, FlowAccountChooserError

        url = (
            "https://accounts.google.com/v3/signin/accountchooser"
            "?client_id=365941595420-x.apps.googleusercontent.com&state=PKOA6qjxDh"
            "&code_challenge=rNzAdlPk4Ed"
        )
        page = type("P", (), {"url": url})()
        with pytest.raises(FlowAccountChooserError) as exc_info:
            raise_if_known_landing(page, requested="the Flow gallery", at="test")

        detail = str(exc_info.value)
        assert "accounts.google.com/v3/signin/accountchooser" in detail
        assert "state=" not in detail and "code_challenge" not in detail
        assert "New project" not in detail
        assert EXIT_CODE_MAP[FlowAccountChooserError] == 38

    def test_the_about_landing_is_not_flagged_retryable(self) -> None:
        """The raise site itself, not just the constructor.

        Pins the non-claim: this shape raised exit 23 before (already non-retryable),
        so routing it to exit 31 must not quietly flip consumers into retrying it.
        """
        from gflow_cli.api.transports._common import raise_if_known_landing
        from gflow_cli.errors import EXIT_CODE_MAP, FlowAppError, is_retryable

        page = type("P", (), {"url": "https://flow.google.com/about"})()
        with pytest.raises(FlowAppError) as exc_info:
            raise_if_known_landing(page, requested="project abc", at="test")

        assert is_retryable(exc_info.value) is False
        assert EXIT_CODE_MAP[FlowAppError] == 31

    def test_a_truthy_non_bool_override_does_not_flip_the_class_answer(self) -> None:
        """`is_retryable` pins the override with `isinstance(..., bool)`, not truthiness.

        A `MagicMock` answers every `getattr` with a truthy child mock. Under a
        truthiness test that child would read as "retryable: yes" for any object
        carrying it, and no assertion in the suite would notice (memory
        `magicmock-truthy-getattr-silences-guards`).

        Deliberately a BARE mock, not `spec=FlowAppError`: a spec'd mock satisfies
        `isinstance(exc, RETRYABLE_ERRORS)`, so the class answer is `True` anyway and
        the guard becomes unobservable through it. Bare, the class answer is `False`,
        so the truthy child is the only thing that could flip it — which makes this a
        real test of the guard rather than a test that agrees with itself by accident.
        """
        from unittest.mock import MagicMock

        from gflow_cli.errors import is_retryable

        mock_exc = MagicMock()
        assert not isinstance(mock_exc.retryable, bool), "precondition: not a bool"
        assert bool(mock_exc.retryable) is True, "precondition: but it IS truthy"
        assert is_retryable(mock_exc) is False
