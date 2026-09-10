"""Marker-tier invariants and auto-marker hook self-tests.

Two concerns:

1. **Marker invariants** — every test file under ``tests/e2e/`` must declare a
   cost sub-marker (``e2e_auth``, ``e2e_image``, ``e2e_video``, ``e2e_batch``,
   or ``e2e_data``).  A test that carries only ``e2e`` (or none at all) cannot
   be selectively skipped by cost tier, which defeats the whole point of the
   layered strategy.

2. **Auto-marker hook self-test** — ``conftest.pytest_collection_modifyitems``
   uses ``item.path.parts`` to decide which directory-based marker to inject.
   If the path logic ever regresses (e.g. a platform-specific path format), the
   auto-injection silently breaks and e2e tests start running in unguarded
   ``pytest`` invocations.  These tests confirm the hook behaves correctly on
   the current platform without needing a real pytest collection run.
"""

from __future__ import annotations

import pathlib
import re
import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Constants — cost sub-markers that every e2e test must carry at least one of
# ---------------------------------------------------------------------------

_COST_SUB_MARKERS: frozenset[str] = frozenset(
    {"e2e_auth", "e2e_image", "e2e_video", "e2e_batch", "e2e_data", "e2e_scene", "e2e_character"}
)

_E2E_TEST_DIR = pathlib.Path(__file__).parent / "e2e"


# ---------------------------------------------------------------------------
# Marker-invariant checks
# ---------------------------------------------------------------------------


#: A BDD-bound e2e module declares no `pytest.mark.*` at all — pytest-bdd derives its
#: markers from the Gherkin tags at collection time. Reading Python source alone
#: therefore sees nothing and reports a correctly-tiered file as untiered. Resolving
#: the tags keeps the Gherkin the SINGLE source of truth: duplicating the tier into a
#: `pytestmark` would satisfy this check and then drift the moment a Feature is
#: retagged. See docs/E2E_TESTING.md § BDD-bound e2e.
_SCENARIOS_CALL = re.compile(r"scenarios?\(\s*[\"']([^\"']+)[\"']")
_TAG_LINE = re.compile(r"^\s*@[\w@\s]+$")


def _collect_e2e_test_files() -> list[pathlib.Path]:
    return sorted(_E2E_TEST_DIR.glob("test_*.py"))


def _bound_feature_tags(test_file: pathlib.Path) -> list[str]:
    """Gherkin tags of every feature file this module binds via ``scenarios(...)``."""
    tags: list[str] = []
    for ref in _SCENARIOS_CALL.findall(test_file.read_text(encoding="utf-8")):
        feature = (test_file.parent / ref).resolve()
        if not feature.is_file():  # pragma: no cover - a broken bind fails at collection
            continue
        for line in feature.read_text(encoding="utf-8").splitlines():
            if _TAG_LINE.match(line):
                tags.extend(token.lstrip("@") for token in line.split() if token.startswith("@"))
    return tags


def _extract_pytestmarks(source: str) -> list[str]:
    """Return all marker *names* referenced in ``pytestmark`` declarations."""
    markers: list[str] = []
    for line in source.splitlines():
        stripped = line.strip()
        if "pytest.mark." not in stripped:
            continue
        # Pick out the bit after "pytest.mark." stopping at the first non-id char
        idx = stripped.find("pytest.mark.")
        while idx != -1:
            rest = stripped[idx + len("pytest.mark.") :]
            name_chars = []
            for ch in rest:
                if ch.isalnum() or ch == "_":
                    name_chars.append(ch)
                else:
                    break
            if name_chars:
                markers.append("".join(name_chars))
            idx = stripped.find("pytest.mark.", idx + 1)
    return markers


@pytest.mark.parametrize("test_file", _collect_e2e_test_files(), ids=lambda p: p.name)
def test_e2e_file_has_cost_sub_marker(test_file: pathlib.Path) -> None:
    """Every e2e test file must reference at least one cost sub-marker.

    The rule is checked at the *file* level (``pytestmark`` or individual
    decorators) rather than per-function to keep the check fast and avoid
    importing test modules.  A file-level ``pytestmark`` that covers all tests
    is the canonical approach; individual ``@pytest.mark.*`` decorators on
    every function are also accepted.

    A **BDD-bound** e2e module is the third accepted shape: it carries no
    ``pytest.mark.*`` at all, because pytest-bdd derives the markers from its
    feature file's Gherkin tags. Source text alone cannot see those, so the tags
    are resolved through the ``scenarios(...)`` call — which keeps the Gherkin the
    one place a tier is written, instead of a ``pytestmark`` copy free to drift.
    """
    source = test_file.read_text(encoding="utf-8")
    declared = set(_extract_pytestmarks(source)) | set(_bound_feature_tags(test_file))
    found = declared & _COST_SUB_MARKERS
    assert found, (
        f"{test_file.name} has no cost sub-marker. "
        f"Add at least one of {sorted(_COST_SUB_MARKERS)} to pytestmark, to "
        "individual test functions, or as a Gherkin tag on the feature it binds, "
        "so callers can filter by cost tier."
    )


def test_bdd_bound_tier_resolution_actually_reads_the_gherkin() -> None:
    """Prove the resolution above works, and is not passing for another reason.

    Without this, a BDD module that happened to mention ``pytest.mark.e2e_auth`` in
    a docstring would satisfy the check and nobody would learn the tag path is dead.
    """
    bdd = _E2E_TEST_DIR / "test_landing_state_diagnosis_bdd.py"
    assert bdd.is_file(), "the reference BDD-bound e2e module is missing"
    assert not set(_extract_pytestmarks(bdd.read_text(encoding="utf-8"))) & _COST_SUB_MARKERS, (
        "this module is supposed to declare NO Python-level cost marker — if it now "
        "does, it is no longer exercising the Gherkin-tag path"
    )
    assert set(_bound_feature_tags(bdd)) & _COST_SUB_MARKERS == {"e2e_auth"}


# ---------------------------------------------------------------------------
# Auto-marker hook self-tests
# ---------------------------------------------------------------------------


def _make_fake_item(path: pathlib.Path, existing_markers: list[str]) -> Any:
    """Build a minimal fake pytest.Item with ``.path`` and ``.iter_markers``."""
    item = MagicMock()
    item.path = path
    item.iter_markers.return_value = [types.SimpleNamespace(name=m) for m in existing_markers]
    item.add_marker = MagicMock()
    return item


def _run_hook(items: list[Any]) -> None:
    """Import and invoke the real ``pytest_collection_modifyitems`` hook."""
    import importlib.util

    root = pathlib.Path(__file__).parent.parent / "conftest.py"
    spec = importlib.util.spec_from_file_location("_root_conftest", root)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    mod.pytest_collection_modifyitems(items)


def test_hook_adds_e2e_marker_for_e2e_path() -> None:
    """A file under tests/e2e/ gets the e2e marker injected."""
    path = pathlib.Path("/repo/tests/e2e/test_example.py")
    item = _make_fake_item(path, existing_markers=[])
    _run_hook([item])
    calls = [str(c) for c in item.add_marker.call_args_list]
    assert any("e2e" in c for c in calls), (
        f"Expected e2e marker to be added for path {path}; add_marker calls: {calls}"
    )


def test_hook_adds_smoke_marker_for_smoke_path() -> None:
    """A file under tests/smoke/ gets the smoke marker injected."""
    path = pathlib.Path("/repo/tests/smoke/test_example.py")
    item = _make_fake_item(path, existing_markers=[])
    _run_hook([item])
    calls = [str(c) for c in item.add_marker.call_args_list]
    assert any("smoke" in c for c in calls), (
        f"Expected smoke marker to be added for path {path}; add_marker calls: {calls}"
    )


def test_hook_skips_e2e_if_already_marked() -> None:
    """The hook does not double-add e2e when the item already carries it."""
    path = pathlib.Path("/repo/tests/e2e/test_example.py")
    item = _make_fake_item(path, existing_markers=["e2e"])
    _run_hook([item])
    item.add_marker.assert_not_called()


def test_hook_skips_smoke_if_already_marked() -> None:
    """The hook does not double-add smoke when the item already carries it."""
    path = pathlib.Path("/repo/tests/smoke/test_example.py")
    item = _make_fake_item(path, existing_markers=["smoke"])
    _run_hook([item])
    item.add_marker.assert_not_called()


def test_hook_ignores_unrelated_paths() -> None:
    """Items outside tests/e2e/ and tests/smoke/ receive no injected markers."""
    path = pathlib.Path("/repo/tests/unit/test_example.py")
    item = _make_fake_item(path, existing_markers=[])
    _run_hook([item])
    item.add_marker.assert_not_called()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-path variant")
def test_hook_works_with_windows_paths() -> None:
    """On Windows the hook must still work with backslash-separated paths."""
    path = pathlib.PureWindowsPath("C:\\repo\\tests\\e2e\\test_example.py")
    # Simulate pathlib.Path on Windows by patching .parts to return the Windows parts
    item = _make_fake_item(path, existing_markers=[])  # type: ignore[arg-type]
    _run_hook([item])
    calls = [str(c) for c in item.add_marker.call_args_list]
    assert any("e2e" in c for c in calls), (
        f"Hook must add e2e marker for Windows path {path}; calls: {calls}"
    )
