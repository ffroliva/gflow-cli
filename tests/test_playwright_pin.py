"""The playwright dependency must stay upper-bounded, and the constants the
transport shows users must stay in step with it.

Why this is worth a test: playwright is not an ordinary dependency here — it
ships the browser driver, and every gflow generation is automation through it,
so an untested playwright minor is an untested product. On 2026-08-03 an
install that resolved 1.62.0 against a project locked to 1.59.0 made every
`gflow video i2v` run hang SILENTLY right after the frame upload (browser
alive, no error, no timeout). `uv tool install <path>` ignores `uv.lock`, so
the pyproject range is the only thing standing between a user and an untested
driver. A future edit that relaxes the bound would silently re-open that.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import yaml
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from gflow_cli.api.transports.ui_automation_video import (
    PINNED_PLAYWRIGHT,
    SUPPORTED_PLAYWRIGHT_RANGE,
)

_ROOT = Path(__file__).resolve().parents[1]


def _playwright_requirement() -> Requirement:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps: list[str] = data["project"]["dependencies"]
    for raw in deps:
        req = Requirement(raw)
        if req.name == "playwright":
            return req
    msg = "playwright is not declared in [project.dependencies]"
    raise AssertionError(msg)


def test_playwright_constraint_has_an_upper_bound() -> None:
    """An unbounded range lets an unpinned install pull an untested driver."""
    req = _playwright_requirement()
    operators = {spec.operator for spec in req.specifier}
    assert operators & {"<", "<=", "=="}, (
        f"playwright must be upper-bounded (got {str(req.specifier)!r}); an "
        "untested playwright minor silently wedges the video transport"
    )


def test_locked_playwright_satisfies_the_declared_range() -> None:
    """The version CI and the lockfile exercise must be installable."""
    req = _playwright_requirement()
    assert req.specifier.contains(Version(PINNED_PLAYWRIGHT)), (
        f"the tested version {PINNED_PLAYWRIGHT} does not satisfy {str(req.specifier)!r}"
    )


def test_known_bad_playwright_is_excluded() -> None:
    """1.62.0 is the version observed wedging i2v — it must not resolve."""
    req = _playwright_requirement()
    assert not req.specifier.contains(Version("1.62.0"))


def _dependabot_uv_ignores() -> dict[str, set[str]]:
    """Map `dependency-name` -> ignored update-types for the `uv` ecosystem entry."""
    raw = yaml.safe_load((_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    config: dict[str, Any] = raw
    uv_updates = [entry for entry in config["updates"] if entry["package-ecosystem"] == "uv"]
    assert uv_updates, "the `uv` package-ecosystem entry vanished from .github/dependabot.yml"
    return {
        str(rule["dependency-name"]): {str(kind) for kind in rule["update-types"]}
        for update in uv_updates
        for rule in update.get("ignore", [])
    }


def test_dependabot_ignores_playwright_minor_bumps() -> None:
    """The pyproject upper bound cannot gate Dependabot — this ignore rule must.

    The `uv` ecosystem does not respect a constraint standing in an update's way,
    it REWRITES it. PR #465 widened `playwright>=1.61.0,<1.62.0` to `<1.63.0` and
    locked 1.62.0 — the exact version above is documented as wedging `video i2v`.
    Ignoring only majors therefore re-offers the known-bad driver every Monday,
    and drags the whole grouped weekly batch red on its way out.

    Patch bumps stay allowed deliberately: the 1.61.x headroom exists so a driver
    CVE fix does not require a gflow release.
    """
    ignored = _dependabot_uv_ignores().get("playwright", set())
    assert "version-update:semver-minor" in ignored, (
        "dependabot must ignore playwright MINOR bumps — the pyproject upper "
        "bound does not stop the uv ecosystem from widening it (PR #465)"
    )
    assert "version-update:semver-major" in ignored, "dependabot must ignore playwright MAJOR bumps"


def test_transport_constants_match_pyproject() -> None:
    """The remediation gflow prints must match what the package actually allows.

    Compared as parsed specifier sets, not strings — packaging normalises the
    clause order, so `>=1.59.0,<1.60.0` and `<1.60.0,>=1.59.0` are the same
    constraint and neither spelling should fail this test.
    """
    req = _playwright_requirement()
    assert SpecifierSet(SUPPORTED_PLAYWRIGHT_RANGE) == req.specifier, (
        "the range shown in the stall error drifted from pyproject.toml"
    )
