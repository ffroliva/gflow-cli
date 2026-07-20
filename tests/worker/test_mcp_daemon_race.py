"""MCP/daemon shared-claim race proof (Task C4, design spec §4).

Two production callers can reach the same queued task at once: the MCP
direct-execution path claims a specific id (``claim_task``) right after
enqueue, and the daemon poll loop claims the oldest pending row
(``claim_next_pending``). Both now route through the SAME atomic
``BEGIN IMMEDIATE`` claim, so exactly ONE wins the task — the other observes
the row already ``processing`` and gets ``None``. Both call the REAL
repository claim API against one shared SQLite file (separate connections,
raced via threads), mirroring the two real callers.

A second test locks the C4 caller contract: ``process_task`` no longer marks
a pending row ``processing`` itself (the claim owns that transition), so it
can never re-claim / double-execute an already-claimed task.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from gflow_cli.data.store import DataStore
from gflow_cli.worker.daemon import FlowWorker
from gflow_cli.worker.queue import QueueRepository


def _seed(tmp_path: Path) -> Path:
    db_path = tmp_path / "race.db"
    store = DataStore.open(db_path)
    store.conn.execute(
        "INSERT INTO profiles(name, profile_dir, first_seen_at) "
        "VALUES ('profile', 'C:/profiles/profile', '2026-07-19T00:00:00Z')"
    )
    QueueRepository(store).enqueue_task("shared-task", "profile", "t2i", {"prompt": "go"})
    store.close()  # release all locks before the race
    return db_path


@pytest.mark.asyncio
async def test_mcp_and_daemon_cannot_process_the_same_task(tmp_path: Path) -> None:
    db_path = _seed(tmp_path)

    def mcp_claim() -> object:
        store = DataStore.open(db_path)
        try:
            return QueueRepository(store).claim_task("shared-task", claimant="mcp")
        finally:
            store.close()

    def daemon_claim() -> object:
        store = DataStore.open(db_path)
        try:
            return QueueRepository(store).claim_next_pending("profile", claimant="daemon")
        finally:
            store.close()

    # Real concurrency: each claim runs on its own connection in its own thread.
    first, second = await asyncio.gather(
        asyncio.to_thread(mcp_claim), asyncio.to_thread(daemon_claim)
    )

    assert sorted([first is not None, second is not None]) == [False, True]

    # The winner's claim is durable and the row is claimed exactly once.
    store = DataStore.open(db_path)
    try:
        task = QueueRepository(store).get_task("shared-task")
        assert task is not None
        assert task.status == "processing"
        assert task.claimant in {"mcp", "daemon"}
    finally:
        store.close()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[DataStore]:
    db = DataStore.open(tmp_path / "c4.db")
    db.conn.execute(
        "INSERT INTO profiles(name, profile_dir, first_seen_at) "
        "VALUES ('default', 'C:/profiles/default', '2026-07-19T00:00:00Z')"
    )
    try:
        yield db
    finally:
        db.close()


@pytest.mark.asyncio
async def test_process_task_never_self_transitions_to_processing(store: DataStore) -> None:
    """C4 caller contract: the atomic claim owns the pending->processing
    transition. ``process_task`` must NEVER move a row to 'processing' itself
    (that non-atomic step is what let MCP and the daemon race the same row)."""
    repo = QueueRepository(store)
    task = repo.enqueue_task("t", "default", "t2i", {"prompt": "x", "count": 1})

    worker = FlowWorker("default", str(store.path))
    statuses: list[str] = []
    original = worker.repo.update_task_status

    def _spy(task_id: str, status: str, **kw: object) -> None:
        statuses.append(status)
        original(task_id, status, **kw)  # type: ignore[arg-type]

    worker.repo.update_task_status = _spy  # type: ignore[method-assign]

    # Fail fast (before any browser) so process_task takes its failure path;
    # we only care which statuses it wrote, not the outcome.
    with patch.object(FlowWorker, "_build_image_request", side_effect=RuntimeError("boom")):
        await worker.process_task(task)

    assert "processing" not in statuses, f"process_task self-claimed: {statuses}"
    worker.close()
