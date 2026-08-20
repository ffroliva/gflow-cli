"""Unit tests for the nightly canary's verdict function (#502).

The canary's value rests entirely on RED meaning one thing: "code or Flow
drifted". The two ways to destroy that are classifying session rot as RED
(trains "just re-login", so real reds get ignored) and classifying a lease
collision as RED (fires whenever the maintainer had Chrome open). Both are
checked here, including the ordering that keeps them ahead of the generic
failure arm.

#502's DoD requires all four states to be reachable — this is that proof.
"""

from __future__ import annotations

import pytest

from scripts.canary.run_canary import (
    AUTH_EXPIRED,
    DEFERRED,
    GREEN,
    RED,
    Result,
    classify,
    parse_junit,
    render,
)

_LEASE_TRACEBACK = (
    "E   gflow_cli.errors.ProfileLockedError: Profile locked\nE   A live process holds this profile"
)
_AUTH_TRACEBACK = "E   gflow_cli.errors.AuthMissingError: no saved session"
_DRIFT_TRACEBACK = "E   gflow_cli.errors.UiSelectorDriftError: composer probe failed"


def test_all_passing_is_green() -> None:
    assert classify(auth_ok_before=True, pytest_rc=0, failure_text="") == GREEN


def test_failed_auth_probe_short_circuits_to_auth_expired() -> None:
    """The probe runs first, so the suite never ran — pytest_rc is None."""
    assert classify(auth_ok_before=False, pytest_rc=None, failure_text="") == AUTH_EXPIRED


def test_genuine_failure_is_red() -> None:
    assert (
        classify(
            auth_ok_before=True, pytest_rc=1, failure_text=_DRIFT_TRACEBACK, auth_ok_after=True
        )
        == RED
    )


def test_lease_collision_is_deferred_not_red() -> None:
    """Maintainer had gflow open. Neutral — nothing ran, nothing is broken."""
    assert classify(auth_ok_before=True, pytest_rc=1, failure_text=_LEASE_TRACEBACK) == DEFERRED


def test_mid_run_session_rot_is_auth_expired_not_red() -> None:
    """Healthy at the start, dead at the end: genuine rot, not drift."""
    assert (
        classify(
            auth_ok_before=True, pytest_rc=1, failure_text=_AUTH_TRACEBACK, auth_ok_after=False
        )
        == AUTH_EXPIRED
    )


def test_auth_shaped_failure_with_a_still_valid_session_is_red() -> None:
    """Regression guard for the first live run (2026-08-20).

    ``test_rest_upload_image_authenticates_after_sapisidhash`` failed with
    ``AuthExpiredError``, yet ``gflow auth status`` verified clean immediately
    afterwards. That is a real divergence between two auth surfaces, not
    session rot. A name-matching classifier reported it correctly only by
    accident (its list had missed ``AuthExpiredError`` entirely) — and once
    "fixed" by adding the name, it would have buried the finding as
    AUTH-EXPIRED. The still-valid session is what makes this RED.
    """
    aisandbox = "E   gflow_cli.errors.AuthExpiredError: Authentication expired: Unauthorized"
    assert (
        classify(auth_ok_before=True, pytest_rc=1, failure_text=aisandbox, auth_ok_after=True)
        == RED
    )


def test_lease_marker_wins_over_a_concurrent_drift_message() -> None:
    """Ordering guard: a lease collision can abort a test whose output also
    mentions drift. Contention is the cause; reporting RED would be a lie."""
    combined = f"{_DRIFT_TRACEBACK}\n{_LEASE_TRACEBACK}"
    assert classify(auth_ok_before=True, pytest_rc=1, failure_text=combined) == DEFERRED


@pytest.mark.parametrize("state", [GREEN, RED, AUTH_EXPIRED, DEFERRED])
def test_every_state_renders(state: str) -> None:
    """#502 DoD: all four states reachable and renderable."""
    body = render(Result(state=state), sha="abc1234", markers="e2e_auth", stamp="2026-08-20")
    assert state in body
    assert "abc1234" in body


def test_render_publishes_test_names_but_no_failure_text() -> None:
    """Sanitization contract: names are publishable, tracebacks are not."""
    result = Result(state=RED, failed=1, failing=("tests.e2e.test_x::test_y",))
    body = render(result, sha="abc1234", markers="e2e_auth", stamp="2026-08-20")
    assert "tests.e2e.test_x::test_y" in body
    assert "ProfileLockedError" not in body
    assert "Traceback" not in body


def test_parse_junit_counts_and_names(tmp_path) -> None:
    xml = tmp_path / "j.xml"
    xml.write_text(
        '<?xml version="1.0"?><testsuites><testsuite tests="3" failures="1" '
        'errors="0" skipped="1">'
        '<testcase classname="tests.e2e.test_a" name="test_ok"/>'
        '<testcase classname="tests.e2e.test_a" name="test_bad"><failure>boom</failure></testcase>'
        '<testcase classname="tests.e2e.test_a" name="test_skip"><skipped/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    passed, failed, skipped, failing = parse_junit(xml)
    assert (passed, failed, skipped) == (1, 1, 1)
    assert failing == ("tests.e2e.test_a::test_bad",)


def test_parse_junit_missing_file_is_empty_not_an_error() -> None:
    """A crashed pytest writes no XML; the canary must still report, not blow up."""
    assert parse_junit(pytest.importorskip("pathlib").Path("does-not-exist.xml")) == (0, 0, 0, ())


def test_engine_downgrade_is_deferred_not_red() -> None:
    """Second gap found on the first live run (2026-08-20).

    Running against the other profile hit ProfileEngineDowngradeError (profile
    written by Chromium 151, bundled engine 149). Like ProfileLockedError — its
    named sibling in errors.py — it fails closed BEFORE any browser starts, so
    the run never reached Flow and cannot evidence drift. Both share
    ConfigurationError's exit 11, so only the class name discriminates.
    """
    downgrade = (
        "E   gflow_cli.errors.ProfileEngineDowngradeError: Profile was written "
        "by a newer Chromium: 151.0.7922.137 vs 149.0.7827.55"
    )
    assert classify(auth_ok_before=True, pytest_rc=1, failure_text=downgrade) == DEFERRED


def test_preserve_evidence_copies_junit_under_a_stamped_name(tmp_path, monkeypatch) -> None:
    """A RED must stay triageable after the next run overwrites the JUnit file."""
    from scripts.canary import run_canary

    junit = tmp_path / "canary-junit.xml"
    junit.write_text("<testsuites/>", encoding="utf-8")
    monkeypatch.setattr(run_canary, "JUNIT_PATH", junit)

    kept = run_canary.preserve_evidence("2026-08-20 21:05 UTC")
    assert kept is not None
    assert kept.exists() and kept != junit
    assert kept.read_text(encoding="utf-8") == "<testsuites/>"


def test_preserve_evidence_is_a_noop_when_pytest_wrote_nothing(tmp_path, monkeypatch) -> None:
    """A crashed pytest leaves no XML; preserving must not raise on the way to publishing."""
    from scripts.canary import run_canary

    monkeypatch.setattr(run_canary, "JUNIT_PATH", tmp_path / "absent.xml")
    assert run_canary.preserve_evidence("2026-08-20 21:05 UTC") is None
