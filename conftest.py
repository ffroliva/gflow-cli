"""Root-level pytest configuration.

Responsibilities:
1. Ensure the ``--basetemp`` parent directory exists before pytest's
   ``tmp_path_factory`` tries to create it (avoids FileNotFoundError on
   clean CI checkouts with no ``tmp/`` directory present).
2. Auto-apply directory-based markers so tests in ``tests/e2e/`` and
   ``tests/smoke/`` are always tagged correctly even if a test author
   forgets the ``pytestmark`` declaration.

The ``addopts`` and ``markers`` settings live in ``pyproject.toml`` so that
``pytest --help`` surfaces them correctly. See docs/E2E_TESTING.md for the
full marker reference and cost-tier run commands.
"""

from __future__ import annotations

import pathlib

import pytest


def pytest_configure(config) -> None:  # noqa: ANN001 — pytest config object
    basetemp = config.getoption("basetemp", default=None)
    if basetemp:
        parent = pathlib.Path(basetemp).expanduser().resolve().parent
        parent.mkdir(parents=True, exist_ok=True)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-apply markers based on the test file's directory.

    Tests under ``tests/e2e/`` receive ``pytest.mark.e2e`` if they don't
    already carry it. Tests under ``tests/smoke/`` receive ``pytest.mark.smoke``.
    This is a safety net — explicit ``pytestmark`` declarations in each file
    are still preferred and will be the canonical record.
    """
    for item in items:
        path_str = str(item.fspath)
        marker_names = {m.name for m in item.iter_markers()}
        if "/tests/e2e/" in path_str and "e2e" not in marker_names:
            item.add_marker(pytest.mark.e2e)
        if "/tests/smoke/" in path_str and "smoke" not in marker_names:
            item.add_marker(pytest.mark.smoke)
