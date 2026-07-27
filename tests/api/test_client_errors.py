"""Unit tests for provider error message extraction and redaction in FlowApiClient."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gflow_cli.api.client import _extract_provider_error_message, _raise_for_non_retryable
from gflow_cli.errors import (
    AuthExpiredError,
    ContentPolicyError,
    RateLimitError,
    WireFormatError,
)
from gflow_cli.redaction import redact_sensitive_text


def test_extract_provider_error_message_shapes() -> None:
    """Test extracting error message across various provider JSON shapes."""
    # error.json.message
    body1 = '{"error": {"json": {"message": "Invalid auth token"}}}'
    assert _extract_provider_error_message(body1) == "Invalid auth token"

    # error.message
    body2 = '{"error": {"message": "Quota exceeded"}}'
    assert _extract_provider_error_message(body2) == "Quota exceeded"

    # error string
    body3 = '{"error": "Bad request format"}'
    assert _extract_provider_error_message(body3) == "Bad request format"

    # message string
    body4 = '{"message": "Resource not found"}'
    assert _extract_provider_error_message(body4) == "Resource not found"

    # detail string
    body5 = '{"detail": "Rate limit exceeded"}'
    assert _extract_provider_error_message(body5) == "Rate limit exceeded"

    # Non-JSON or empty
    assert _extract_provider_error_message("plain text error") is None
    assert _extract_provider_error_message("") is None
    assert _extract_provider_error_message("{}") is None


def test_redact_sensitive_text() -> None:
    """Verify secrets and signed URLs are redacted from free-text strings."""
    assert redact_sensitive_text("Bearer ya29.a0AfH6SMA") == "<redacted:secret>"
    assert (
        redact_sensitive_text("Error with SAPISIDHASH abc123def") == "Error with <redacted:secret>"
    )
    assert (
        redact_sensitive_text("File at https://storage.googleapis.com/f.png?signature=123")
        == "File at <redacted:url>"
    )
    assert redact_sensitive_text("Normal error message") == "Normal error message"


def test_raise_for_non_retryable_auth_expired() -> None:
    """Verify 401 extracts provider message and sets AuthExpiredError.detail."""
    resp = MagicMock(status=401)
    body = '{"error": {"message": "Session expired or token invalid: Bearer ya29.secret"}}'

    with pytest.raises(AuthExpiredError) as exc_info:
        _raise_for_non_retryable(resp, body, route="createProject")

    err = exc_info.value
    assert err.status == 401
    assert "Bearer" not in err.detail
    assert "<redacted:secret>" in err.detail
    assert "Session expired or token invalid:" in err.detail


def test_raise_for_non_retryable_rate_limit() -> None:
    """Verify 429 extracts provider message and sets RateLimitError.detail."""
    resp = MagicMock(status=429)
    body = '{"error": {"json": {"message": "Daily generation quota reached"}}}'

    with pytest.raises(RateLimitError) as exc_info:
        _raise_for_non_retryable(resp, body, route="batchGenerateImages")

    err = exc_info.value
    assert err.status == 429
    assert err.detail == "Daily generation quota reached"


def test_raise_for_non_retryable_content_policy() -> None:
    """Verify 400 with safety reason extracts provider message and sets detail."""
    resp = MagicMock(status=400)
    body = (
        '{"error": {"details": [{"reason": "PUBLIC_ERROR_UNSAFE_GENERATION"}]}, '
        '"message": "Prompt violates policy"}'
    )

    with pytest.raises(ContentPolicyError) as exc_info:
        _raise_for_non_retryable(resp, body, route="generateImage")

    err = exc_info.value
    assert "Prompt violates policy" in err.detail


def test_raise_for_non_retryable_wire_format() -> None:
    """Verify 4xx fallthrough extracts provider message and sets WireFormatError.detail."""
    resp = MagicMock(status=422)
    body = '{"error": {"message": "Invalid field value"}}'

    with pytest.raises(WireFormatError) as exc_info:
        _raise_for_non_retryable(resp, body, route="createProject")

    err = exc_info.value
    assert err.status == 422
    assert err.detail == "Invalid field value"
