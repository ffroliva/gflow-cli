"""Shared fixtures for the e2e test suite.

e2e tests hit the real Flow API / real Google auth endpoints and are opt-in:
run with `-m e2e` and `GFLOW_CLI_E2E_PROFILE=<profile_name>` set. See
docs/superpowers/specs/2026-05-17-e2e-test-coverage-design.md.
"""

from __future__ import annotations

import os
import shutil
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from gflow_cli.config import get_settings

_E2E_PROFILE_ENV = "GFLOW_CLI_E2E_PROFILE"


@pytest.fixture
def e2e_profile_dir() -> Path:
    """Resolve the authenticated Chromium profile from GFLOW_CLI_E2E_PROFILE.

    Skips the test when the env var is unset or the profile dir is absent.
    """
    name = os.environ.get(_E2E_PROFILE_ENV, "")
    if not name:
        pytest.skip(
            f"E2E tests require {_E2E_PROFILE_ENV} - set it to a logged-in "
            "profile name and re-run with -m e2e"
        )
    from gflow_cli.auth import profile_dir as _resolve_profile_dir

    candidate = _resolve_profile_dir(name)
    if not candidate.exists():
        pytest.skip(
            f"Profile directory not found: {candidate}. "
            f"Run `gflow auth login --profile {name}` to create it."
        )
    return candidate


@pytest.fixture
def e2e_nosession_profile() -> Iterator[Path]:
    """Yield a fresh, empty profile dir INSIDE the gflow home.

    `verify_flow_session` enforces a boundary check that the profile dir
    resolves inside GFLOW_CLI_HOME, so a pytest `tmp_path` dir (system temp)
    cannot be used. The dir is UUID-named so it can never collide with a real
    `profile_<name>` dir, and is removed in teardown - `ignore_errors=True`
    plus a short delay tolerate the Windows Chrome profile lock that may
    briefly outlive `ctx.close()`.
    """
    home = get_settings().home
    home.mkdir(parents=True, exist_ok=True)
    path = home / f"profile_e2e_nosession_{uuid.uuid4().hex}"
    assert not path.exists(), f"temp profile dir unexpectedly already exists: {path}"
    path.mkdir()
    try:
        yield path
    finally:
        time.sleep(0.5)  # let Chrome release the Windows profile lock
        shutil.rmtree(path, ignore_errors=True)
