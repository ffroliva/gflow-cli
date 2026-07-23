"""Unit tests for the manifest allowlist and fixed-field incident events
(Task 5 — S01/S25/S41/S42)."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
import structlog

from gflow_cli.config import Settings
from gflow_cli.diagnostics import (
    build_manifest,
    emit_capture_completed,
    emit_capture_failed,
    emit_capture_started,
    emit_capture_suppressed,
    emit_owner_evidence_read,
    emit_retention_pruned,
    resolve_correlation_id,
)

_EMITTERS = (
    emit_capture_started,
    emit_capture_completed,
    emit_capture_failed,
    emit_capture_suppressed,
    emit_retention_pruned,
    emit_owner_evidence_read,
)


@pytest.fixture(autouse=True)
def _reset_structlog():
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    yield
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()


def _install_log_capture() -> structlog.testing.LogCapture:
    cap = structlog.testing.LogCapture()
    structlog.configure(processors=[structlog.contextvars.merge_contextvars, cap])
    return cap


class TestEventConstructors:
    def test_event_constructors_accept_fixed_fields_only(self) -> None:
        """S41: no **kwargs escape hatch — callers cannot attach raw URLs,
        paths, owner metadata, exception text, or browser objects."""
        for fn in _EMITTERS:
            params = inspect.signature(fn).parameters.values()
            assert not any(p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL) for p in params), fn.__name__

    def test_events_emit_stable_names_and_fixed_keys(self) -> None:
        cap = _install_log_capture()
        emit_capture_started("corr-fp")
        emit_capture_completed("corr-fp", status="complete", artifact_kinds=["ui"], duration_ms=42)
        emit_capture_failed("corr-fp", exc_class="OSError", artifact_kind="screenshot")
        emit_capture_suppressed("corr-fp", count=3)
        emit_retention_pruned(complete_count=2, pending_count=1, bytes_freed=1024)
        emit_owner_evidence_read(valid=True)
        names = [e["event"] for e in cap.entries]
        assert names == [
            "incident.capture_started",
            "incident.capture_completed",
            "incident.capture_failed",
            "incident.capture_suppressed",
            "incident.retention_pruned",
            "profile_lease.owner_evidence_read",
        ]

    def test_capture_failed_event_carries_class_only(self) -> None:
        """S25: only the exception CLASS crosses into the event — the signature
        accepts no message/text field at all."""
        cap = _install_log_capture()
        exc = RuntimeError("token=SECRETCANARYXYZ")
        emit_capture_failed("corr-fp", exc_class=type(exc).__name__, artifact_kind="dom")
        entry = cap.entries[0]
        assert "SECRETCANARYXYZ" not in repr(entry)
        assert set(entry) == {"event", "log_level", "incident_id", "exc_class", "artifact_kind"}


class TestResolveCorrelationId:
    def test_bound_contextvar_is_used(self) -> None:
        """S42: the id embeds the command's correlation id when bound."""
        structlog.contextvars.bind_contextvars(correlation_id="abc-123")
        assert resolve_correlation_id() == "abc-123"

    def test_missing_context_generates_an_id(self) -> None:
        got = resolve_correlation_id()
        assert got
        assert len(got) == 12
        # Caller binds once; a second resolve without context is a NEW id.
        assert resolve_correlation_id() != got


class TestBuildManifest:
    def _manifest(self, settings: Settings) -> dict[str, object]:
        return build_manifest(
            incident_id="corr-fp",
            settings=settings,
            created_utc="2026-07-22T21:30:00Z",
            finalized_utc="2026-07-22T21:30:04Z",
            cli_version="0.42.0",
            exc_class="FlowAppError",
            problem_type="https://gflow-cli.dev/errors/flow-app",
            exit_code=31,
            retryable=True,
            route="/fx/tools/flow/project/{id}",
            phase="mode_switch",
            command="video t2v",
            transport="ui_automation",
            artifacts={"ui.json": "automatic", "sensitive/screenshot.png": "sensitive"},
            artifact_status={"ui.json": "complete", "sensitive/screenshot.png": "complete"},
            har_state="disabled",
            suppressed_count=2,
        )

    def test_manifest_allowlist_excludes_settings_secrets(self) -> None:
        """S01/§5.1: the manifest is built from an explicit allowlist — a
        Settings object full of canaries must not leak one byte of them."""
        settings = Settings(
            home=Path("/home/CANARYUSER/gflow"),
            gemini_api_key="CANARY-GEMINI-KEY",
            daemon_token="CANARY-DAEMON-TOKEN",
            storage_uri="s3://CANARY-BUCKET/media",
            har_path=Path("C:/CANARY-HAR-DIR/session.har"),
        )
        blob = json.dumps(self._manifest(settings))
        for canary in ("CANARY", "har_path", "gemini", "daemon_token", "storage_uri"):
            assert canary not in blob

    def test_manifest_carries_error_and_artifact_facts(self) -> None:
        m = self._manifest(Settings())
        assert m["schema"] == "gflow-incident-v1"
        assert m["incident_id"] == "corr-fp"
        error = m["error"]
        assert isinstance(error, dict)
        assert error["class"] == "FlowAppError"
        assert error["exit_code"] == 31
        assert error["retryable"] is True
        assert m["har_state"] == "disabled"
        assert m["suppressed_count"] == 2
        artifacts = m["artifacts"]
        assert isinstance(artifacts, dict)
        assert artifacts["sensitive/screenshot.png"] == "sensitive"
        notice = m["notice"]
        assert isinstance(notice, str)
        assert "Never uploaded" in notice
