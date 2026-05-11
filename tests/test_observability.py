"""structlog bootstrap + emit_*_event tests (T5)."""

from __future__ import annotations

import hashlib
import json
from io import StringIO
from unittest.mock import patch

import pytest
import structlog

from gflow_cli.config import LogFormat
from gflow_cli.errors import AuthExpiredError, ContentPolicyError, WireFormatError
from gflow_cli.observability import (
    configure_logging,
    emit_error_event,
    emit_unhandled_event,
)


@pytest.fixture(autouse=True)
def _reset_structlog():
    """Reset structlog state between tests to avoid bleed-over from
    `cache_logger_on_first_use=True` in `configure_logging`."""
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    yield
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()


def test_auto_detects_tty_renders_text(monkeypatch):
    """When stdout.isatty() == True, AUTO selects text format."""
    fake_tty = StringIO()
    fake_tty.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr("sys.stdout", fake_tty)
    configure_logging(LogFormat.AUTO)
    log = structlog.get_logger("test")
    log.info("hello", extra="value")
    output = fake_tty.getvalue()
    # Text format renders 'hello' but is NOT valid JSON.
    assert "hello" in output
    with pytest.raises(json.JSONDecodeError):
        json.loads(output.strip().splitlines()[0])


def test_auto_detects_pipe_renders_json(monkeypatch):
    fake_pipe = StringIO()
    fake_pipe.isatty = lambda: False  # type: ignore[method-assign]
    monkeypatch.setattr("sys.stdout", fake_pipe)
    configure_logging(LogFormat.AUTO)
    log = structlog.get_logger("test")
    log.info("hello", extra="value")
    output = fake_pipe.getvalue().strip().splitlines()[0]
    parsed = json.loads(output)
    assert parsed["event"] == "hello"
    assert parsed["extra"] == "value"


def test_show_locals_is_false_in_exception_renderer():
    """show_locals=False — secrets in local frames must NOT leak into output."""
    fake_pipe = StringIO()
    fake_pipe.isatty = lambda: False  # type: ignore[method-assign]
    with patch("sys.stdout", fake_pipe):
        configure_logging(LogFormat.JSON)
        log = structlog.get_logger("test")
        try:
            secret_token = "DO_NOT_LEAK_xyz123"  # noqa: F841
            raise ValueError("boom")
        except ValueError:
            log.exception("oops")
        output = fake_pipe.getvalue()
    assert "DO_NOT_LEAK_xyz123" not in output


def _install_log_capture() -> structlog.testing.LogCapture:
    """Install a fresh ``LogCapture`` processor (also merges contextvars).

    structlog 25.x's ``LogCapture()`` takes no constructor args and exposes
    captured events via ``.entries``. The plan's verbatim code (``LogCapture(list)``)
    targets a pre-25 API; we wrap the modern call to keep the tests stable.
    """
    cap = structlog.testing.LogCapture()
    # merge_contextvars must run BEFORE LogCapture so contextvar-bound fields
    # (correlation_id, cli_version) land in the captured event dict.
    structlog.configure(
        processors=[structlog.contextvars.merge_contextvars, cap],
    )
    return cap


def test_emit_error_event_shape():
    cap = _install_log_capture()
    log = structlog.get_logger("test")
    structlog.contextvars.bind_contextvars(correlation_id="abc-123")
    exc = AuthExpiredError(
        detail="401",
        status=401,
        route="createProject",
        instance="gflow:error:abc-123",
    )
    emit_error_event(log, exc, cli_command="image t2i")
    assert cap.entries
    e = cap.entries[0]
    assert e["event"] == "error_raised"
    assert e["error_class"] == "AuthExpiredError"
    assert e["problem"]["type"] == "https://gflow-cli.dev/errors/auth-expired"
    assert e["cli_command"] == "image t2i"


def test_emit_error_event_content_policy_adds_upstream_status():
    cap = _install_log_capture()
    log = structlog.get_logger("test")
    exc = ContentPolicyError(detail="empty media[]")
    emit_error_event(log, exc, cli_command="image t2i")
    e = cap.entries[0]
    # ContentPolicyError omits `status` from Problem Details (RFC 9457 forbids
    # 2xx on the status field). The literal upstream status (200) surfaces as
    # an event extension.
    assert e.get("upstream_status") == 200
    assert "status" not in e["problem"]


def test_emit_error_event_wire_format_includes_discovery():
    cap = _install_log_capture()
    log = structlog.get_logger("test")
    exc = WireFormatError(
        detail="unknown shape",
        discovery={
            "route_name": "batchGenerateImages",
            "http_status": 200,
            "content_type": "application/json",
            "top_level_keys": ["error"],
            "body_prefix_redacted": "{...}",
        },
    )
    emit_error_event(log, exc, cli_command="image t2i")
    e = cap.entries[0]
    assert e["discovery"]["top_level_keys"] == ["error"]


def test_emit_unhandled_event_hashes_message_and_stack():
    cap = _install_log_capture()
    log = structlog.get_logger("test")
    try:
        raise ValueError("secret-value")
    except ValueError as e:
        emit_unhandled_event(log, e, cli_command="image t2i")
    ev = cap.entries[0]
    assert ev["event"] == "error_unhandled"
    assert ev["exception_class"] == "ValueError"
    assert ev["message_hash"] == hashlib.sha256(b"secret-value").hexdigest()
    assert len(ev["stack_hash"]) == 64
    # Privacy: raw message text MUST NOT appear in the event payload.
    assert "secret-value" not in json.dumps(ev)


def test_correlation_id_bound_at_boundary_appears_in_events():
    cap = _install_log_capture()
    structlog.contextvars.bind_contextvars(correlation_id="zzz-111", cli_version="0.4.0a1")
    log = structlog.get_logger("test")
    log.info("any_event")
    assert cap.entries[0]["correlation_id"] == "zzz-111"
    assert cap.entries[0]["cli_version"] == "0.4.0a1"
