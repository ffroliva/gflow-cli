"""End-to-end CLI error handling: typed errors -> exit codes + remediation prints + telemetry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import structlog
from click.testing import CliRunner

from gflow_cli.cli import main
from gflow_cli.errors import (
    AuthExpiredError,
    AuthLoginTimeoutError,
    ContentPolicyError,
    GFlowError,
    NetworkError,
    RateLimitError,
    SecurityError,
    WireFormatError,
)


@pytest.fixture(autouse=True)
def _isolate_structlog(monkeypatch: pytest.MonkeyPatch):
    """structlog.configure() is global state. T4a tests repeatedly install
    LogCapture processors -- without this fixture, captured events from prior
    tests would leak into the current test's log_capture list and
    bind_contextvars values would persist. Reset both before AND after each
    test so order doesn't matter.

    Also patches the ``configure_logging`` calls that ``gflow_cli.cli.main``
    invokes at the process boundary (T5) to a no-op so the test's own
    ``LogCapture`` (installed BEFORE ``runner.invoke``) survives the CLI
    bootstrap. Without this, ``configure_logging`` overwrites the test's
    processor chain with the production stack and captured events vanish.
    """
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    monkeypatch.setattr("gflow_cli.cli.configure_logging", lambda *a, **kw: None)
    yield
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()


def _patch_profile_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, module: str) -> None:
    """Patch `_resolve_profile` and `_make_provider_dir` on the given CLI module so
    commands reach the `run_with_handlers` wrapper instead of bailing out with
    exit 2 during profile resolution. ``tmp_path`` is the real per-test temp dir
    so any code that touches the provider dir (e.g. mkdir checks) works on Windows."""
    monkeypatch.setattr(f"{module}._resolve_profile", lambda profile: "test")
    monkeypatch.setattr(f"{module}._make_provider_dir", lambda name: tmp_path)


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
    exc: GFlowError,
    expected_exit_code: int,
    expected_in_output: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Each typed GFlowError surfaces with the right exit code + remediation hint."""
    _patch_profile_resolution(monkeypatch, tmp_path, "gflow_cli.cli_image")
    monkeypatch.setattr("gflow_cli.cli_image._run_t2i", _make_raiser(exc))

    runner = CliRunner()
    result = runner.invoke(main, ["image", "t2i", "test prompt"])
    assert result.exit_code == expected_exit_code, result.output
    assert expected_in_output.lower() in result.output.lower()


# ---- wiring smoke: all 6 wrapped _run_* helpers ----


def _make_image_file(parent: Path, name: str = "in.png") -> Path:
    """Create a real PNG-magic-byte file so Click's existence/path checks pass."""
    p = parent / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    return p


def _make_manifest_file(parent: Path, name: str = "manifest.tsv") -> Path:
    """Create a real TSV manifest with one valid prompt row."""
    p = parent / name
    p.write_text("prompt\toutput\nhello world\tout.mp4\n", encoding="utf-8")
    return p


def test_image_upload_wires_run_with_handlers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_profile_resolution(monkeypatch, tmp_path, "gflow_cli.cli_image")
    img = _make_image_file(tmp_path)
    monkeypatch.setattr(
        "gflow_cli.cli_image._run_upload",
        _make_raiser(AuthExpiredError(detail="401", status=401, route="upload")),
    )
    runner = CliRunner()
    result = runner.invoke(main, ["image", "upload", str(img)])
    assert result.exit_code == 3, result.output


def test_image_i2i_wires_run_with_handlers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_profile_resolution(monkeypatch, tmp_path, "gflow_cli.cli_image")
    ref = _make_image_file(tmp_path, "ref.png")
    monkeypatch.setattr(
        "gflow_cli.cli_image._run_i2i",
        _make_raiser(AuthExpiredError(detail="401", status=401, route="batchGenerateImages")),
    )
    runner = CliRunner()
    result = runner.invoke(main, ["image", "i2i", "make it stormy", "--ref", str(ref)])
    assert result.exit_code == 3, result.output


def test_video_t2v_wires_run_with_handlers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_profile_resolution(monkeypatch, tmp_path, "gflow_cli.cli_video")
    monkeypatch.setattr(
        "gflow_cli.cli_video._run_t2v",
        _make_raiser(AuthExpiredError(detail="401", status=401, route="generateVideo")),
    )
    runner = CliRunner()
    result = runner.invoke(main, ["video", "t2v", "a kite over a beach"])
    assert result.exit_code == 3, result.output


def test_video_i2v_wires_run_with_handlers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_profile_resolution(monkeypatch, tmp_path, "gflow_cli.cli_video")
    img = _make_image_file(tmp_path, "first_frame.png")
    monkeypatch.setattr(
        "gflow_cli.cli_video._run_i2v",
        _make_raiser(AuthExpiredError(detail="401", status=401, route="generateVideo")),
    )
    runner = CliRunner()
    result = runner.invoke(main, ["video", "i2v", str(img), "make it move"])
    assert result.exit_code == 3, result.output


def test_video_batch_wires_run_with_handlers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_profile_resolution(monkeypatch, tmp_path, "gflow_cli.cli_video")
    manifest = _make_manifest_file(tmp_path)
    monkeypatch.setattr(
        "gflow_cli.cli_video._run_batch",
        _make_raiser(AuthExpiredError(detail="401", status=401, route="generateVideo")),
    )
    runner = CliRunner()
    result = runner.invoke(main, ["video", "batch", str(manifest)])
    assert result.exit_code == 3, result.output


# ---- SIGINT / KeyboardInterrupt path ----


def test_cli_keyboard_interrupt_exits_130(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`run_with_handlers` translates KeyboardInterrupt -> exit 130 (standard SIGINT code).
    Without this the user's Ctrl-C would surface as exit 1 (catch-all) and the telemetry
    layer would emit a noisy error_unhandled event for what is normal control flow."""
    _patch_profile_resolution(monkeypatch, tmp_path, "gflow_cli.cli_image")

    async def _interrupt(*args: Any, **kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("gflow_cli.cli_image._run_t2i", _interrupt)

    runner = CliRunner()
    result = runner.invoke(main, ["image", "t2i", "test prompt"])
    assert result.exit_code == 130, result.output


# ---- telemetry events ----


def test_cli_unhandled_exception_exits_1_and_emits_unhandled_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """Non-GFlowError exception -> exit code 1 + error_unhandled event fires."""
    log_capture = install_log_capture.entries

    _patch_profile_resolution(monkeypatch, tmp_path, "gflow_cli.cli_image")
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


def test_cli_gflow_error_emits_error_raised_event_with_correlation_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """A typed GFlowError -> exit 3 + structured error_raised event with Problem Details."""
    log_capture = install_log_capture.entries

    _patch_profile_resolution(monkeypatch, tmp_path, "gflow_cli.cli_image")
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
    # T5: correlation_id flows from a contextvar bound at the cli.main
    # process boundary. merge_contextvars (installed above) folds it into
    # the event dict before LogCapture sees the call.
    assert "correlation_id" in e
    assert e["cli_command"].startswith("image t2i")
    # Cross-contamination guard — error_unhandled MUST NOT fire on a GFlowError.
    assert not [evt for evt in log_capture if evt.get("event") == "error_unhandled"]


def test_cli_wire_format_error_logs_full_discovery_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """WireFormatError surfaces ALL 5 discovery payload fields in the structured event.

    Spec § 3.1 / C5: discovery must carry route_name, http_status, content_type,
    top_level_keys, body_prefix_redacted — the five log-grep-evolution fields that
    let log mining propose new error subclasses."""
    log_capture = install_log_capture.entries

    _patch_profile_resolution(monkeypatch, tmp_path, "gflow_cli.cli_image")
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
    discovery = events[0]["discovery"]
    # All 5 spec-mandated discovery fields present
    for field in (
        "route_name",
        "http_status",
        "content_type",
        "top_level_keys",
        "body_prefix_redacted",
    ):
        assert field in discovery, f"missing discovery field: {field}"
    assert discovery["route_name"] == "batchGenerateImages"
    assert discovery["http_status"] == 200
    assert discovery["content_type"] == "application/json"
    assert discovery["top_level_keys"] == ["error", "status"]
    assert discovery["body_prefix_redacted"] == '{"error": "..."}'


def test_content_policy_logs_upstream_status_200_extension(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """ContentPolicyError -> upstream_status=200 extension + RFC 9457 omits status."""
    log_capture = install_log_capture.entries

    _patch_profile_resolution(monkeypatch, tmp_path, "gflow_cli.cli_image")
    exc = ContentPolicyError(detail="empty media[]")
    monkeypatch.setattr("gflow_cli.cli_image._run_t2i", _make_raiser(exc))

    runner = CliRunner()
    runner.invoke(main, ["image", "t2i", "test"])
    events = [e for e in log_capture if e.get("event") == "error_raised"]
    assert events
    assert events[0].get("upstream_status") == 200
    # Problem Details `status` field MUST be absent (RFC 9457 contract: no 2xx status on errors).
    assert "status" not in events[0]["problem"]


def _make_raiser(exc: BaseException):
    """Return an async function that raises *exc* when awaited."""

    async def _raise(*args: Any, **kwargs: Any) -> None:
        raise exc

    return _raise


# ---------------------------------------------------------------------------
# auth login — error handling (exit codes for timeout + security violation)
# ---------------------------------------------------------------------------


class TestAuthLoginErrors:
    """Verify `gflow auth login` exits with the right code for each GFlowError subclass.

    The CLI must NEVER print "Session saved" on error — agents rely on the
    exit code to distinguish success (0) from failure (non-zero).
    """

    def _invoke_auth_login(self, error: GFlowError) -> Any:
        """Invoke `gflow auth login` with asyncio.run mocked to raise *error*."""
        from unittest.mock import patch

        runner = CliRunner()
        with patch("gflow_cli.cli.asyncio.run", side_effect=error):
            return runner.invoke(
                main, ["auth", "login", "--browser", "internal", "--profile", "test"]
            )

    def test_timeout_exits_12(self) -> None:
        """AuthLoginTimeoutError → exit code 12 (distinct from all other errors)."""
        result = self._invoke_auth_login(AuthLoginTimeoutError("timed out after 0s"))
        assert result.exit_code == 12, result.output
        # Error class title must appear
        assert "Login timed out" in result.output
        # Must NOT claim success
        assert "Session saved" not in result.output

    def test_timeout_prints_remediation_hint(self) -> None:
        """Remediation hint is printed to help agents and users know what to do."""
        err = AuthLoginTimeoutError(
            "timed out",
            remediation_hint="Run `gflow auth login` again.",
        )
        result = self._invoke_auth_login(err)
        assert result.exit_code == 12
        assert "Run `gflow auth login` again." in result.output

    def test_security_error_exits_13(self) -> None:
        """SecurityError → exit code 13; never claims success."""
        result = self._invoke_auth_login(SecurityError("outside of GFLOW_CLI_HOME"))
        assert result.exit_code == 13, result.output
        assert "Security violation" in result.output
        assert "Session saved" not in result.output

    def test_configuration_error_exits_11(self) -> None:
        """ConfigurationError keeps its exit code 11 under the new broad catch."""
        from gflow_cli.errors import ConfigurationError

        result = self._invoke_auth_login(ConfigurationError("unknown browser mode"))
        assert result.exit_code == 11, result.output
        assert "Session saved" not in result.output
