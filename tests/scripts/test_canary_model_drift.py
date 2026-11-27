"""The canary's MODEL-DRIFT section (#1550): doctor's model-catalog check, for its profile."""

from __future__ import annotations

import pytest

from gflow_cli.services import doctor


def test_the_section_reports_the_check_for_the_canary_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.canary import run_canary

    asked: list[str | None] = []

    def check(_settings: object, profile: str | None = None) -> list[doctor.Finding]:
        asked.append(profile)
        return [doctor.Finding("models.catalog_drift", "warn", "1 difference(s): veo_4", "")]

    monkeypatch.setattr(doctor, "check_model_catalog", check)

    section = run_canary.model_drift_section("ffroliva")

    assert section == "#### MODEL-DRIFT WARN\n\n1 difference(s): veo_4"
    assert asked == ["ffroliva"]


def test_a_broken_check_never_takes_the_canary_down(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.canary import run_canary

    def boom(_settings: object, _profile: str | None = None) -> list[doctor.Finding]:
        raise RuntimeError("https://flow.google.com/secret")

    monkeypatch.setattr(doctor, "check_model_catalog", boom)

    section = run_canary.model_drift_section("ffroliva")

    assert section.startswith("#### MODEL-DRIFT INCONCLUSIVE")
    assert "RuntimeError" in section and "http" not in section
