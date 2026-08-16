"""Shared fixtures/constants for the `gflow doctor` test suites (#542).

Home for what tests/services/test_doctor.py and tests/cli/test_cli_doctor.py
previously duplicated verbatim:

- ``CHECK_IDS`` — the frozen v1 check inventory; any rename/addition/removal
  must update this tuple AND the plan
  (docs/superpowers/plans/2026-08-16-doctor-and-catalog-sync).
- ``healthy_doctor_env`` — makes the env-shaped checks pass so only seeded DB
  defects can flag. Named to avoid semantic collision with the different
  ``clean_env`` fixture in tests/test_config.py. Registered for the whole
  suite via a re-export in tests/conftest.py (importing it directly into a
  test module would trip F811 wherever the fixture is a test parameter).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.profile_store import ProfileMeta

CHECK_IDS = (
    "catalog.display_name_missing",
    "catalog.local_file_missing",
    "catalog.sha256_null",
    "db.migration_drift",
    "db.wal_state",
    "operations.stuck_started",
    "queue.stuck_processing",
    "env.deprecated_vars",
    "env.browsers_missing",
    "auth.files_present",
)


@pytest.fixture
def healthy_doctor_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the env-shaped checks pass: browsers installed, auth present."""
    for var in ("GFLOW_CLI_PREFER_CLASSIC", "GFLOW_CLI_FORCE_AGENT_UI", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "gflow_cli.browser_manager.installed_chromium_version",
        lambda: "139.0.7258.5",
    )
    monkeypatch.setattr(
        "gflow_cli.profile_store.list_profiles",
        lambda: [
            ProfileMeta(
                name="default",
                profile_dir=Path("profile_default"),
                cookies_present=True,
                last_used_at=None,
                is_default=True,
            )
        ],
    )
