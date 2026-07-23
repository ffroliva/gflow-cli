"""Assess the DIAGNOSTIC QUALITY of a private incident bundle.

Artifact-exists checks (the live spike) prove capture works; they do NOT prove
the captured data actually lets a triager who wasn't there fix the problem.
This scorer answers that: for a given bundle directory it scores the design's
five diagnostic questions (spec §2) plus quality dimensions, and prints a
grade. Reusable on ANY field bundle a user emails — not just test output.

Standalone:
    .venv/Scripts/python.exe scripts/dev/incident_bundle_quality.py <bundle-dir> [secret...]

Importable (the e2e benchmark uses this):
    from incident_bundle_quality import score_bundle, QualityReport

Pure stdlib; never mutates the bundle.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import cast


def _as_dict(value: object) -> dict[str, object]:
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return cast("list[object]", value) if isinstance(value, list) else []


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


_VALID_HAR_STATES = {"disabled", "pending_flush", "complete", "possibly_incomplete"}
_KNOWN_HOST_CATEGORIES = {
    "flow_app",
    "aisandbox",
    "google_auth",
    "google_cdn",
    "google_static",
    "google_web",
}
# Structural leak heuristics — a well-formed bundle contains NONE of these.
_LEAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("query_string", re.compile(r"https?://[^\s\"]+\?[^\s\"]+")),
    ("oauth_token", re.compile(r"ya29\.[A-Za-z0-9_-]{5,}")),
    ("sapisid", re.compile(r"SAPISID", re.IGNORECASE)),
    ("session_token", re.compile(r"session-token")),
    ("bearer", re.compile(r"[Bb]earer\s+[A-Za-z0-9._-]{10,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.")),
)
# Failure classes where no page exists, so page-derived evidence is N/A.
_METADATA_ONLY_CLASSES = {"ProfileLockedError"}


@dataclass
class QualityReport:
    """Structured diagnostic-quality assessment of one incident bundle."""

    bundle: str
    error_class: str = ""
    # Design §2 questions: True=answerable, False=not, None=N/A for this class.
    q1_what_failed: bool | None = None
    q2_ui_state: bool | None = None
    q3_recent_failures: bool | None = None
    q4_har_honest: bool | None = None
    q5_contention_owner: bool | None = None
    # Quality dimensions, 0.0–1.0.
    completeness: float = 0.0
    fidelity: float = 0.0
    actionability: float = 0.0
    privacy_clean: bool = True
    leaks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def answerable(self) -> dict[str, bool | None]:
        return {
            "q1_what_failed": self.q1_what_failed,
            "q2_ui_state": self.q2_ui_state,
            "q3_recent_failures": self.q3_recent_failures,
            "q4_har_honest": self.q4_har_honest,
            "q5_contention_owner": self.q5_contention_owner,
        }

    @property
    def score(self) -> float:
        """0.0–1.0 overall. Privacy is a HARD gate: any leak → 0.0."""
        if not self.privacy_clean:
            return 0.0
        applicable = [v for v in self.answerable.values() if v is not None]
        q_ratio = sum(bool(v) for v in applicable) / len(applicable) if applicable else 0.0
        # Questions weigh most (they are the point of the bundle); dimensions refine.
        return round(
            0.55 * q_ratio
            + 0.15 * self.completeness
            + 0.15 * self.fidelity
            + 0.15 * self.actionability,
            3,
        )

    @property
    def grade(self) -> str:
        if not self.privacy_clean:
            return "F (privacy leak)"
        s = self.score
        for threshold, g in ((0.9, "A"), (0.8, "B"), (0.7, "C"), (0.6, "D")):
            if s >= threshold:
                return g
        return "F"

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["score"] = self.score
        d["grade"] = self.grade
        return d


def _load(bundle: Path, name: str) -> object | None:
    path = bundle / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _scan_leaks(bundle: Path, secrets: list[str]) -> list[str]:
    hits: list[str] = []
    for artifact in sorted(bundle.rglob("*.json")):
        text = artifact.read_text(encoding="utf-8", errors="replace")
        for secret in secrets:
            if secret and secret in text:
                hits.append(f"{artifact.name}:literal:{secret[:12]}")
        for label, pattern in _LEAK_PATTERNS:
            if pattern.search(text):
                hits.append(f"{artifact.name}:{label}")
    return sorted(set(hits))


def score_bundle(bundle: Path, secrets: list[str] | None = None) -> QualityReport:
    """Score one finalized incident bundle. ``secrets`` are known-sensitive
    strings (account email, profile name, live tokens) that must NOT appear."""
    report = QualityReport(bundle=str(bundle))
    raw_manifest = _load(bundle, "manifest.json")
    if not isinstance(raw_manifest, dict):
        report.notes.append("no valid manifest.json — bundle incomplete or crash-left")
        report.privacy_clean = not _scan_leaks(bundle, secrets or [])
        return report

    manifest = _as_dict(raw_manifest)
    error = _as_dict(manifest.get("error"))
    error_class = str(error.get("class", ""))
    report.error_class = error_class
    metadata_only = error_class in _METADATA_ONLY_CLASSES

    # --- Privacy: the hard gate ---
    report.leaks = _scan_leaks(bundle, secrets or [])
    report.privacy_clean = not report.leaks

    # --- Q1: which command / version / lifecycle phase failed? ---
    q1_fields = [
        manifest.get("cli_version"),
        manifest.get("python_version"),
        manifest.get("os_family"),
        error.get("class"),
        error.get("phase"),
    ]
    report.q1_what_failed = all(bool(v) for v in q1_fields) and isinstance(
        error.get("exit_code"), int
    )
    if not manifest.get("command"):
        report.notes.append("Q1 partial: manifest.command is null (direct-capture path?)")

    # --- Q2: structural UI state (N/A for metadata-only classes) ---
    if metadata_only:
        report.q2_ui_state = None
        if (bundle / "ui.json").exists():
            report.notes.append("Q2: metadata-only class unexpectedly wrote ui.json")
    else:
        ui = _as_dict(_load(bundle, "ui.json"))
        report.q2_ui_state = bool(
            _int(ui.get("ligature_count")) > 0 or ui.get("overlays") or ui.get("tag_counts")
        )

    # --- Q3: recent browser/network failures ---
    network = _as_dict(_load(bundle, "network.json"))
    browser = _load(bundle, "browser.json")
    if metadata_only:
        report.q3_recent_failures = None  # no live context existed
    else:
        recs = _as_list(network.get("records"))
        report.q3_recent_failures = len(recs) > 0 and isinstance(browser, dict)

    # --- Q4: HAR state honest ---
    report.q4_har_honest = manifest.get("har_state") in _VALID_HAR_STATES

    # --- Q5: contention owner identified (only for contention bundles) ---
    if metadata_only:
        # The bundle correctly classifies contention and is metadata-only;
        # owner evidence itself is a live-only local surface by design (§6.4).
        report.q5_contention_owner = bool(error.get("phase")) and not (bundle / "ui.json").exists()
    else:
        report.q5_contention_owner = None

    # --- Completeness: expected artifact set present + finalized ---
    status = manifest.get("artifact_status")
    status = status if isinstance(status, dict) else {}
    if metadata_only:
        report.completeness = (
            1.0 if not status or all(v in ("complete", "partial") for v in status.values()) else 0.5
        )
    else:
        expected = {"network.json", "browser.json", "ui.json"}
        present = {k for k, v in status.items() if v in ("complete", "partial")}
        report.completeness = round(len(expected & present) / len(expected), 3)

    # --- Fidelity: captured REAL state, not noise ---
    fidelity_signals: list[bool] = []
    if not metadata_only:
        records = _as_list(network.get("records"))
        cats = {_as_dict(r).get("host_category") for r in records}
        # At least one record recognized as a known Flow host → the allowlist
        # reduction actually engaged (not everything collapsed to "other").
        fidelity_signals.append(bool(cats & _KNOWN_HOST_CATEGORIES))
        ui = _as_dict(_load(bundle, "ui.json"))
        if ui:
            fidelity_signals.append(_int(ui.get("ligature_count")) > 0)
            fidelity_signals.append(_as_dict(ui.get("url")).get("host_category") != "other")
    report.fidelity = (
        round(sum(fidelity_signals) / len(fidelity_signals), 3) if fidelity_signals else 1.0
    )

    # --- Actionability: enough to triage + dedup a distinct failure ---
    action_signals = [
        bool(error.get("class")),
        isinstance(error.get("exit_code"), int),
        isinstance(error.get("retryable"), bool),
        bool(error.get("phase")),
        bool(manifest.get("incident_id")),
    ]
    report.actionability = round(sum(action_signals) / len(action_signals), 3)
    return report


def print_scorecard(report: QualityReport) -> None:
    print(f"\nIncident bundle quality — {Path(report.bundle).name}")
    print(f"  error class : {report.error_class or '(unknown)'}")
    print(f"  GRADE       : {report.grade}   (score {report.score})")
    labels = {
        "q1_what_failed": "Q1 what/version/phase failed",
        "q2_ui_state": "Q2 structural UI state",
        "q3_recent_failures": "Q3 recent browser/network",
        "q4_har_honest": "Q4 HAR state honest",
        "q5_contention_owner": "Q5 contention classified",
    }
    for key, ans in report.answerable.items():
        mark = "N/A " if ans is None else ("YES " if ans else "NO  ")
        print(f"    [{mark}] {labels[key]}")
    print(
        f"  dimensions  : completeness={report.completeness} "
        f"fidelity={report.fidelity} actionability={report.actionability}"
    )
    print(f"  privacy     : {'CLEAN' if report.privacy_clean else 'LEAK ' + str(report.leaks)}")
    for note in report.notes:
        print(f"  note        : {note}")


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: incident_bundle_quality.py <bundle-dir> [known-secret ...]")
        return 2
    bundle = Path(argv[0])
    if not bundle.is_dir():
        print(f"not a directory: {bundle}")
        return 2
    report = score_bundle(bundle, secrets=argv[1:])
    print_scorecard(report)
    return 0 if report.privacy_clean else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
