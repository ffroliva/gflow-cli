"""Live E2E for `gflow data sync --names` (#543): restore, ghost-mark, idempotence.

Seeds an ISOLATED catalog DB with real media UUIDs from a live project listing
(names stripped, simulating pre-0.58.0 rows) plus one fabricated UUID that is
guaranteed absent from the listing, then drives `gflow data sync --names` via
subprocess and asserts:

1. Stripped rows get their display names restored from the listing, with
   ``sync.named_at`` + ``sync.source == "sync"`` provenance.
2. The fabricated row is ghost-marked ``sync.status == "missing_remote"``
   (never deleted).
3. A second run is a no-op: 0 names written, 0 ghosts marked, and the synced
   rows' ``metadata_json`` is byte-identical (no timestamp churn).

**Costs ZERO credits** — only the credit-free ``flow.projectInitialData``
listing endpoint is touched (once per phase, ~0.5s); no generation surface is
involved.

Opt in with BOTH env vars::

    GFLOW_CLI_E2E_PROFILE=<profile-name>       # logged-in Chromium profile
    GFLOW_CLI_E2E_SYNC_PROJECT=<project-id>    # a project OWNED by that
                                               # profile with at least one
                                               # NAMED generated asset

    uv run pytest -m e2e_auth -v tests/e2e/test_data_sync_names_e2e.py

The listing must also be complete (no pagination markers) — very large
projects are skipped because ghost-marking is by design refused on partial
listings.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from gflow_cli.data.store import DataStore
from gflow_cli.services.catalog_sync import parse_project_listing

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.e2e

_SYNC_PROJECT_ENV = "GFLOW_CLI_E2E_SYNC_PROJECT"


def _run_gflow(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run `gflow <args>` with JSON structlog on stderr so events are assertable.

    ``GFLOW_CLI_HOME`` is dropped so the child resolves the REAL platformdirs
    home where `gflow auth login` planted the live session; ``e2e_env``'s
    isolated ``GFLOW_CLI_DB_PATH`` is kept — the catalog under test is the one
    this test seeds itself.
    """
    child_env = {k: v for k, v in env.items() if k != "GFLOW_CLI_HOME"}
    return subprocess.run(
        [sys.executable, "-m", "gflow_cli", *args],
        capture_output=True,
        text=True,
        check=False,
        env={**child_env, "GFLOW_CLI_LOG_FORMAT": "json"},
    )


def _ts(dt: datetime) -> str:
    """Timestamp in the recorder's on-disk format (see data/recorder.py)."""
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _read_rows(db: Path, media_ids: list[str]) -> dict[str, str | None]:
    """Map flow_media_id -> raw metadata_json text for the given rows."""
    conn = sqlite3.connect(db)
    try:
        placeholders = ",".join("?" * len(media_ids))
        rows = conn.execute(
            f"SELECT flow_media_id, metadata_json FROM assets"
            f" WHERE flow_media_id IN ({placeholders})",  # noqa: S608 — placeholders only
            media_ids,
        ).fetchall()
    finally:
        conn.close()
    return {str(mid): meta for mid, meta in rows}


@pytest.mark.e2e_auth
def test_e2e_sync_restores_names_marks_ghosts_idempotent(
    e2e_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pathlib import Path as _Path

    project_id = os.environ.get(_SYNC_PROJECT_ENV, "").strip()
    if not project_id:
        pytest.skip(
            f"sync e2e requires {_SYNC_PROJECT_ENV} — set it to a Flow project id "
            "owned by the e2e profile that has at least one NAMED generated asset"
        )
    profile_name = e2e_env["GFLOW_CLI_PROFILE"]

    # ------------------------------------------------------------------
    # Phase 1 (seed): fetch the real listing in-process, then seed an
    # isolated catalog DB with name-stripped rows + one fabricated ghost.
    # ------------------------------------------------------------------
    # Undo the autouse `_isolate_settings` fixture so the profile dir resolves
    # to the real platformdirs path where the live session lives (same pattern
    # as the r2v e2e); teardown clears the settings cache again.
    from gflow_cli.auth import profile_dir as _resolve_profile_dir
    from gflow_cli.config import reset_settings

    monkeypatch.delenv("GFLOW_CLI_HOME", raising=False)
    reset_settings()
    prof_dir = _resolve_profile_dir(profile_name)
    if not prof_dir.exists():
        pytest.skip(f"profile dir not found: {prof_dir}")

    async def _fetch() -> dict[str, Any]:
        from gflow_cli.api.client import FlowApiClient

        client = FlowApiClient(profile_dir=prof_dir, headless=True)
        await client.__aenter__()
        try:
            return dict(await client.fetch_project_listing(project_id))
        finally:
            await client.__aexit__(None, None, None)

    parsed = parse_project_listing(asyncio.run(_fetch()))
    if not parsed.names:
        pytest.skip(f"project {project_id} has no named assets — nothing to restore")
    if not parsed.complete:
        pytest.skip(
            f"project {project_id} listing is paginated — ghost-marking is refused "
            "on partial listings by design; pick a smaller project"
        )

    named_pairs = dict(list(parsed.names.items())[:3])
    ghost_id = str(uuid.uuid4())
    assert ghost_id not in parsed.present  # uuid4 collision is astronomically unlikely

    db = _Path(e2e_env["GFLOW_CLI_DB_PATH"])
    now = _ts(datetime.now(UTC))
    with DataStore.open(db) as store:
        store.conn.execute(
            "INSERT INTO profiles(name, first_seen_at) VALUES (?, ?)",
            (profile_name, now),
        )
        for media_id in [*named_pairs, ghost_id]:
            # metadata_json NULL = name-stripped pre-0.58.0 row.
            store.conn.execute(
                "INSERT INTO assets(id, profile_name, flow_project_id, flow_media_id,"
                " kind, status, created_at, metadata_json)"
                " VALUES (?, ?, ?, ?, 'image', 'ready', ?, NULL)",
                (str(uuid.uuid4()), profile_name, project_id, media_id, now),
            )

    # ------------------------------------------------------------------
    # Phase 2 (sync run): names restored, ghost marked, summary on stdout.
    # ------------------------------------------------------------------
    args = ["data", "sync", "--names", "--project", project_id, "--max-projects", "1"]
    first = _run_gflow(args, e2e_env)
    # With a single project, partial failure (exit 34) is impossible: it
    # requires >=1 success AND >=1 failure. Anything non-zero is a real break.
    assert first.returncode == 0, (
        f"sync failed; stdout: {first.stdout[-400:]!r} stderr: {first.stderr[-600:]!r}"
    )
    assert "Sync:" in first.stdout, f"no summary line on stdout: {first.stdout!r}"

    all_ids = [*named_pairs, ghost_id]
    metas = _read_rows(db, all_ids)
    for media_id, expected_name in named_pairs.items():
        raw = metas.get(media_id)
        assert raw, f"row {media_id} has no metadata after sync"
        meta = json.loads(raw)
        assert meta.get("display_name") == expected_name, (
            f"name not restored for {media_id}: {meta}"
        )
        sync_meta = meta.get("sync") or {}
        assert sync_meta.get("named_at"), f"missing sync.named_at on {media_id}: {meta}"
        assert sync_meta.get("source") == "sync", f"wrong sync.source on {media_id}: {meta}"

    ghost_raw = metas.get(ghost_id)
    assert ghost_raw, "ghost row lost its metadata (or was deleted — it must never be)"
    ghost_meta = json.loads(ghost_raw)
    assert (ghost_meta.get("sync") or {}).get("status") == "missing_remote", (
        f"fabricated row was not ghost-marked: {ghost_meta}"
    )

    # ------------------------------------------------------------------
    # Phase 3 (idempotency): re-run is a no-op — every row is resolved or
    # tombstoned, so the work list is empty and no metadata churns.
    # ------------------------------------------------------------------
    second = _run_gflow(args, e2e_env)
    assert second.returncode == 0, f"idempotent re-run failed; stderr: {second.stderr[-600:]!r}"
    assert "0 name(s) written" in second.stdout, f"re-run rewrote names: {second.stdout!r}"
    assert "0 ghost(s) marked" in second.stdout, f"re-run re-marked ghosts: {second.stdout!r}"
    assert _read_rows(db, all_ids) == metas, (
        "metadata_json changed on an idempotent re-run (timestamp churn)"
    )
