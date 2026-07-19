"""Atomic-claim tests for QueueRepository (Task C3, design spec §4).

A claim holds a ``BEGIN IMMEDIATE`` transaction that selects the oldest
pending row (or a requested task id), decodes+validates its payload via the
C2 codec, fails an invalid row WITHOUT launching a browser, and otherwise
transitions exactly that row ``pending`` -> ``processing`` with claimant
metadata. See ``tests/worker/test_queue_multiprocess.py`` for the two-process
contention proof.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from gflow_cli.data.store import DataStore
from gflow_cli.worker.queue import QueueRepository, make_checkpoint_document


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


def fail_if_called(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("browser client must not be constructed at claim time")


def test_claim_next_pending_changes_only_one_row(store: DataStore) -> None:
    repo = QueueRepository(store)
    repo.enqueue_task("a", "profile", "t2i", {"prompt": "a"})

    first = repo.claim_next_pending("profile", claimant="one")
    assert first is not None
    assert first.task_id == "a"
    assert first.status == "processing"

    assert repo.claim_next_pending("profile", claimant="two") is None


def test_claim_sets_claimant_metadata(store: DataStore) -> None:
    repo = QueueRepository(store)
    repo.enqueue_task("a", "profile", "t2i", {"prompt": "a"})

    claimed = repo.claim_next_pending("profile", claimant="worker-7")
    assert claimed is not None
    assert claimed.claimant == "worker-7"
    assert claimed.claimed_at is not None

    persisted = repo.get_task("a")
    assert persisted is not None
    assert persisted.status == "processing"
    assert persisted.claimant == "worker-7"


def test_claim_next_pending_empty_returns_none(store: DataStore) -> None:
    repo = QueueRepository(store)
    assert repo.claim_next_pending("profile", claimant="one") is None


def test_invalid_payload_fails_without_browser_launch(
    store: DataStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = QueueRepository(store)
    repo.enqueue_task("bad", "profile", "t2i", {"schema_version": 99})
    monkeypatch.setattr("gflow_cli.worker.daemon.FlowApiClient", fail_if_called)

    assert repo.claim_next_pending("profile", claimant="one") is None

    failed = repo.get_task("bad")
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error is not None
    # detail is redacted and never echoes the payload
    assert failed.error.get("type", "").endswith("queue-schema")


def test_unknown_task_type_fails_at_claim(store: DataStore) -> None:
    repo = QueueRepository(store)
    repo.enqueue_task("weird", "profile", "nonsense", {"prompt": "x"})

    assert repo.claim_next_pending("profile", claimant="one") is None
    failed = repo.get_task("weird")
    assert failed is not None
    assert failed.status == "failed"


def test_claim_task_by_id(store: DataStore) -> None:
    repo = QueueRepository(store)
    repo.enqueue_task("first", "profile", "t2i", {"prompt": "a"})
    repo.enqueue_task("second", "profile", "t2i", {"prompt": "b"})

    claimed = repo.claim_task("second", claimant="one")
    assert claimed is not None
    assert claimed.task_id == "second"
    assert claimed.status == "processing"

    # first is untouched, still claimable
    assert repo.get_task("first").status == "pending"  # type: ignore[union-attr]
    # re-claiming an already-claimed task yields None
    assert repo.claim_task("second", claimant="two") is None


def test_claim_task_missing_returns_none(store: DataStore) -> None:
    repo = QueueRepository(store)
    assert repo.claim_task("ghost", claimant="one") is None


def test_claim_task_invalid_payload_fails(store: DataStore) -> None:
    repo = QueueRepository(store)
    repo.enqueue_task("bad", "profile", "t2i", {"schema_version": 99})

    assert repo.claim_task("bad", claimant="one") is None
    failed = repo.get_task("bad")
    assert failed is not None
    assert failed.status == "failed"


def test_checkpoint_write_read_roundtrip(store: DataStore) -> None:
    repo = QueueRepository(store)
    repo.enqueue_task("a", "profile", "i2v", {"prompt": "a"})
    repo.claim_next_pending("profile", claimant="one")

    doc = make_checkpoint_document(
        claimant="one",
        phase="submit_attempted",
        may_have_spent=True,
        project_id="proj-xyz",
        operation_id="op-1",
        media_ids=("m1", "m2"),
        workflow_ids=("wf-1",),
    )
    repo.write_checkpoint("a", doc)

    read = repo.read_checkpoint("a")
    assert read is not None
    assert read["schema_version"] == 1
    assert read["claimant"] == "one"
    assert read["phase"] == "submit_attempted"
    assert read["may_have_spent"] is True
    assert read["project_id"] == "proj-xyz"
    assert read["operation_id"] == "op-1"
    assert read["media_ids"] == ["m1", "m2"]
    assert read["workflow_ids"] == ["wf-1"]
    # no secrets / prompt text ever in the checkpoint document
    keys = set(read)
    assert "prompt" not in keys
    assert not any("cookie" in k or "token" in k or "url" in k for k in keys)

    task = repo.get_task("a")
    assert task is not None
    assert task.checkpoint == read


def test_indeterminate_status_is_accepted(store: DataStore) -> None:
    repo = QueueRepository(store)
    repo.enqueue_task("a", "profile", "t2i", {"prompt": "a"})
    repo.claim_next_pending("profile", claimant="one")
    # the extended state machine allows processing -> indeterminate
    repo.update_task_status("a", status="indeterminate")
    task = repo.get_task("a")
    assert task is not None
    assert task.status == "indeterminate"
