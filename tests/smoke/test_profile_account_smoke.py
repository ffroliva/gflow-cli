"""Smoke test for issue-#92 — Google account persistence and profile naming.

REAL ENVIRONMENT REQUIRED
--------------------------
These tests require a profile that was authenticated with ``gflow auth login``
against real Google Flow. They **cannot** run in CI, a sandbox, or any
environment without a live Google session. The tests skip automatically when
``GFLOW_CLI_E2E_PROFILE`` is unset or the named profile directory does not exist.

To run on a developer workstation or server::

    GFLOW_CLI_E2E_PROFILE=<name> pytest -m smoke tests/smoke/test_profile_account_smoke.py -v

Credit cost: zero — no image or video generation is performed.

What is verified
----------------
  1. ``.gflow_account`` file in the profile dir holds a valid email address.
  2. ``profile_store.list_profiles()`` surfaces that email in ``google_account``.
  3. ``gflow auth list --json`` returns the ``google_account`` key per entry.

Backfill behaviour
------------------
If ``.gflow_account`` is absent (profile created before the PR #110 fix), the
first two tests derive the email from ``verify_flow_session`` and write the file,
emulating what a fresh ``gflow auth login`` would do. The third test
(``test_auth_list_json_includes_google_account``) skips if the file is still
absent after that, since the CLI subprocess cannot write to the profile during
``--json`` output.

See ``docs/E2E_TESTING.md`` § Smoke test inventory and
``docs/AUTHENTICATION.md`` § Profile naming for full context.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from gflow_cli import profile_store
from gflow_cli.auth.verification import FlowSessionOutcome, verify_flow_session
from gflow_cli.profile_store import ACCOUNT_FILE

pytestmark = pytest.mark.smoke

_E2E_PROFILE_ENV = "GFLOW_CLI_E2E_PROFILE"


@pytest.fixture
def smoke_profile(tmp_path: Path) -> tuple[str, Path]:
    """Resolve the authenticated profile from ``GFLOW_CLI_E2E_PROFILE``.

    Returns (profile_name, profile_dir). Skips when the env var is unset or
    the profile directory doesn't exist.
    """
    name = os.environ.get(_E2E_PROFILE_ENV, "").strip()
    if not name:
        pytest.skip(
            f"Smoke tests require {_E2E_PROFILE_ENV} — set it to a logged-in "
            "profile name and re-run with -m smoke"
        )
    from gflow_cli.auth import profile_dir as _resolve

    pdir = _resolve(name)
    if not pdir.exists():
        pytest.skip(
            f"Profile directory not found: {pdir}. Run `gflow auth login --profile {name}` first."
        )
    return name, pdir


async def test_account_file_present_and_readable(smoke_profile: tuple[str, Path]) -> None:
    """`.gflow_account` contains a non-empty email for a verified profile.

    If the file is absent (profile predates issue-#92 fix), it is derived from
    ``verify_flow_session`` and written — matching what a fresh login does.
    This covers both new and migrated profiles.
    """
    name, pdir = smoke_profile
    account_file = pdir / ACCOUNT_FILE

    if not account_file.exists():
        # Backfill for profiles created before the fix.
        status = await verify_flow_session(pdir, channel="chrome", source="smoke")
        assert status.outcome is FlowSessionOutcome.AUTHENTICATED, (
            f"Profile '{name}' is not AUTHENTICATED: {status.outcome}. "
            "Re-run `gflow auth login` to refresh the session."
        )
        assert status.user_email, "AUTHENTICATED status must carry a non-empty user_email."
        account_file.write_text(status.user_email, encoding="utf-8")

    email = account_file.read_text(encoding="utf-8").strip()
    assert email, f".gflow_account at {account_file} is empty — expected a valid email."
    assert "@" in email, f".gflow_account contains '{email}', which doesn't look like an email."


async def test_list_profiles_surfaces_google_account(smoke_profile: tuple[str, Path]) -> None:
    """``profile_store.list_profiles()`` returns the email in ``google_account``.

    Depends on `.gflow_account` being present; if not, backfills it first via
    ``verify_flow_session`` (same pattern as the account-file test above).
    """
    name, pdir = smoke_profile
    account_file = pdir / ACCOUNT_FILE

    if not account_file.exists():
        status = await verify_flow_session(pdir, channel="chrome", source="smoke")
        if status.outcome is not FlowSessionOutcome.AUTHENTICATED or not status.user_email:
            pytest.skip("Could not derive email from session — re-login required.")
        account_file.write_text(status.user_email, encoding="utf-8")

    profiles = {p.name: p for p in profile_store.list_profiles()}
    assert name in profiles, (
        f"Profile '{name}' not found in list_profiles(); available: {list(profiles)}"
    )
    meta = profiles[name]
    assert meta.google_account, (
        f"profile_store.list_profiles() returned google_account=None for '{name}'. "
        f"Expected the email from {account_file}."
    )
    assert "@" in meta.google_account, (
        f"google_account '{meta.google_account}' doesn't look like an email."
    )


def test_auth_list_json_includes_google_account(smoke_profile: tuple[str, Path]) -> None:
    """``gflow auth list --json`` emits a ``google_account`` field per profile.

    This is the user-visible surface: a downstream script consuming
    ``gflow auth list --json`` must see the account for each entry.
    """
    name, pdir = smoke_profile
    account_file = pdir / ACCOUNT_FILE
    if not account_file.exists():
        pytest.skip(
            f".gflow_account absent for '{name}' — run the account-file test first "
            "or perform a fresh `gflow auth login`."
        )

    result = subprocess.run(
        [sys.executable, "-m", "gflow_cli", "auth", "list", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"`gflow auth list --json` exited {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    entries: list[dict] = json.loads(result.stdout)
    entry = next((e for e in entries if e["name"] == name), None)
    assert entry is not None, (
        f"Profile '{name}' not found in `gflow auth list --json` output.\n"
        f"Entries: {[e['name'] for e in entries]}"
    )
    assert "google_account" in entry, (
        "'google_account' key missing from `gflow auth list --json` entry. "
        "Was the CLI rebuilt after the fix?"
    )
    assert entry["google_account"], (
        f"'google_account' is null/empty for '{name}' — expected a valid email. Full entry: {entry}"
    )
