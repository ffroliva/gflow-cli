"""Issue #528 — HTTP 400 on a generation route is a content-policy rejection.

The `ui_automation` transports raised :class:`WireFormatError` for any non-2xx
status they did not explicitly branch on, so Google's content-policy 400s
arrived carrying "the request was rejected as malformed... retry with a simpler
prompt text". That remediation is actively wrong: shortening the prompt never
helps, and it hides the two levers that do (reference-shape and person
descriptors).

Same defect shape as #379 (429 mishandled as WireFormatError), one status over.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from gflow_cli.api.transports._common import generation_error
from gflow_cli.api.transports.ui_automation import _images_from_responses
from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin
from gflow_cli.errors import (
    ContentPolicyError,
    RateLimitError,
    WireFormatError,
    classify_content_safety,
)

IMAGE_ROUTE = "https://aisandbox-pa.googleapis.com/v1/projects/p1/flowMedia:batchGenerateImages"
VIDEO_ROUTE = (
    "https://aisandbox-pa.googleapis.com/v1/projects/p1/flowMedia:batchAsyncGenerateVideoText"
)

UNSAFE_BODY = {
    "error": {
        "code": 400,
        "message": "Request contains an invalid argument.",
        "status": "INVALID_ARGUMENT",
        "details": [
            {"@type": "type.googleapis.com/x.ErrorInfo", "reason": "PUBLIC_ERROR_UNSAFE_GENERATION"}
        ],
    }
}

# What #528's incident bundles actually captured: a bare 400 with no reason we
# recognise. The fix must still classify it as policy, not wire format.
BARE_400_BODY = {
    "error": {
        "code": 400,
        "message": "Request contains an invalid argument.",
        "status": "INVALID_ARGUMENT",
    }
}


# ---------------------------------------------------------------------------
# shared classifier (was triplicated: client.py, diagnostics.py, transports)
# ---------------------------------------------------------------------------


def test_classify_content_safety_accepts_a_parsed_dict() -> None:
    """The ui_automation listener stores an already-parsed body, not text."""
    assert classify_content_safety(UNSAFE_BODY) == "PUBLIC_ERROR_UNSAFE_GENERATION"


def test_classify_content_safety_accepts_raw_text() -> None:
    """client.py's callers hold the undecoded response text."""
    import json

    assert classify_content_safety(json.dumps(UNSAFE_BODY)) == "PUBLIC_ERROR_UNSAFE_GENERATION"


@pytest.mark.parametrize("body", ["not json", "", {}, None, {"error": {"details": []}}])
def test_classify_content_safety_returns_none_when_absent(body: object) -> None:
    assert classify_content_safety(body) is None


# ---------------------------------------------------------------------------
# image i2i — the path the issue was filed against
# ---------------------------------------------------------------------------


def test_images_from_responses_carries_the_error_body_out() -> None:
    """The 400's body is captured by the listener and must not be dropped.

    Before #528 the `status != 200` branch kept only status+route, so the
    caller could not tell a policy rejection from a malformed request.
    """
    responses = [{"status": 400, "url": IMAGE_ROUTE, "body": UNSAFE_BODY}]

    images, status, route, body = _images_from_responses(responses)

    assert images == []
    assert status == 400
    assert route == IMAGE_ROUTE
    assert body == UNSAFE_BODY


def test_image_400_with_a_safety_reason_raises_content_policy() -> None:
    with pytest.raises(ContentPolicyError) as exc_info:
        raise generation_error(status=400, route=IMAGE_ROUTE, body=UNSAFE_BODY)

    err = exc_info.value
    assert "PUBLIC_ERROR_UNSAFE_GENERATION" in str(err)
    assert err.status == 400


def test_image_400_without_a_reason_is_still_a_policy_rejection() -> None:
    """#528's own evidence: every bundle showed a bare 400, no reason field.

    On the ui_automation path Flow's own web app composes the request body, so
    a 400 there cannot be OUR malformation — classifying it as WireFormatError
    is wrong regardless of whether a reason rode along.
    """
    with pytest.raises(ContentPolicyError):
        raise generation_error(status=400, route=IMAGE_ROUTE, body=BARE_400_BODY)


def test_policy_remediation_names_the_levers_that_actually_work() -> None:
    """The whole point of #528: recover from the error text alone (#380)."""
    with pytest.raises(ContentPolicyError) as exc_info:
        raise generation_error(status=400, route=IMAGE_ROUTE, body=BARE_400_BODY)

    hint = exc_info.value.remediation_hint.lower()
    # (a) the reference-shape lever
    assert "one" in hint and "face" in hint
    assert "--reference-entity" in hint or "reference-entity" in hint
    # (b) the person-descriptor lever
    assert "descriptor" in hint or "age" in hint
    # and the anti-guidance that cost three hours
    assert "shorten" in hint


def test_non_400_4xx_is_still_a_wire_format_error() -> None:
    """Don't over-swing: a 404/422 on the route really is unexpected shape."""
    with pytest.raises(WireFormatError):
        raise generation_error(status=404, route=IMAGE_ROUTE, body={})


def test_image_400_body_is_logged_for_discovery() -> None:
    """We still don't know Flow's real 400 shape — log it like the 403 branch.

    Uses structlog's own capture rather than capsys: the rendered stream depends
    on whatever structlog configuration an earlier test left behind, so a stdout
    assertion passes alone and fails in a full run.
    """
    responses = [{"status": 400, "url": IMAGE_ROUTE, "body": BARE_400_BODY}]

    with capture_logs() as logs:
        _images_from_responses(responses)

    assert [e for e in logs if e["event"] == "ui_automation.batch_400_body"]


# ---------------------------------------------------------------------------
# video — identical defect at ui_automation_video.py:3353, plus no 429 branch
# ---------------------------------------------------------------------------


def test_video_400_raises_content_policy_not_wire_format() -> None:
    resp = {"status": 400, "url": VIDEO_ROUTE, "body": UNSAFE_BODY}

    with pytest.raises(ContentPolicyError):
        VideoGenerationMixin._parse_generate_response(resp)


def test_video_429_raises_rate_limit() -> None:
    """#379 gave the image path a 429 branch; video never got one."""
    resp = {
        "status": 429,
        "url": VIDEO_ROUTE,
        "body": {"error": {"message": "Resource exhausted"}},
        "headers": {"retry-after": "30"},
    }

    with pytest.raises(RateLimitError) as exc_info:
        VideoGenerationMixin._parse_generate_response(resp)

    assert exc_info.value.retry_after == 30.0


def test_video_404_is_still_a_wire_format_error() -> None:
    resp = {"status": 404, "url": VIDEO_ROUTE, "body": {}}

    with pytest.raises(WireFormatError):
        VideoGenerationMixin._parse_generate_response(resp)
