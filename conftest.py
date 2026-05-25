"""Root-level pytest config.

Only purpose right now: ensure the parent directory of the ``--basetemp``
path exists before pytest's ``tmp_path_factory`` tries to create it. Without
this, a clean CI checkout (no ``tmp/`` directory present) fails every test
with ``FileNotFoundError: [Errno 2] No such file or directory:
'.../tmp/pytest'`` because ``Path.mkdir(parents=False)`` won't auto-create
the missing ``tmp`` parent.

Why root-level: pytest loads root ``conftest.py`` before scanning ``testpaths``
in ``pyproject.toml``, which is the window we need to create the parent dir.
The ``addopts = "--basetemp=tmp/pytest"`` setting itself lives in
``pyproject.toml`` so that ``pytest --help`` still surfaces the override.
"""

from __future__ import annotations

import pathlib


def pytest_configure(config) -> None:  # noqa: ANN001 — pytest config object
    basetemp = config.getoption("basetemp", default=None)
    if basetemp:
        parent = pathlib.Path(basetemp).expanduser().resolve().parent
        parent.mkdir(parents=True, exist_ok=True)
