"""Shared fixtures for the e2e test suite.

e2e tests hit the real Flow API / real Google auth endpoints and are opt-in:
run with ``-m e2e`` and ``GFLOW_CLI_E2E_PROFILE=<profile_name>`` set.

## Cost sub-markers

Every e2e test carries ``pytest.mark.e2e`` plus one or more cost sub-markers
so you can run only what you can afford:

    pytest -m "e2e_auth"                         # zero credits
    pytest -m "e2e and not e2e_batch and not e2e_video"  # cheap
    pytest -m "e2e"                              # full regression

| marker     | cost             | typical wallclock |
|------------|------------------|-------------------|
| e2e_auth   | 0 credits        | < 30 s            |
| e2e_image  | 1 Imagen credit  | 30–120 s          |
| e2e_batch  | N Imagen credits | 2–10 min          |
| e2e_video  | 1 Veo credit     | 1–10 min          |
| e2e_data   | same as above *  | +0 s              |

``e2e_data`` is always combined with ``e2e_image`` or ``e2e_video`` because
data-layer assertions depend on a real generation having run.

See docs/E2E_TESTING.md for the full layer reference.
"""

from __future__ import annotations

import os
import shutil
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from gflow_cli.config import get_settings, reset_settings

_E2E_PROFILE_ENV = "GFLOW_CLI_E2E_PROFILE"


@pytest.fixture
def e2e_profile_dir(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Resolve the authenticated Chromium profile from ``GFLOW_CLI_E2E_PROFILE``.

    Skips the test when the env var is unset or the profile directory is absent.
    Use this fixture in every e2e test that needs a live authenticated session
    instead of the inlined ``_profile_dir()`` helper.

    Note: This fixture temporarily unsets ``GFLOW_CLI_HOME`` to resolve the real
    user data directory, bypassing the isolation from ``_isolate_settings``.
    """
    name = os.environ.get(_E2E_PROFILE_ENV, "").strip()
    if not name:
        pytest.skip(
            f"E2E tests require {_E2E_PROFILE_ENV} — set it to a logged-in "
            "profile name and re-run with -m e2e"
        )

    from gflow_cli.config import Settings

    # Resolve real home by bypassing the isolation env var
    with monkeypatch.context() as m:
        m.delenv("GFLOW_CLI_HOME", raising=False)
        real_settings = Settings()
        candidate = real_settings.profile_subdir(name)

    if not candidate.exists():
        pytest.skip(
            f"Profile directory not found: {candidate}. "
            f"Run `gflow auth login --profile {name}` to create it."
        )

    monkeypatch.setenv("GFLOW_CLI_HOME", str(real_settings.home))
    reset_settings()
    try:
        yield candidate
    finally:
        reset_settings()


@pytest.fixture
def e2e_nosession_profile() -> Iterator[Path]:
    """Yield a fresh, empty profile dir INSIDE the gflow home.

    ``verify_flow_profile`` enforces a boundary check that the profile dir
    resolves inside ``GFLOW_CLI_HOME``, so a pytest ``tmp_path`` dir (system
    temp) cannot be used. The dir is UUID-named so it can never collide with a
    real ``profile_<name>`` dir, and is removed in teardown — ``ignore_errors=True``
    plus a short delay tolerate the Windows Chrome profile lock that may
    briefly outlive ``ctx.close()``.
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


@pytest.fixture
def e2e_env(tmp_path: Path) -> Iterator[dict[str, str]]:
    """Build an isolated subprocess environment: temp DB + temp output dir + active profile.

    Use this for tests that drive ``gflow`` via subprocess and need a clean
    SQLite database and output directory that are torn down with pytest's
    ``tmp_path`` after the test.

    Environment variables set:
      PYTHONUTF8          — ensure UTF-8 across platforms
      GFLOW_CLI_DB_PATH   — isolated SQLite database in tmp_path
      GFLOW_CLI_OUTPUT_DIR — isolated output directory in tmp_path
      GFLOW_CLI_PROFILE   — profile from GFLOW_CLI_E2E_PROFILE
      GFLOW_CLI_HISTORY_PROMPTS — "store" so full prompt is available for assertions
    """
    name = os.environ.get(_E2E_PROFILE_ENV, "").strip()
    if not name:
        pytest.skip(
            f"E2E subprocess tests require {_E2E_PROFILE_ENV} — "
            "set it to a logged-in Chromium profile name and re-run with -m e2e"
        )
    db = tmp_path / "gflow.db"
    out = tmp_path / "out"
    out.mkdir()
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["GFLOW_CLI_DB_PATH"] = str(db)
    env["GFLOW_CLI_OUTPUT_DIR"] = str(out)
    env["GFLOW_CLI_PROFILE"] = name
    env["GFLOW_CLI_HISTORY_PROMPTS"] = "store"
    yield env
