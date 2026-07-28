"""End-to-end unit test suite for self-documenting errors (#380).

Verifies that all domain errors, client provider response parsers, MCP error formatters,
and CLI JSON output builders surface structured remediation hints and provider error details.
"""

from __future__ import annotations

from typing import Any

import pytest

from gflow_cli._cli_helpers import _handle_gflow_error
from gflow_cli.api.client import _raise_for_non_retryable
from gflow_cli.errors import (
    AisandboxAuthError,
    AuthExpiredError,
    ContentPolicyError,
    DataStoreError,
    FrameExtractionError,
    RateLimitError,
    SceneConcatError,
    WireFormatError,
)
from gflow_cli.json_output import error_payload
from gflow_cli.mcp.tools import _format_mcp_error, _gflow_error_dict


class MockResponse:
    def __init__(self, status: int, text: str):
        self.status = status
        self.status_code = status
        self.text = text

    def json(self) -> dict[str, Any]:
        import json

        return json.loads(self.text)  # type: ignore[no-any-return]


def test_all_domain_errors_have_non_empty_remediation_hints() -> None:
    """Verify that every domain exception class carries a non-empty default remediation hint."""
    error_classes = [
        WireFormatError,
        ContentPolicyError,
        RateLimitError,
        DataStoreError,
        SceneConcatError,
        FrameExtractionError,
        AisandboxAuthError,
        AuthExpiredError,
    ]
    for cls in error_classes:
        exc = cls("Test failure detail")
        problem = exc.to_problem_details()
        assert "remediation_hint" in problem
        assert len(problem["remediation_hint"]) > 10, f"{cls.__name__} has empty remediation hint"


def test_client_extracts_provider_json_error_message() -> None:
    """Verify that client parsing extracts error.json.message and populates detail."""
    json_body = (
        '{"error": {"json": {"message": "You have reached the daily limit for Nano Banana Pro."}}}'
    )
    resp = MockResponse(429, json_body)

    with pytest.raises(RateLimitError) as exc_info:
        _raise_for_non_retryable(resp, body_text=json_body, route="test_route")  # type: ignore[arg-type]

    exc = exc_info.value
    assert "daily limit for Nano Banana Pro" in exc.detail
    assert "remediation_hint" in exc.to_problem_details()


def test_mcp_error_formatting_includes_class_detail_and_remediation() -> None:
    """Verify that MCP error responses format [class] detail (Remediation: hint)."""
    exc = ContentPolicyError(
        detail="Image prompt rejected due to multiple persons",
        remediation_hint="Reduce prompt text or describe <= 1 person per scene.",
    )
    mcp_msg = _format_mcp_error(exc)
    assert "[ContentPolicyError]" in mcp_msg
    assert "multiple persons" in mcp_msg
    assert "Remediation: Reduce prompt text" in mcp_msg

    mcp_dict = _gflow_error_dict(exc)
    assert "message" in mcp_dict
    assert "[ContentPolicyError]" in mcp_dict["message"]


def test_json_error_payload_carries_remediation_hint() -> None:
    """Verify that json_output.error_payload contains remediation_hint."""
    exc = RateLimitError("Daily limit reached")
    payload = error_payload(exc)

    assert payload["status"] == "fail"
    assert "error" in payload
    assert payload["error"]["class"] == "RateLimitError"
    assert "remediation_hint" in payload["error"]
    assert len(payload["error"]["remediation_hint"]) > 0


def test_cli_rich_error_handler_renders_remediation(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify that _cli_helpers renders remediation hints to stdout/stderr."""
    exc = WireFormatError(
        detail="Invalid parameter payload",
        remediation_hint="Check request payload parameters or retry with a simpler prompt text.",
    )
    code = _handle_gflow_error(exc, cli_command="test")
    captured = capsys.readouterr()

    assert code == 7
    assert "WireFormatError" in captured.out
    assert "Check request payload parameters" in captured.out
