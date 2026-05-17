from __future__ import annotations

import json

import pytest

from gflow_cli.auth.verification import (
    FlowSessionOutcome,
    FlowSessionStatus,  # noqa: F401 — imported to assert it's part of the public API
    evaluate_session_response,
)

# Representative authenticated /api/auth/session body. Sanitised — no real
# PII. Pins the endpoint contract: if Google changes the response shape, the
# AUTHENTICATED assertions below fail loudly instead of the change going silent.
AUTHENTICATED_BODY = json.dumps(
    {
        "user": {
            "name": "Test User",
            "email": "test.user@example.com",
            "image": "https://lh3.googleusercontent.com/a/fake",
        },
        "expires": "2026-06-16T08:39:21.000Z",
    }
)


class TestEvaluateSessionResponse:
    def test_authenticated_user_with_email(self) -> None:
        status = evaluate_session_response(
            200, AUTHENTICATED_BODY, google_session=True, source="chrome"
        )
        assert status.outcome is FlowSessionOutcome.AUTHENTICATED
        assert status.authenticated is True
        assert status.user_email == "test.user@example.com"
        assert status.detail == "Flow app session verified."

    def test_empty_session_with_google_cookie(self) -> None:
        status = evaluate_session_response(200, "{}", google_session=True, source="chrome")
        assert status.outcome is FlowSessionOutcome.GOOGLE_SESSION_ONLY
        assert status.authenticated is False
        assert status.user_email is None

    def test_empty_session_no_google_cookie(self) -> None:
        status = evaluate_session_response(200, "{}", google_session=False, source="chrome")
        assert status.outcome is FlowSessionOutcome.NO_SESSION

    def test_null_user_does_not_crash(self) -> None:
        status = evaluate_session_response(
            200, '{"user": null}', google_session=False, source="chrome"
        )
        assert status.outcome is FlowSessionOutcome.NO_SESSION

    def test_null_user_with_google_cookie(self) -> None:
        status = evaluate_session_response(
            200, '{"user": null}', google_session=True, source="chrome"
        )
        assert status.outcome is FlowSessionOutcome.GOOGLE_SESSION_ONLY

    @pytest.mark.parametrize(
        "body",
        [
            '{"user": {"name": "x"}}',  # user present, no email key
            '{"user": {"email": ""}}',  # empty-string email
            '{"user": ["not", "a", "dict"]}',  # user is not a dict
            "[]",  # JSON array, not an object
            '{"user":',  # truncated JSON
            "",  # empty body
            "   ",  # whitespace only
            "not json at all",  # garbage
        ],
    )
    def test_unexpected_or_malformed_body_is_verification_error(self, body: str) -> None:
        status = evaluate_session_response(200, body, google_session=True, source="chrome")
        assert status.outcome is FlowSessionOutcome.VERIFICATION_ERROR
        assert status.detail == "Could not verify the Flow session."

    @pytest.mark.parametrize("status_code", [302, 401, 403, 404, 500, 503])
    def test_non_200_is_verification_error(self, status_code: int) -> None:
        # google_session is irrelevant on the error path.
        status = evaluate_session_response(
            status_code, AUTHENTICATED_BODY, google_session=True, source="chrome"
        )
        assert status.outcome is FlowSessionOutcome.VERIFICATION_ERROR

    def test_source_is_passed_through(self) -> None:
        status = evaluate_session_response(200, "{}", google_session=False, source="internal")
        assert status.source == "internal"
