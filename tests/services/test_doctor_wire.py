"""`gflow doctor` reports the direct-read ("wire") health of the default profile (G6, #1229).

SCENARIO row 25. The probe itself (what is sent, and when nothing is) is proven against a
scripted Flow in tests/api/test_flow_wire.py; here `flow_wire.health` is stubbed so only the
finding is under test: one per status, never flipping doctor's exit 33.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli import profile_store
from gflow_cli.api import flow_wire
from gflow_cli.config import get_settings
from gflow_cli.profile_store import NoDefaultProfileError, NoProfilesError
from gflow_cli.services import doctor


def _health(status: str, build: str | None = None) -> object:
    seen: list[Path] = []

    async def fake(profile_dir: Path, settings: object = None) -> dict[str, str | None]:
        seen.append(profile_dir)
        fake.settings = settings  # type: ignore[attr-defined]
        return {"status": status, "build": build}

    fake.seen = seen  # type: ignore[attr-defined]
    return fake


@pytest.fixture
def default_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(profile_store, "resolve_profile", lambda _flag: "denon")


def _check(settings: object = None) -> list[doctor.Finding]:
    return doctor._check_wire_read(settings or get_settings())  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]


def test_wire_read_is_in_the_check_inventory() -> None:
    assert "auth.wire_read" in doctor.CHECK_IDS


def test_an_accepted_session_passes_and_names_the_build(
    monkeypatch: pytest.MonkeyPatch, default_profile: None
) -> None:
    fake = _health("accepted", "boq_labs-flow-frontend_20261110.00_p0")
    monkeypatch.setattr(flow_wire, "health", fake)

    [finding] = _check()

    assert finding.check == "auth.wire_read"
    assert finding.severity == "pass"
    assert finding.summary.startswith("status=accepted build=boq_labs-flow-frontend_20261110.00_p0")
    assert fake.seen == [get_settings().profile_subdir("denon")]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("status", "hint"),
    [
        ("signed_out_shell", "gflow auth status"),
        ("http_401", "gflow auth status"),
        ("store_unreadable", "close Chrome on it, or run: gflow auth login"),
        ("disabled", "GFLOW_CLI_WIRE_READS"),
        ("proxy_configured", "GFLOW_CLI_BROWSER_PROXY"),
        ("throttled", "wait"),
        ("network", "browser"),
        ("no_chrome_version", "browser"),
        ("http_error", "browser"),
        ("unparseable", "browser"),
    ],
)
def test_every_other_status_is_info_and_never_flips_exit_33(
    monkeypatch: pytest.MonkeyPatch, default_profile: None, status: str, hint: str
) -> None:
    """The browser read still answers in every one of these; a wire state is not a defect."""
    monkeypatch.setattr(flow_wire, "health", _health(status))

    [finding] = _check()

    assert finding.severity == "info"
    assert finding.summary.startswith(f"status={status} build=unknown")
    assert hint in finding.remediation
    assert doctor.DoctorReport(findings=(finding,)).overall_status == "ok"


@pytest.mark.parametrize(
    ("error", "hint"),
    [
        (NoProfilesError("none"), "gflow auth login"),
        (NoDefaultProfileError(["a", "b"]), "gflow auth use"),
    ],
)
def test_no_profile_to_probe_is_info_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, error: Exception, hint: str
) -> None:
    def raises(_flag: object) -> str:
        raise error

    fake = _health("accepted")
    monkeypatch.setattr(profile_store, "resolve_profile", raises)
    monkeypatch.setattr(flow_wire, "health", fake)

    [finding] = _check()

    assert finding.severity == "info"
    assert finding.summary.startswith("status=no_profile")
    assert hint in finding.remediation
    assert fake.seen == []  # type: ignore[attr-defined]


def test_the_suite_default_reports_disabled_with_no_stub(default_profile: None) -> None:
    """tests/conftest.py sets GFLOW_CLI_WIRE_READS=0: the real probe answers without sending."""
    [finding] = _check()

    assert finding.summary.startswith("status=disabled")


def test_the_check_hands_its_own_settings_to_the_probe(
    monkeypatch: pytest.MonkeyPatch, default_profile: None
) -> None:
    """The kill switch and proxy come from doctor's Settings, not a second global lookup."""
    fake = _health("disabled")
    monkeypatch.setattr(flow_wire, "health", fake)
    mine = get_settings().model_copy(update={"wire_reads": False})

    _check(mine)

    assert fake.settings is mine  # type: ignore[attr-defined]
