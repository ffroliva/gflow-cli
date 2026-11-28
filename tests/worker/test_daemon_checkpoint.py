"""Tests for worker daemon checkpoint tracking and recovery boundaries (Task 8 / #1545)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from gflow_cli.api.dto import GenerationCheckpoint
from gflow_cli.data.store import DataStore
from gflow_cli.worker.daemon import FlowWorker
from gflow_cli.worker.queue import QueueRepository


@pytest.fixture
def store(tmp_path: Path) -> Iterator[DataStore]:
    db = DataStore.open(tmp_path / "gflow_test.db")
    db.conn.execute(
        "INSERT INTO profiles(name, profile_dir, first_seen_at) "
        "VALUES ('profile', 'C:/profiles/profile', '2026-07-19T00:00:00Z')"
    )
    try:
        yield db
    finally:
        db.close()


def test_submit_refused_checkpoint_resets_to_claimed(store: DataStore, tmp_path: Path) -> None:
    repo = QueueRepository(store)
    task = repo.enqueue_task("task-refusal-1", "profile", "t2i", {"prompt": "test prompt"})
    claimed = repo.claim_task(task.task_id, claimant="worker:profile:123")
    assert claimed is not None

    worker = FlowWorker("profile", str(tmp_path / "gflow_test.db"))
    try:
        project_holder: list[str | None] = ["proj-123"]

        def save_checkpoint(
            phase: str,
            *,
            may_have_spent: bool,
            operation_id: str | None = None,
            media_ids: tuple[str, ...] = (),
            workflow_ids: tuple[str, ...] = (),
            catalog_operation_id: str | None = None,
        ) -> None:
            worker.repo.update_checkpoint(
                task.task_id,
                claimant="worker:profile:123",
                phase=phase,
                may_have_spent=may_have_spent,
                project_id=project_holder[0],
                operation_id=operation_id,
                media_ids=media_ids,
                workflow_ids=workflow_ids,
                catalog_operation_id=catalog_operation_id,
            )

        observer = worker._checkpoint_observer(
            task.task_id,
            claimant="worker:profile:123",
            project_id=project_holder,
            save_checkpoint=save_checkpoint,
        )

        # Initial submit attempt writes submit_attempted with may_have_spent=True
        observer(GenerationCheckpoint(phase="submit_attempted"))
        cp_after_submit = repo.read_checkpoint(task.task_id)
        assert cp_after_submit is not None
        assert cp_after_submit["phase"] == "submit_attempted"
        assert cp_after_submit["may_have_spent"] is True

        # When Flow refuses the submit, submit_refused resets checkpoint to claimed
        observer(GenerationCheckpoint(phase="submit_refused"))
        cp_after_refusal = repo.read_checkpoint(task.task_id)
        assert cp_after_refusal is not None
        assert cp_after_refusal["phase"] == "claimed"
        assert cp_after_refusal["may_have_spent"] is False
    finally:
        worker.close()
