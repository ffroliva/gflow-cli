"""Guard: `@e2e`-tagged Gherkin and the e2e suite must agree about each other.

The Bug Lane (`skills/issue-resolve/SKILL.md`) says a scenario on a Flow/UI
surface is formalised as an **e2e** test, and the binding is carried by a
Gherkin tag: pytest-bdd converts `@e2e @e2e_auth` into `pytest.mark.e2e` /
`pytest.mark.e2e_auth`, which is what `addopts`' `-m 'not e2e ...'` filters on.

Three ways that binding silently breaks, all caught here without a browser:

1. **Orphan scenario** — Gherkin written, e2e test never was. The lane's whole
   failure mode ("recorded, not omitted"), invisible to every other gate.
2. **No cost sub-marker** — `-m e2e_auth` cannot select it, so it only ever runs
   in a full `-m e2e` sweep and the nightly canary never sees it.
3. **Untagged live binding** — a feature bound from ``tests/e2e/`` but *not*
   tagged carries no `e2e` marker, so `addopts` does not exclude it and hosted
   CI runs it: Chrome launch, no profile, red for a reason nobody can read.

Static text checks only. Proving these tests **pass** is a different job, done
on a machine with a warm profile (`scripts/canary/`), never in hosted CI.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FEATURES_DIR = _REPO_ROOT / "tests" / "features"
_E2E_DIR = _REPO_ROOT / "tests" / "e2e"

# Mirrors the cost sub-markers registered in pyproject.toml. A bare `@e2e` is
# not selectable by tier, and the canary runs tiers.
_COST_MARKERS = frozenset(
    {
        "e2e_auth",
        "e2e_image",
        "e2e_video",
        "e2e_batch",
        "e2e_data",
        "e2e_scene",
        "e2e_character",
    }
)

_TAG_LINE = re.compile(r"^\s*@[\w@\s]+$")


def _tags(feature: Path) -> set[str]:
    """Every Gherkin tag in a feature file, Feature- and Scenario-level alike."""
    found: set[str] = set()
    for line in feature.read_text(encoding="utf-8").splitlines():
        if _TAG_LINE.match(line):
            found.update(token.lstrip("@") for token in line.split() if token.startswith("@"))
    return found


def _binders(feature_name: str, search_dir: Path) -> list[Path]:
    """Modules under `search_dir` whose `scenarios(...)` call names this feature."""
    if not search_dir.is_dir():
        return []
    call = re.compile(rf"scenarios?\(\s*[\"'][^\"']*{re.escape(feature_name)}[\"']")
    return sorted(
        path for path in search_dir.rglob("*.py") if call.search(path.read_text(encoding="utf-8"))
    )


def _feature_files() -> list[Path]:
    return sorted(_FEATURES_DIR.glob("*.feature"))


def test_every_e2e_tagged_feature_is_bound_under_tests_e2e() -> None:
    orphans = [
        feature.name
        for feature in _feature_files()
        if "e2e" in _tags(feature) and not _binders(feature.name, _E2E_DIR)
    ]
    assert not orphans, (
        f"@e2e-tagged Gherkin with no binding module under tests/e2e/: {orphans}. "
        "The scenario was written and the e2e test never was — write "
        f"tests/e2e/test_<slug>_bdd.py calling scenarios('../features/<name>')."
    )


def test_every_e2e_tagged_feature_declares_a_cost_tier() -> None:
    untiered = [
        feature.name
        for feature in _feature_files()
        if "e2e" in (tags := _tags(feature)) and not (tags & _COST_MARKERS)
    ]
    assert not untiered, (
        f"@e2e Gherkin with no cost sub-marker: {untiered}. "
        f"Add one of {sorted(_COST_MARKERS)} so `-m <tier>` and the canary can select it."
    )


def test_every_feature_bound_from_tests_e2e_is_tagged_e2e() -> None:
    """The dangerous direction: an untagged live binding runs in hosted CI."""
    untagged = [
        feature.name
        for feature in _feature_files()
        if _binders(feature.name, _E2E_DIR) and "e2e" not in _tags(feature)
    ]
    assert not untagged, (
        f"Bound from tests/e2e/ but not tagged @e2e: {untagged}. "
        "Without the tag these scenarios carry no e2e marker, so addopts does not "
        "exclude them and hosted CI will try to drive a browser."
    )


def test_the_guard_actually_fires(tmp_path: Path) -> None:
    """A guard that has only ever passed vacuously has not been tested.

    Feeds the detectors a synthetic orphan and a synthetic bare `@e2e`, and
    proves each one is seen — so a green suite above means "no orphans", not
    "the check never looked".
    """
    orphan = tmp_path / "orphan.feature"
    orphan.write_text("@e2e\nFeature: nobody binds me\n", encoding="utf-8")
    assert _tags(orphan) == {"e2e"}
    assert not _binders(orphan.name, _E2E_DIR)
    assert not _tags(orphan) & _COST_MARKERS

    tiered = tmp_path / "tiered.feature"
    tiered.write_text("  @e2e @e2e_auth\nFeature: tagged at scenario level\n", encoding="utf-8")
    assert _tags(tiered) & _COST_MARKERS == {"e2e_auth"}

    binder_dir = tmp_path / "e2e"
    binder_dir.mkdir()
    (binder_dir / "test_x_bdd.py").write_text(
        'from pytest_bdd import scenarios\n\nscenarios("../features/orphan.feature")\n',
        encoding="utf-8",
    )
    assert _binders("orphan.feature", binder_dir)
    assert not _binders("unrelated.feature", binder_dir)
