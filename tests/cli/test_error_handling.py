"""End-to-end CLI error handling: typed errors -> exit codes + remediation prints + telemetry."""

from __future__ import annotations

import pytest
import structlog
from click.testing import CliRunner

from gflow_cli.cli import main
from gflow_cli.errors import (
    AuthExpiredError,
    ContentPolicyError,
    NetworkError,
    RateLimitError,
    WireFormatError,
)


@pytest.fixture(autouse=True)
def _isolate_structlog():
    """structlog.configure() is global state. T4a tests repeatedly install
    LogCapture processors -- without this fixture, captured events from prior
    tests would leak into the current test's log_capture list and
    bind_contextvars values would persist. Reset both before AND after each
    test so order doesn't matter."""
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    yield
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()


@pytest.mark.parametrize(
    "exc, expected_exit_code, expected_in_output",
    [
        (
            AuthExpiredError(detail="401", status=401, route="createProject"),
            3,
            "Run `gflow auth login",
        ),
        (
            RateLimitError(detail="429", status=429, retry_after=42),
            4,
            "Wait a few minutes",
        ),
        (ContentPolicyError(detail="empty media[]"), 5, "content policy"),
        (NetworkError(detail="503 after retries", status=503), 6, "Check connectivity"),
        (WireFormatError(detail="unknown shape"), 7, "File a bug"),
    ],
)
def test_cli_error_to_exit_code_and_remediation(
    exc, expected_exit_code, expected_in_output, monkeypatch
):
    """Each typed GFlowError surfaces with the right exit code + remediation hint."""
    # Patch BOTH _resolve_profile and _make_provider_dir so the command body
    # doesn't bail with exit 2 before reaching the run_with_handlers wrapper.
    monkeypatch.setattr("gflow_cli.cli_image._resolve_profile", lambda profile: "test")
    monkeypatch.setattr(
        "gflow_cli.cli_image._make_provider_dir",
        lambda name: __import__("pathlib").Path("/tmp/fake"),
    )
    monkeypatch.setattr("gflow_cli.cli_image._run_t2i", _make_raiser(exc))

    runner = CliRunner()
    result = runner.invoke(main, ["image", "t2i", "test prompt"])
    assert result.exit_code == expected_exit_code, result.output
    assert expected_in_output.lower() in result.output.lower()


def test_cli_unhandled_exception_exits_1_and_emits_unhandled_event(monkeypatch):
    """Non-GFlowError exception -> exit code 1 + error_unhandled event fires."""
    cap = structlog.testing.LogCapture()
    structlog.configure(processors=[cap])
    log_capture = cap.entries

    monkeypatch.setattr("gflow_cli.cli_image._resolve_profile", lambda profile: "test")
    monkeypatch.setattr(
        "gflow_cli.cli_image._make_provider_dir",
        lambda name: __import__("pathlib").Path("/tmp/fake"),
    )
    monkeypatch.setattr(
        "gflow_cli.cli_image._run_t2i",
        _make_raiser(ValueError("bad input")),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["image", "t2i", "test prompt"])
    assert result.exit_code == 1
    events = [e for e in log_capture if e.get("event") == "error_unhandled"]
    assert events, "error_unhandled event MUST fire"
    e = events[0]
    assert e["exception_class"] == "ValueError"
    assert "message_hash" in e and len(e["message_hash"]) == 64  # SHA-256 hex
    assert "stack_hash" in e and len(e["stack_hash"]) == 64
    # Privacy: full message MUST NOT appear in event payload
    assert "bad input" not in str(e)


def test_cli_gflow_error_emits_error_raised_event_with_correlation_id(monkeypatch):
    """A typed GFlowError -> exit 3 + structured error_raised event with Problem Details."""
    cap = structlog.testing.LogCapture()
    structlog.configure(processors=[cap])
    log_capture = cap.entries

    monkeypatch.setattr("gflow_cli.cli_image._resolve_profile", lambda profile: "test")
    monkeypatch.setattr(
        "gflow_cli.cli_image._make_provider_dir",
        lambda name: __import__("pathlib").Path("/tmp/fake"),
    )
    exc = AuthExpiredError(detail="401", status=401, route="createProject")
    monkeypatch.setattr("gflow_cli.cli_image._run_t2i", _make_raiser(exc))

    runner = CliRunner()
    result = runner.invoke(main, ["image", "t2i", "test prompt"])
    assert result.exit_code == 3
    events = [e for e in log_capture if e.get("event") == "error_raised"]
    assert events
    e = events[0]
    assert e["error_class"] == "AuthExpiredError"
    assert e["problem"]["type"] == "https://gflow-cli.dev/errors/auth-expired"
    assert e["problem"]["status"] == 401
    # In the T4a fallback path (observability.py not yet shipped) correlation_id
    # is provided by the in-line _handle_gflow_error helper.
    assert "correlation_id" in e
    assert e["cli_command"].startswith("image t2i")


def test_cli_wire_format_error_logs_discovery_fields(monkeypatch):
    """WireFormatError surfaces its discovery payload in the structured event."""
    cap = structlog.testing.LogCapture()
    structlog.configure(processors=[cap])
    log_capture = cap.entries

    monkeypatch.setattr("gflow_cli.cli_image._resolve_profile", lambda profile: "test")
    monkeypatch.setattr(
        "gflow_cli.cli_image._make_provider_dir",
        lambda name: __import__("pathlib").Path("/tmp/fake"),
    )
    exc = WireFormatError(
        detail="unknown shape",
        status=200,
        route="batchGenerateImages",
        discovery={
            "route_name": "batchGenerateImages",
            "http_status": 200,
            "content_type": "application/json",
            "top_level_keys": ["error", "status"],
            "body_prefix_redacted": '{"error": "..."}',
        },
    )
    monkeypatch.setattr("gflow_cli.cli_image._run_t2i", _make_raiser(exc))

    runner = CliRunner()
    runner.invoke(main, ["image", "t2i", "test"])
    events = [e for e in log_capture if e.get("event") == "error_raised"]
    assert events
    # Discovery fields land in the structured event extension (NOT in Problem Details type/title).
    assert "discovery" in events[0]
    assert events[0]["discovery"]["top_level_keys"] == ["error", "status"]


def test_content_policy_logs_upstream_status_200_extension(monkeypatch):
    """ContentPolicyError -> upstream_status=200 extension + RFC 9457 omits status."""
    cap = structlog.testing.LogCapture()
    structlog.configure(processors=[cap])
    log_capture = cap.entries

    monkeypatch.setattr("gflow_cli.cli_image._resolve_profile", lambda profile: "test")
    monkeypatch.setattr(
        "gflow_cli.cli_image._make_provider_dir",
        lambda name: __import__("pathlib").Path("/tmp/fake"),
    )
    exc = ContentPolicyError(detail="empty media[]")
    monkeypatch.setattr("gflow_cli.cli_image._run_t2i", _make_raiser(exc))

    runner = CliRunner()
    runner.invoke(main, ["image", "t2i", "test"])
    events = [e for e in log_capture if e.get("event") == "error_raised"]
    assert events
    assert events[0].get("upstream_status") == 200
    # Problem Details `status` field MUST be absent (RFC 9457 contract: no 2xx status on errors).
    assert "status" not in events[0]["problem"]


def _make_raiser(exc):
    """Return an async function that raises *exc* when awaited."""

    async def _raise(*args, **kwargs):
        raise exc

    return _raise
