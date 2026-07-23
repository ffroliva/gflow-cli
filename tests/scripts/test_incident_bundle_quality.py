"""Offline unit coverage for the incident-bundle diagnostic-quality scorer
(scripts/dev/incident_bundle_quality.py). Fast regression net for the rubric
itself — the live benchmark (tests/e2e/test_incident_quality_e2e.py) proves it
against real Flow bundles."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

# scripts/ is outside pyright's include and is not a package — load by path.
_MOD_PATH = Path(__file__).resolve().parents[2] / "scripts" / "dev" / "incident_bundle_quality.py"
_spec = importlib.util.spec_from_file_location("incident_bundle_quality", _MOD_PATH)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules["incident_bundle_quality"] = _mod
_spec.loader.exec_module(_mod)

score_bundle = _mod.score_bundle


def _write(bundle: Path, name: str, obj: object) -> None:
    path = bundle / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _ui_failure_bundle(root: Path, *, extra: dict[str, object] | None = None) -> Path:
    """A well-formed FlowAppError (UI-state) bundle — the grade-A shape."""
    b = root / "ui-bundle"
    b.mkdir()
    manifest = {
        "schema": "gflow-incident-v1",
        "incident_id": "corr-abc123",
        "cli_version": "0.43.0",
        "python_version": "3.13.7",
        "os_family": "Windows",
        "command": "video t2v",
        "transport": "ui_automation",
        "error": {
            "class": "FlowAppError",
            "problem_type": "https://gflow-cli.dev/errors/flow-app",
            "exit_code": 31,
            "retryable": True,
            "route": "/fx/tools/flow",
            "phase": "video_generation",
        },
        "artifact_status": {
            "network.json": "complete",
            "browser.json": "complete",
            "ui.json": "complete",
        },
        "har_state": "disabled",
    }
    manifest.update(extra or {})
    _write(b, "manifest.json", manifest)
    _write(
        b,
        "ui.json",
        {
            "ligatures": ["add_2", "delete", "edit"],
            "ligature_count": 25,
            "tag_counts": {"div": 81, "button": 36},
            "url": {"host_category": "flow_app", "route": "/fx/tools/flow"},
            "overlays": [],
        },
    )
    _write(
        b,
        "network.json",
        {
            "records": [
                {
                    "host_category": "flow_app",
                    "route": "/fx/api/trpc/x",
                    "status_or_failure": "200",
                },
                {"host_category": "aisandbox", "route": "/v1/x", "status_or_failure": "500"},
            ]
        },
    )
    _write(b, "browser.json", {"console": [], "page_errors": []})
    return b


def _contention_bundle(root: Path) -> Path:
    """A ProfileLockedError metadata-only bundle — page evidence is N/A."""
    b = root / "lock-bundle"
    b.mkdir()
    _write(
        b,
        "manifest.json",
        {
            "schema": "gflow-incident-v1",
            "incident_id": "corr-lock99",
            "cli_version": "0.43.0",
            "python_version": "3.13.7",
            "os_family": "Windows",
            "command": "image t2i",
            "transport": None,
            "error": {
                "class": "ProfileLockedError",
                "exit_code": 11,
                "retryable": False,
                "route": "",
                "phase": "profile_lease",
            },
            "artifact_status": {},
            "har_state": "disabled",
        },
    )
    return b


class TestGradeA:
    def test_well_formed_ui_bundle_scores_a_grade(self, tmp_path: Path) -> None:
        report = score_bundle(_ui_failure_bundle(tmp_path))
        assert report.grade == "A"
        assert report.score >= 0.9
        assert report.q1_what_failed is True
        assert report.q2_ui_state is True
        assert report.q3_recent_failures is True
        assert report.q4_har_honest is True
        assert report.q5_contention_owner is None  # N/A for a UI failure
        assert report.fidelity == 1.0
        assert report.completeness == 1.0
        assert report.privacy_clean


class TestContention:
    def test_metadata_only_bundle_scores_well_without_page_evidence(self, tmp_path: Path) -> None:
        report = score_bundle(_contention_bundle(tmp_path))
        # Page-derived questions are N/A and must NOT drag the grade down.
        assert report.q2_ui_state is None
        assert report.q3_recent_failures is None
        assert report.q5_contention_owner is True
        assert report.q1_what_failed is True
        assert report.q4_har_honest is True
        assert report.grade in ("A", "B")
        assert report.privacy_clean


class TestHardPrivacyGate:
    def test_literal_secret_leak_forces_fail(self, tmp_path: Path) -> None:
        b = _ui_failure_bundle(tmp_path)
        # Inject the account email into ui.json (a real leak).
        ui = json.loads((b / "ui.json").read_text())
        ui["leaked"] = "victim@example.com is the account"
        (b / "ui.json").write_text(json.dumps(ui), encoding="utf-8")
        report = score_bundle(b, secrets=["victim@example.com"])
        assert not report.privacy_clean
        assert report.grade.startswith("F")
        assert report.score == 0.0  # hard gate: overrides an otherwise-perfect bundle

    def test_structural_query_string_leak_detected_without_hint(self, tmp_path: Path) -> None:
        b = _ui_failure_bundle(tmp_path)
        net = json.loads((b / "network.json").read_text())
        net["records"][0]["route"] = "https://labs.google/x?X-Goog-Signature=abc123"
        (b / "network.json").write_text(json.dumps(net), encoding="utf-8")
        report = score_bundle(b)  # no secret hints — the pattern alone catches it
        assert not report.privacy_clean
        assert any("query_string" in leak for leak in report.leaks)


class TestDegradedBundles:
    def test_missing_manifest_scores_zero_questions(self, tmp_path: Path) -> None:
        b = tmp_path / "crash-left"
        b.mkdir()
        (b / ".pending").write_bytes(b"\0")  # crash-left, no manifest
        report = score_bundle(b)
        assert report.q1_what_failed is None
        assert report.grade == "F"
        assert "no valid manifest" in " ".join(report.notes)

    def test_empty_journals_fail_q3(self, tmp_path: Path) -> None:
        b = _ui_failure_bundle(tmp_path)
        (b / "network.json").write_text(json.dumps({"records": []}), encoding="utf-8")
        report = score_bundle(b)
        assert report.q3_recent_failures is False  # no recent-traffic evidence
        assert report.grade in ("B", "C")  # still salvageable, just weaker

    def test_all_other_hosts_drop_fidelity(self, tmp_path: Path) -> None:
        b = _ui_failure_bundle(tmp_path)
        net = json.loads((b / "network.json").read_text())
        for r in net["records"]:
            r["host_category"] = "other"  # allowlist reduction never engaged
        (b / "network.json").write_text(json.dumps(net), encoding="utf-8")
        ui = json.loads((b / "ui.json").read_text())
        ui["url"]["host_category"] = "other"
        (b / "ui.json").write_text(json.dumps(ui), encoding="utf-8")
        report = score_bundle(b)
        assert report.fidelity < 0.5  # 1 of 3 fidelity signals (ligatures) survives


def test_null_command_is_a_note_not_a_q1_failure(tmp_path: Path) -> None:
    """A direct-capture bundle (null command) still answers Q1 from version +
    phase; the missing command is a quality NOTE, not a hard failure."""
    report = score_bundle(_ui_failure_bundle(tmp_path, extra={"command": None}))
    assert report.q1_what_failed is True
    assert any("command is null" in n for n in report.notes)
