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
        "cli_version": "0.44.0",
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


def _waf_bundle(root: Path) -> Path:
    """A WafRejectionError network-failure bundle — real shape: a page exists
    (so ``ui.json`` structural DOM IS staged) but WAF is NOT a screenshot
    trigger, so there is no ``sensitive/`` shot, and the failed 403 request in
    the network journal is the primary diagnostic payload.

    Grades this class explicitly: the live benchmark can only floor-test the
    two credit-free classes (UI-state crash + contention), so this offline
    case proves the rubric handles the WAF/wire-format/network class that only
    a real (paid or naturally-occurring) failure captures live."""
    b = root / "waf-bundle"
    b.mkdir()
    _write(
        b,
        "manifest.json",
        {
            "schema": "gflow-incident-v1",
            "incident_id": "corr-waf42",
            "cli_version": "0.44.0",
            "python_version": "3.13.7",
            "os_family": "Windows",
            "command": "image t2i",
            "transport": "ui_automation",
            "error": {
                "class": "WafRejectionError",
                "problem_type": "https://gflow-cli.dev/errors/waf-rejection",
                "exit_code": 10,
                "retryable": True,
                "route": "/v1/projects/{id}/flowMedia:batchGenerateImages",
                "phase": "image_generation",
            },
            "artifact_status": {
                "network.json": "complete",
                "browser.json": "complete",
                "ui.json": "complete",
            },
            "har_state": "disabled",
        },
    )
    # ui.json IS present (structural DOM, staged for any page-available
    # failure); the screenshot is NOT (WAF is not a screenshot trigger).
    _write(
        b,
        "ui.json",
        {
            "ligatures": ["send", "crop_landscape"],
            "ligature_count": 18,
            "tag_counts": {"div": 60, "button": 20},
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
                    "host_category": "aisandbox",
                    "route": "/v1/projects/{id}/flowMedia:batchGenerateImages",
                    "method": "POST",
                    "status_or_failure": "403",
                    "resource_type": "xhr",
                },
                {"host_category": "flow_app", "route": "/fx/trpc/x", "status_or_failure": "200"},
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
            "cli_version": "0.44.0",
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


class TestWafWireFormatClass:
    def test_network_failure_bundle_captures_the_failed_request(self, tmp_path: Path) -> None:
        """The WAF/wire-format class (real shape): structural ui.json present,
        no sensitive/ screenshot, and the failed 403 in the network journal is
        the diagnostic payload. Must grade A/B on that evidence."""
        b = _waf_bundle(tmp_path)
        report = score_bundle(b)
        assert report.q1_what_failed is True
        assert report.q2_ui_state is True  # structural DOM staged (page existed)
        assert report.q3_recent_failures is True  # the 403 request IS the evidence
        assert report.q4_har_honest is True
        assert not (b / "sensitive").exists()  # WAF is correctly NOT screenshot-triggered
        assert report.actionability == 1.0
        assert report.fidelity == 1.0  # aisandbox host recognized; route canonical
        assert report.grade in ("A", "B")
        assert report.score >= 0.8
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
        # 1 of 3 signals survives: the DOM-rendered check. The fixture carries
        # BOTH ligature_count 25 and button 36, so since #696 that signal is
        # satisfied twice over — do not read this as isolating ligatures.
        assert report.fidelity < 0.5


def test_null_command_is_a_note_not_a_q1_failure(tmp_path: Path) -> None:
    """A direct-capture bundle (null command) still answers Q1 from version +
    phase; the missing command is a quality NOTE, not a hard failure."""
    report = score_bundle(_ui_failure_bundle(tmp_path, extra={"command": None}))
    assert report.q1_what_failed is True
    assert any("command is null" in n for n in report.notes)


def test_a_migrated_host_page_without_ligatures_is_still_real_state(tmp_path: Path) -> None:
    """#696: `flow.google.com` renders no `i.google-symbols`, so a good capture scored 0.667.

    The fidelity signal asked "did we snapshot a real rendered page, or noise?"
    and used the Material icon ligature count as its proxy. That proxy is
    labs-only. Measured on a live migrated bundle 2026-09-06: `ligatures: []`
    and `ligature_count: 0`, while the very same snapshot carried
    `div: 28, button: 5, iframe: 1, img: 3` and a Flow page title — a real page,
    well captured, scored as "captured noise, not real state".

    Same shape as #690, where `health_check` only recognised the old host.
    """
    b = _ui_failure_bundle(tmp_path)
    ui = json.loads((b / "ui.json").read_text())
    ui["ligatures"] = []
    ui["ligature_count"] = 0
    ui["tag_counts"] = {"div": 28, "button": 5, "iframe": 1, "img": 3}
    ui["url"] = {"host_category": "flow_app", "route": "/"}
    (b / "ui.json").write_text(json.dumps(ui), encoding="utf-8")

    report = score_bundle(b)

    assert report.fidelity == 1.0, f"{report.fidelity} — notes: {report.notes}"


def test_a_genuinely_empty_snapshot_still_drops_fidelity(tmp_path: Path) -> None:
    """No ligatures AND no controls is noise, and must stay scored as noise.

    Honest about what this is: a **characterization guard**, not a regression
    test for #696. It passes against the pre-#696 expression too, because
    nothing here distinguishes `ligature_count > 0` from
    `ligature_count > 0 or button > 0` — both are False on an empty DOM.

    It earns its place by pinning the arithmetic against a *future* widening.
    The tempting next "fix" is to reach for `div > 0`, or to drop the signal to
    a constant, either of which would score a blank page as real state and
    silently retire the check. Asserting the exact value catches that; asserting
    `< 1.0` would not.
    """
    b = _ui_failure_bundle(tmp_path)
    ui = json.loads((b / "ui.json").read_text())
    ui["ligatures"] = []
    ui["ligature_count"] = 0
    ui["tag_counts"] = {"div": 0, "button": 0}
    (b / "ui.json").write_text(json.dumps(ui), encoding="utf-8")

    report = score_bundle(b)

    # Exactly one of the three signals drops: the DOM is empty, while the
    # network hosts and the page URL are still recognised.
    assert report.fidelity == round(2 / 3, 3), f"{report.fidelity} — notes: {report.notes}"
