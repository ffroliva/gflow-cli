"""Self-test for the autouse ``_isolate_settings`` fixture in ``conftest.py``.

Without this guard, a future refactor that drops ``reset_settings()``, renames
the env-var keys, or otherwise breaks the autouse fixture would silently let
the test suite resolve ``get_settings()`` against the developer's production
``platformdirs`` paths — re-opening issue #86 (data-layer test pollution).

If this test fails, the rest of the suite is leaking again. Fix the fixture
before touching anything else.
"""

from __future__ import annotations

import os
from pathlib import Path


def test_isolate_settings_redirects_db_path_under_tmp(tmp_path: Path) -> None:
    """``get_settings().resolved_db_path()`` must resolve under ``tmp_path``."""
    from gflow_cli.config import get_settings

    db_path = get_settings().resolved_db_path()
    assert tmp_path in db_path.parents, (
        f"Autouse _isolate_settings did not redirect db_path: "
        f"got {db_path}, expected under {tmp_path}"
    )


def test_isolate_settings_sets_home_env() -> None:
    """``GFLOW_CLI_HOME`` must be set to a per-test tmp dir."""
    home = os.environ.get("GFLOW_CLI_HOME")
    assert home is not None, "GFLOW_CLI_HOME not set by autouse fixture"
    assert "gflow_home" in home, f"Unexpected GFLOW_CLI_HOME value: {home}"


def test_isolate_settings_sets_db_path_env() -> None:
    """``GFLOW_CLI_DB_PATH`` must be set to a per-test tmp file."""
    db = os.environ.get("GFLOW_CLI_DB_PATH")
    assert db is not None, "GFLOW_CLI_DB_PATH not set by autouse fixture"
    assert db.endswith("test_gflow.db"), f"Unexpected GFLOW_CLI_DB_PATH value: {db}"
