"""Live BENCHMARK of incident-bundle diagnostic QUALITY against real Flow.

The live spike proves bundles capture; the offline suite proves mechanics.
This gate proves the thing that actually matters for a diagnostics feature:
that a triager who wasn't there can answer the design's five questions from
the bundle alone (spec §2), on data captured from a REAL Flow session — not a
synthetic fixture. It grades two real bundle classes (a UI-state failure and a
metadata-only profile-lock contention) and asserts hard quality FLOORS, so a
regression that quietly hollows out the evidence (empty journals, hosts all
collapsed to ``other``, a null command, a leaked identifier) fails CI here even
though the artifacts still exist.

Zero credits — drives the editor + a contention probe, never a generation.
Opt in: ``-m e2e`` (or ``-m e2e_auth``) with ``GFLOW_CLI_E2E_PROFILE`` set.

The scorer is ``scripts/dev/incident_bundle_quality.py``, also runnable
standalone on any field bundle a user emails.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.config import get_settings
from gflow_cli.errors import FlowAppError

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]

_MOD_PATH = Path(__file__).resolve().parents[2] / "scripts" / "dev" / "incident_bundle_quality.py"
_spec = importlib.util.spec_from_file_location("incident_bundle_quality", _MOD_PATH)
assert _spec is not None and _spec.loader is not None
_scorer = importlib.util.module_from_spec(_spec)
sys.modules["incident_bundle_quality"] = _scorer
_spec.loader.exec_module(_scorer)
score_bundle = _scorer.score_bundle
print_scorecard = _scorer.print_scorecard
QualityReport = _scorer.QualityReport


def _bundles(root: Path) -> set[Path]:
    if not root.is_dir():
        return set()
    return {b for day in root.iterdir() if day.is_dir() for b in day.iterdir() if b.is_dir()}


@pytest.mark.asyncio
async def test_incident_bundle_diagnostic_quality(e2e_profile_dir: Path) -> None:
    settings = get_settings()
    incidents_root = settings.home / "incidents"
    profile_name = os.environ["GFLOW_CLI_E2E_PROFILE"].strip()
    account = ""
    account_file = e2e_profile_dir / ".gflow_account"
    if account_file.exists():
        account = account_file.read_text(encoding="utf-8").strip()
    # Known-sensitive strings that must appear in NO bundle JSON.
    secrets = [s for s in (account, profile_name, "ya29", "SAPISID", "session-token") if s]

    before = _bundles(incidents_root)

    # --- Class 1: a real UI-state failure bundle (no generation submitted) ---
    async with FlowApiClient(e2e_profile_dir, settings=settings) as client:
        page = client._page  # noqa: SLF001
        assert page is not None
        await page.wait_for_timeout(4000)  # let real traffic fill journals
        await client._capture_incident(  # noqa: SLF001
            FlowAppError("live quality benchmark: deliberate capture, no generation"),
            phase="video_generation",
        )

        # --- Class 2: real two-process profile-lock contention (metadata-only) ---
        # The client above holds the lease; a real CLI subprocess contends.
        contender = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-m",
                "gflow_cli.cli",
                "image",
                "t2i",
                "probe",
                "--profile",
                profile_name,
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "PYTHONUTF8": "1", "GFLOW_CLI_HOME": str(settings.home)},
        )
        assert contender.returncode == 11, f"expected exit 11, got {contender.returncode}"

    # Bundles finalize on context exit.
    new = sorted(_bundles(incidents_root) - before)
    assert new, "no incident bundles were produced"

    reports = [score_bundle(b, secrets=secrets) for b in new]
    for report in reports:
        print_scorecard(report)

    by_class: dict[str, QualityReport] = {r.error_class: r for r in reports}

    # --- UI-state bundle: the full evidence set, grade A/B, real fidelity ---
    ui = by_class.get("FlowAppError")
    assert ui is not None, "no FlowAppError (UI-state) bundle produced"
    assert ui.privacy_clean, f"UI bundle leaked: {ui.leaks}"
    assert ui.grade in ("A", "B"), f"UI bundle grade {ui.grade} below floor (score {ui.score})"
    assert ui.q1_what_failed, "UI bundle cannot answer Q1 (what/version/phase failed)"
    assert ui.q2_ui_state, "UI bundle cannot answer Q2 (structural UI state)"
    assert ui.q3_recent_failures, "UI bundle cannot answer Q3 (recent browser/network)"
    assert ui.q4_har_honest, "UI bundle cannot answer Q4 (HAR state honest)"
    assert ui.fidelity == 1.0, f"UI bundle fidelity {ui.fidelity} — captured noise, not real state"
    assert ui.completeness == 1.0, f"UI bundle incomplete: {ui.completeness}"

    # --- Contention bundle: correctly classified, metadata-only, command set ---
    lock = by_class.get("ProfileLockedError")
    assert lock is not None, "no ProfileLockedError (contention) bundle produced"
    assert lock.privacy_clean, f"contention bundle leaked: {lock.leaks}"
    assert lock.q5_contention_owner, "contention bundle did not classify contention"
    assert lock.q2_ui_state is None, "contention bundle should have no page evidence"
    assert lock.q1_what_failed, "contention bundle cannot answer Q1"
    assert not (Path(lock.bundle) / "ui.json").exists(), "metadata-only bundle wrote ui.json"
    # The contender ran through the REAL CLI boundary, so command must be set
    # (proves the cli_command contextvar binding fix live, not just in unit tests).
    lock_manifest = json.loads((Path(lock.bundle) / "manifest.json").read_text(encoding="utf-8"))
    assert lock_manifest.get("command"), "contention bundle manifest.command is null"

    # --- Benchmark floor: the WORST real bundle still clears a usable grade ---
    worst = min(reports, key=lambda r: r.score)
    assert worst.score >= 0.7, f"a real bundle scored below the benchmark floor: {worst.to_dict()}"

    # Persist the scorecard for the evidence ledger (gitignored tmp/).
    out = Path("tmp/live-verify")
    out.mkdir(parents=True, exist_ok=True)
    (out / "incident-quality-benchmark.json").write_text(
        json.dumps([r.to_dict() for r in reports], indent=2), encoding="utf-8"
    )
    time.sleep(0.2)  # let Windows release the sensitive/ screenshot handle
