"""Unit tests for IncidentRecorder orchestration (Task 6 — S01, S04,
S10-S15, S19, S23, S25, S36). Fake pages only — no Playwright."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.config import Settings
from gflow_cli.diagnostics import BundleDir, IncidentRecorder
from gflow_cli.errors import (
    AuthExpiredError,
    ConfigurationError,
    ContentPolicyError,
    FlowAgentUiError,
    FlowAppError,
    NetworkError,
    ProfileLockedError,
    TransportTimeoutError,
    UiSelectorDriftError,
    WafRejectionError,
    WireFormatError,
)

CANARY = "SECRETCANARY-9f8e7d"


class FakePage:
    """Minimal async stand-in recording every method the recorder touches."""

    def __init__(
        self,
        evaluate_result: dict[str, Any] | None = None,
        *,
        evaluate_exc: Exception | None = None,
        fullpage_exc: Exception | None = None,
        screenshot_exc: Exception | None = None,
    ) -> None:
        self.calls: list[str] = []
        self._evaluate_result = evaluate_result or {"ligatures": ["crop_landscape"]}
        self._evaluate_exc = evaluate_exc
        self._fullpage_exc = fullpage_exc
        self._screenshot_exc = screenshot_exc

    async def evaluate(self, script: str, *args: object) -> dict[str, Any]:
        self.calls.append("evaluate")
        if self._evaluate_exc is not None:
            raise self._evaluate_exc
        return self._evaluate_result

    async def screenshot(self, *, path: str, full_page: bool = False) -> None:
        self.calls.append(f"screenshot(full_page={full_page})")
        if self._screenshot_exc is not None:
            raise self._screenshot_exc
        if full_page and self._fullpage_exc is not None:
            raise self._fullpage_exc
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\nfake")


def _recorder(tmp_path: Path, **kwargs: object) -> IncidentRecorder:
    return IncidentRecorder(Settings(home=tmp_path, **kwargs))  # type: ignore[arg-type]


def _bundles(tmp_path: Path) -> list[Path]:
    root = tmp_path / "incidents"
    if not root.is_dir():
        return []
    return [b for day in root.iterdir() if day.is_dir() for b in day.iterdir()]


class TestShouldCapture:
    def test_trigger_classification_matches_design(self, tmp_path: Path) -> None:
        rec = _recorder(tmp_path)
        for exc in (
            FlowAppError("x"),
            FlowAgentUiError("x"),
            UiSelectorDriftError("x"),
            TransportTimeoutError("x"),
            WireFormatError("x"),
            WafRejectionError("x"),
            NetworkError("x"),
            ProfileLockedError("x"),
            RuntimeError("unexpected"),
        ):
            assert rec.should_capture(exc), type(exc).__name__
        for exc in (
            ContentPolicyError("expected"),
            AuthExpiredError("expected"),
            ConfigurationError("usage"),
        ):
            assert not rec.should_capture(exc), type(exc).__name__

    def test_disabled_setting_captures_nothing(self, tmp_path: Path) -> None:
        rec = _recorder(tmp_path, incident_capture=False)
        assert not rec.should_capture(FlowAppError("x"))

    def test_cancellation_and_exits_never_capture(self, tmp_path: Path) -> None:
        """S20: cancellation is not an incident — capture must not fire on it."""
        rec = _recorder(tmp_path)
        for exc in (asyncio.CancelledError(), KeyboardInterrupt(), SystemExit(1)):
            assert not rec.should_capture(exc), type(exc).__name__


@pytest.mark.asyncio
class TestBugReportTemplate:
    """Finalized bundles carry a pre-filled Markdown bug report (issue #476)."""

    async def test_staged_bundle_contains_prefilled_report(self, tmp_path: Path) -> None:
        rec = _recorder(tmp_path)
        ref = await rec.capture_failure(FlowAppError("crash"), page=FakePage(), phase="mode_switch")
        assert ref is not None and ref.path is not None
        # Written at STAGE time so the frozen IncidentRef tuple — the single
        # source of truth for the Rich and --json surfaces — includes it.
        assert "report.md" in ref.artifacts
        report = (ref.path / "report.md").read_text(encoding="utf-8")
        assert "FlowAppError" in report
        assert "31" in report  # mapped exit code
        assert "ui.json" in report  # artifact pointer
        assert "COPY THIS FILE" in report  # retention can prune the bundle
        await rec.finalize_all(close_ok=True)
        manifest = json.loads((ref.path / "manifest.json").read_text(encoding="utf-8"))
        assert str(manifest["cli_version"]) in report
        assert str(manifest["os_family"]) in report
        assert manifest["artifacts"]["report.md"] == "report"
        assert manifest["artifact_status"]["report.md"] == "complete"

    async def test_report_never_contains_exception_text(self, tmp_path: Path) -> None:
        """Same allowlist discipline as the manifest (S01): the raw exception
        message must never reach the report."""
        rec = _recorder(tmp_path)
        ref = await rec.capture_failure(FlowAppError(CANARY), page=FakePage(), phase="p")
        assert ref is not None and ref.path is not None
        await rec.finalize_all(close_ok=True)
        assert CANARY not in (ref.path / "report.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
class TestCaptureFailure:
    async def test_ui_failure_stages_dom_and_screenshot(self, tmp_path: Path) -> None:
        rec = _recorder(tmp_path)
        page = FakePage()
        ref = await rec.capture_failure(FlowAppError("crash"), page=page, phase="mode_switch")
        assert ref is not None
        assert ref.path is not None
        assert (ref.path / "ui.json").exists()
        assert (ref.path / "sensitive" / "screenshot.png").exists()
        assert (ref.path / "network.json").exists()
        assert (ref.path / "browser.json").exists()
        assert not (ref.path / "manifest.json").exists()  # staged, not finalized
        await rec.finalize_all(close_ok=True)
        manifest = json.loads((ref.path / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["error"]["class"] == "FlowAppError"
        assert manifest["error"]["exit_code"] == 31

    async def test_screenshot_lives_under_sensitive_and_manifest_marks_it(
        self, tmp_path: Path
    ) -> None:
        rec = _recorder(tmp_path)
        ref = await rec.capture_failure(FlowAppError("x"), page=FakePage(), phase="p")
        assert ref is not None and ref.path is not None
        await rec.finalize_all(close_ok=True)
        manifest = json.loads((ref.path / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["artifacts"]["sensitive/screenshot.png"] == "sensitive"
        assert "review" in manifest["notice"].lower()

    async def test_no_screenshot_for_network_class_failures(self, tmp_path: Path) -> None:
        rec = _recorder(tmp_path)
        page = FakePage()
        ref = await rec.capture_failure(WafRejectionError("403"), page=page, phase="generate")
        assert ref is not None and ref.path is not None
        assert not (ref.path / "sensitive").exists()
        assert not any(c.startswith("screenshot") for c in page.calls)

    async def test_structural_result_rejects_non_allowlisted_fields(self, tmp_path: Path) -> None:
        """S12: hostile DOM payload fields never reach ui.json."""
        hostile = {
            "ligatures": ["crop_portrait"],
            "bodyText": f"prompt {CANARY}",
            "ariaLabel": CANARY,
            "innerHTML": f"<div>{CANARY}</div>",
            "url": f"https://labs.google/x?tok={CANARY}",
            "title": f"Flow doc {CANARY}",
        }
        rec = _recorder(tmp_path)
        ref = await rec.capture_failure(
            FlowAppError("x"), page=FakePage(hostile), phase="mode_switch"
        )
        assert ref is not None and ref.path is not None
        blob = (ref.path / "ui.json").read_text(encoding="utf-8")
        assert CANARY not in blob
        for key in ("bodyText", "ariaLabel", "innerHTML"):
            assert key not in blob

    async def test_overlay_records_bounded_geometry_without_text(self, tmp_path: Path) -> None:
        """S11: overlay evidence keeps geometry/role/ligatures, drops text."""
        result = {
            "ligatures": [],
            "overlays": [
                {
                    "tag": "div",
                    "role": "dialog",
                    "ariaModal": True,
                    "visible": True,
                    "rect": {"x": 0, "y": 0, "width": 1280, "height": 200},
                    "zIndex": 9999,
                    "pointerEvents": "auto",
                    "ligatures": ["close"],
                    "innerText": f"one-time banner {CANARY}",
                }
            ],
        }
        rec = _recorder(tmp_path)
        ref = await rec.capture_failure(FlowAppError("x"), page=FakePage(result), phase="p")
        assert ref is not None and ref.path is not None
        ui = json.loads((ref.path / "ui.json").read_text(encoding="utf-8"))
        overlay = ui["overlays"][0]
        assert overlay["rect"]["width"] == 1280
        assert overlay["zIndex"] == 9999
        assert overlay["ligatures"] == ["close"]
        assert CANARY not in json.dumps(ui)

    async def test_fullpage_screenshot_failure_falls_back_to_viewport(self, tmp_path: Path) -> None:
        rec = _recorder(tmp_path)
        page = FakePage(fullpage_exc=TimeoutError("hang"))
        ref = await rec.capture_failure(FlowAppError("x"), page=page, phase="p")
        assert ref is not None and ref.path is not None
        assert (ref.path / "sensitive" / "screenshot.png").exists()
        assert "screenshot(full_page=True)" in page.calls
        assert "screenshot(full_page=False)" in page.calls
        assert ref.capture_status == "partial"

    async def test_concurrent_capture_same_fingerprint_yields_one_bundle(
        self, tmp_path: Path
    ) -> None:
        """S19/S14: one staged dir; losers only increment suppression."""
        rec = _recorder(tmp_path)
        refs = await asyncio.gather(
            *(
                rec.capture_failure(FlowAppError("same"), page=FakePage(), phase="submit")
                for _ in range(10)
            )
        )
        assert len(_bundles(tmp_path)) == 1
        assert len({r.id for r in refs if r is not None}) == 1
        await rec.finalize_all(close_ok=True)
        bundle = _bundles(tmp_path)[0]
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["suppressed_count"] == 9

    async def test_bundle_cap_three_distinct_fingerprints(self, tmp_path: Path) -> None:
        rec = _recorder(tmp_path)
        excs = (
            FlowAppError("a"),
            FlowAgentUiError("b"),
            UiSelectorDriftError("c"),
            WafRejectionError("d"),
            NetworkError("e"),
        )
        for exc in excs:
            await rec.capture_failure(exc, page=FakePage(), phase="p")
        assert len(_bundles(tmp_path)) == 3

    async def test_capture_is_observation_only(self, tmp_path: Path) -> None:
        """S15: the recorder may read (evaluate/screenshot) — nothing else."""
        rec = _recorder(tmp_path)
        page = FakePage()
        await rec.capture_failure(FlowAppError("x"), page=page, phase="p")
        assert set(page.calls) <= {
            "evaluate",
            "screenshot(full_page=True)",
            "screenshot(full_page=False)",
        }

    async def test_capture_io_failure_preserves_original_exception(self, tmp_path: Path) -> None:
        """S23: a broken page never raises out of capture."""
        rec = _recorder(tmp_path)
        page = FakePage(evaluate_exc=OSError("boom"), screenshot_exc=OSError("boom"))
        ref = await rec.capture_failure(FlowAppError("x"), page=page, phase="p")
        assert ref is not None  # journals still staged
        assert ref.capture_status in {"partial", "failed"}

    async def test_readonly_root_reports_failed_capture_original_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S36: bundle-creation failure → no recursion, no raise, no bundle."""
        rec = _recorder(tmp_path)
        monkeypatch.setattr(
            BundleDir,
            "create_exclusive",
            classmethod(lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))),
        )
        ref = await rec.capture_failure(FlowAppError("x"), page=FakePage(), phase="p")
        assert ref is None
        assert _bundles(tmp_path) == []

    async def test_metadata_only_capture_without_page(self, tmp_path: Path) -> None:
        """S34/S07 wiring surface: contention/partial-setup gets a bundle with
        no page-derived artifacts."""
        rec = _recorder(tmp_path)
        ref = await rec.capture_metadata_only(ProfileLockedError("held"), phase="profile_lease")
        assert ref is not None and ref.path is not None
        assert not (ref.path / "ui.json").exists()
        assert not (ref.path / "sensitive").exists()
        await rec.finalize_all(close_ok=True)
        manifest = json.loads((ref.path / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["error"]["class"] == "ProfileLockedError"
        assert manifest["error"]["exit_code"] == 11

    async def test_expected_failures_create_no_bundle(self, tmp_path: Path) -> None:
        rec = _recorder(tmp_path)
        for exc in (ContentPolicyError("x"), AuthExpiredError("x")):
            assert await rec.capture_failure(exc, page=FakePage(), phase="p") is None
        assert _bundles(tmp_path) == []

    async def test_wedged_page_still_stages_journals_and_registers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Review fix: a page that hangs past every timeout must not cost the
        whole bundle — journals stage first, page artifacts share the budget
        via a deadline, and the fingerprint still registers for suppression."""
        import gflow_cli.diagnostics as diag

        monkeypatch.setattr(diag, "_CAPTURE_BUDGET_S", 0.3)
        monkeypatch.setattr(diag, "_DOM_TIMEOUT_S", 0.1)
        monkeypatch.setattr(diag, "_SCREENSHOT_TIMEOUT_S", 0.1)

        class _WedgedPage:
            async def evaluate(self, script: str, /) -> dict[str, object]:
                await asyncio.sleep(5)
                return {}

            async def screenshot(self, *, path: str, full_page: bool = False) -> None:
                await asyncio.sleep(5)

        rec = _recorder(tmp_path)
        rec.record_response(
            url="https://labs.google/fx/tools/flow",
            method="GET",
            resource_type="document",
            status=200,
            request_key="r1",
            monotonic_ts=1.0,
        )
        ref = await rec.capture_failure(FlowAppError("wedged"), page=_WedgedPage(), phase="p")
        assert ref is not None and ref.path is not None
        assert (ref.path / "network.json").exists()  # journals survived the wedge
        assert ref.capture_status == "partial"
        # The fingerprint registered: a repeat suppresses instead of re-staging.
        again = await rec.capture_failure(FlowAppError("wedged"), page=_WedgedPage(), phase="p")
        assert again is not None and again.id == ref.id
        assert len(_bundles(tmp_path)) == 1

    async def test_bundle_json_contains_no_canary(self, tmp_path: Path) -> None:
        """S01 end-to-end: canaries in URLs, console text, and error bodies
        never reach any written file."""
        rec = _recorder(tmp_path)
        rec.record_request(request_key="r1", monotonic_ts=1.0)
        rec.record_response(
            url=f"https://aisandbox-pa.googleapis.com/v1/flow/uploadImage?sig={CANARY}",
            method="POST",
            resource_type="xhr",
            status=500,
            request_key="r1",
            monotonic_ts=1.5,
        )
        rec.record_console(level="error", text=f"token={CANARY}", url=None, line=1, column=1)
        rec.record_page_error(error_class="TypeError", message=f"boom {CANARY}")
        ref = await rec.capture_failure(FlowAppError("x"), page=FakePage(), phase="p")
        assert ref is not None and ref.path is not None
        await rec.finalize_all(close_ok=True)
        for artifact in ref.path.rglob("*.json"):
            assert CANARY not in artifact.read_text(encoding="utf-8"), artifact


class TestHarState:
    def test_har_disabled_when_unset(self, tmp_path: Path) -> None:
        rec = _recorder(tmp_path)
        rec.note_har_pre_launch(None)
        assert rec.resolve_har_state(close_ok=True) == "disabled"

    def test_har_complete_only_when_session_changed_file(self, tmp_path: Path) -> None:
        """S32: pre-existing unchanged file is NOT proof of capture."""
        har = tmp_path / "s.har"
        har.write_text("{}")
        rec = _recorder(tmp_path)
        rec.note_har_pre_launch(har)
        assert rec.resolve_har_state(close_ok=True) == "possibly_incomplete"  # unchanged
        har.write_text('{"log": {"entries": [1]}}')
        assert rec.resolve_har_state(close_ok=True) == "complete"
        assert rec.resolve_har_state(close_ok=False) == "possibly_incomplete"

    def test_har_created_by_session_is_complete(self, tmp_path: Path) -> None:
        har = tmp_path / "new.har"
        rec = _recorder(tmp_path)
        rec.note_har_pre_launch(har)
        assert rec.resolve_har_state(close_ok=True) == "possibly_incomplete"  # never written
        har.write_text("{}")
        assert rec.resolve_har_state(close_ok=True) == "complete"


# ---------------------------------------------------------------------------
# #639: every arm of the mode-switch cascade must keep capturing incidents
# ---------------------------------------------------------------------------


def test_every_mode_switch_error_arm_is_a_capture_trigger() -> None:
    """`_mode_switch_error` returns one of four classes, and the operator needs a
    bundle for ALL of them — the reporter's own #639 evidence came from one.

    This is the invariant, not a list of four names: adding a fifth arm without
    adding it here silently turns capture OFF for that failure, and nothing else
    in the suite would notice.
    """
    from gflow_cli.diagnostics import _capture_triggers, _screenshot_triggers
    from gflow_cli.errors import (
        FlowAgentUiError,
        FlowAppError,
        FlowHostMigratedError,
        UiSelectorDriftError,
    )

    arms = (FlowAppError, FlowHostMigratedError, FlowAgentUiError, UiSelectorDriftError)
    captured = _capture_triggers()
    shot = _screenshot_triggers()
    assert [a for a in arms if a not in captured] == []
    assert [a for a in arms if a not in shot] == []
