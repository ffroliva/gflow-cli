"""`gflow doctor` compares Flow's live model catalog with gflow's mapping (#1550).

The comparison itself is ``api.model_catalog.catalog_drift`` (tests/api/test_model_catalog.py);
the read is ``flow_wire.read_model_catalog`` (tests/api/test_flow_wire.py). Here the read is
stubbed, so only the finding is under test: a removed family or key is a warning (exit 33), an
added one is info, and every state in which nothing could be compared is info, sent under the
direct read's own rules.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gflow_cli import profile_store
from gflow_cli.api import flow_wire
from gflow_cli.api.flow_wire import WireFallbackReason, WireReadUnavailable
from gflow_cli.config import get_settings
from gflow_cli.errors import RateLimitError
from gflow_cli.services import doctor

FIXTURE = Path(__file__).parent.parent / "fixtures" / "flow_htrjv_model_families.json"
_CHECK = "models.catalog_drift"


def _recorded() -> list[object]:
    families: list[object] = json.loads(FIXTURE.read_text(encoding="utf-8"))["families"]
    return families


def _on() -> object:
    return get_settings().model_copy(update={"wire_reads": True, "browser_proxy": None})


class _Read:
    """A stubbed catalog read: what it returns, and the profiles it was asked for."""

    def __init__(self) -> None:
        self.payload: object = [_recorded()]
        self.seen: list[Path] = []

    async def __call__(self, profile_dir: Path) -> object:
        self.seen.append(profile_dir)
        return self.payload


@pytest.fixture
def read(monkeypatch: pytest.MonkeyPatch) -> _Read:
    fake = _Read()
    monkeypatch.setattr(flow_wire, "read_model_catalog", fake)
    monkeypatch.setattr(profile_store, "resolve_profile", lambda flag: flag or "denon")
    return fake


def test_the_check_is_in_the_inventory() -> None:
    assert _CHECK in doctor.CHECK_IDS


def test_a_catalog_that_matches_passes(read: _Read) -> None:
    [finding] = doctor.check_model_catalog(_on())  # type: ignore[arg-type]

    assert (finding.check, finding.severity) == (_CHECK, "pass")
    assert finding.summary.startswith("15 families, all mapped")
    assert read.seen == [get_settings().profile_subdir("denon")]


def test_a_family_flow_added_is_info_not_a_warning(read: _Read) -> None:
    """Users cannot map a new model themselves: it must not turn doctor's exit into 33."""
    extra = ["Veo 4", [["veo_4_t2v"]], True, "veo_4"]
    read.payload = [[*_recorded(), extra]]

    [finding] = doctor.check_model_catalog(_on())  # type: ignore[arg-type]

    assert finding.severity == "info"
    assert not finding.summary.startswith("skipped")
    assert "added family veo_4 (Veo 4): not mapped in gflow" in finding.summary
    assert "gflow update" in finding.remediation


def test_a_family_flow_removed_is_a_warning(read: _Read) -> None:
    read.payload = [[f for f in _recorded() if f[3] != "veo_3_1_lite"]]  # type: ignore[index]

    [finding] = doctor.check_model_catalog(_on())  # type: ignore[arg-type]

    assert finding.severity == "warn"
    assert "removed family veo_3_1_lite" in finding.summary


def test_a_removal_with_an_addition_is_a_warning_naming_both(read: _Read) -> None:
    extra = ["Veo 4", [["veo_4_t2v"]], True, "veo_4"]
    kept = [f for f in _recorded() if f[3] != "veo_3_1_lite"]  # type: ignore[index]
    read.payload = [[*kept, extra]]

    [finding] = doctor.check_model_catalog(_on())  # type: ignore[arg-type]

    assert finding.severity == "warn"
    assert "removed family veo_3_1_lite" in finding.summary
    assert "added family veo_4" in finding.summary


def test_an_undecodable_catalog_is_skipped_as_unparseable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def raises(_profile_dir: Path) -> object:
        raise WireReadUnavailable(WireFallbackReason.UNPARSEABLE)

    monkeypatch.setattr(flow_wire, "read_model_catalog", raises)
    monkeypatch.setattr(profile_store, "resolve_profile", lambda _flag: "denon")

    [finding] = doctor.check_model_catalog(_on())  # type: ignore[arg-type]

    assert finding.severity == "info"
    assert "unparseable" in finding.summary
    assert "format changed" not in finding.summary


def test_a_reply_with_no_families_is_a_warning(read: _Read) -> None:
    read.payload = [[1, 2, 3]]

    [finding] = doctor.check_model_catalog(_on())  # type: ignore[arg-type]

    assert finding.severity == "warn"
    assert "no model families" in finding.summary


def test_a_named_profile_is_read_instead_of_the_default(read: _Read) -> None:
    doctor.check_model_catalog(_on(), "canary")  # type: ignore[arg-type]

    assert read.seen == [get_settings().profile_subdir("canary")]


@pytest.mark.parametrize(
    ("update", "word"),
    [
        ({"wire_reads": False}, "GFLOW_CLI_WIRE_READS"),
        ({"wire_reads": True, "browser_proxy": "http://p:1"}, "proxy"),
    ],
)
def test_wire_reads_off_skip_the_check_and_send_nothing(
    read: _Read, update: dict[str, object], word: str
) -> None:
    settings = get_settings().model_copy(update=update)

    [finding] = doctor.check_model_catalog(settings)

    assert finding.severity == "info"
    assert finding.summary.startswith("skipped")
    assert word in finding.summary
    assert read.seen == []


def test_the_suite_default_skips_with_no_stub() -> None:
    """tests/conftest.py sets GFLOW_CLI_WIRE_READS=0: nothing is sent."""
    [finding] = doctor.check_model_catalog(get_settings())

    assert (finding.severity, finding.summary.startswith("skipped")) == ("info", True)


@pytest.mark.parametrize(
    "error",
    [
        WireReadUnavailable(WireFallbackReason.STORE_UNREADABLE),
        RateLimitError("HTTP 429", status=429, route="credits_wire", retry_after=60.0),
    ],
)
def test_an_unavailable_read_is_info(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    async def raises(_profile_dir: Path) -> object:
        raise error

    monkeypatch.setattr(flow_wire, "read_model_catalog", raises)
    monkeypatch.setattr(profile_store, "resolve_profile", lambda _flag: "denon")

    [finding] = doctor.check_model_catalog(_on())  # type: ignore[arg-type]

    assert finding.severity == "info"
    assert finding.summary.startswith("skipped")


def test_no_default_profile_is_info(monkeypatch: pytest.MonkeyPatch) -> None:
    def raises(_flag: object) -> str:
        raise profile_store.NoProfilesError

    monkeypatch.setattr(profile_store, "resolve_profile", raises)

    [finding] = doctor.check_model_catalog(_on())  # type: ignore[arg-type]

    assert (finding.severity, "gflow auth login" in finding.remediation) == ("info", True)
