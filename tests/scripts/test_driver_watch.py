"""Unit tests for the browser-driver watch (scripts/ci/driver_watch.py).

Dependabot ignores playwright and patchright by design, so this job is the only thing
that announces a driver release the pins refuse. Offline: no PyPI, no `gh`.
"""

from __future__ import annotations

import pytest

from scripts.ci import driver_watch as dw

_PYPROJECT = """
[project]
name = "x"
dependencies = ["click>=8", "playwright>=1.61.0,<1.62.0"]
[project.optional-dependencies]
patchright = ["patchright==1.64.4"]
dev = ["pytest>=8"]
"""


def test_pins_are_read_from_dependencies_and_extras() -> None:
    assert dw.pins(_PYPROJECT) == {"playwright": ">=1.61.0,<1.62.0", "patchright": "==1.64.4"}


@pytest.mark.parametrize(
    ("spec", "version", "ok"),
    [
        (">=1.61.0,<1.62.0", "1.61.3", True),
        (">=1.61.0,<1.62.0", "1.62.0", False),
        ("==1.64.4", "1.64.4", True),
        ("==1.64.4", "1.64.10", False),
        ("==1.64", "1.64.0", True),  # a short version pads with zeros
    ],
)
def test_admits(spec: str, version: str, ok: bool) -> None:
    assert dw.admits(spec, version) is ok


def test_an_unsupported_clause_is_loud() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        dw.admits("~=1.6", "1.6.1")


def test_only_releases_outside_the_pin_are_bumps() -> None:
    found = dw.bumps(dw.pins(_PYPROJECT), {"playwright": "1.61.1", "patchright": "1.65.0"})
    assert found == [dw.Bump("patchright", "==1.64.4", "1.65.0")]
    assert found[0].title == "Driver release available: patchright 1.65.0 (pinned ==1.64.4)"


def test_one_issue_per_package_created_then_updated_never_duplicated() -> None:
    newer = dw.Bump("patchright", "==1.64.4", "1.66.0")
    stale = {"number": 7, "title": "Driver release available: patchright 1.65.0 (pinned ==1.64.4)"}
    assert dw.plan([newer], []) == [("create", newer, 0)]
    assert dw.plan([newer], [stale]) == [("edit", newer, 7)]
    assert dw.plan([newer], [{"number": 8, "title": newer.title}]) == []  # already current


def test_the_issue_body_names_the_gates_and_never_auto_merges() -> None:
    text = dw.body(dw.Bump("playwright", ">=1.61.0,<1.62.0", "1.62.0"))
    assert "engine-contract" in text and "never merged automatically" in text


def test_an_issue_whose_pin_now_admits_the_release_is_closed() -> None:
    stale = {
        "number": 7,
        "title": "Driver release available: patchright 1.65.0 (pinned ==1.64.4)",
        "state": "OPEN",
    }
    assert dw.plan([], [stale]) == [("close", None, 7)]


def test_a_release_a_maintainer_closed_is_not_reopened() -> None:
    bump = dw.Bump("patchright", "==1.64.4", "1.65.0")
    declined = {"number": 9, "title": bump.title, "state": "CLOSED"}
    assert dw.plan([bump], [declined]) == []


@pytest.mark.parametrize(("version", "ok"), [("1.64.4.post1", True), ("1.65.0rc1", False)])
def test_post_and_pre_releases_compare_by_their_release_number(version: str, ok: bool) -> None:
    assert dw.admits("==1.64.4", version) is ok


def test_extras_in_a_requirement_are_not_part_of_its_pin() -> None:
    text = '[project]\ndependencies = ["playwright[x]>=1.61,<1.62"]\n'
    assert dw.pins(text) == {"playwright": ">=1.61,<1.62"}


def test_conflicting_pins_for_one_driver_are_loud() -> None:
    text = (
        '[project]\ndependencies = ["playwright>=1.61"]\n'
        '[project.optional-dependencies]\ny = ["playwright==1.60"]\n'
    )
    with pytest.raises(ValueError, match="pinned twice"):
        dw.pins(text)
