"""``abort_task``: the one local-abort function behind ``gflow cancel`` and MCP ``tasks/cancel``.

Abort is local: gflow drops a queued job, or stops a claimed one before it submits. It never
cancels a generation Flow already has (``cancel.server`` is ``no``, #1198).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from gflow_cli.data.store import DataStore
from gflow_cli.worker.queue import QueueRepository, abort_task, mark_interrupted

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[QueueRepository]:
    with DataStore.open(tmp_path / "abort.db") as store:
        store.conn.execute(
            "INSERT INTO profiles (name, first_seen_at) VALUES ('default', CURRENT_TIMESTAMP)"
        )
        yield QueueRepository(store)


def _seed(repo: QueueRepository, task_id: str, status: str = "pending") -> None:
    repo.enqueue_task(
        task_id=task_id,
        profile_name="default",
        task_type="t2i",
        payload={"prompt": "p", "aspect": "1:1", "count": 1},
    )
    if status != "pending":
        repo.update_task_status(task_id, status)


def _submitted(repo: QueueRepository, task_id: str) -> None:
    """A worker claimed it and reached the credit gesture."""
    repo.claim_task(task_id, "worker")
    repo.update_checkpoint(
        task_id, claimant="worker", phase="submit_attempted", may_have_spent=True
    )


def _status(repo: QueueRepository, task_id: str) -> str | None:
    return getattr(repo.get_task(task_id), "status", None)


def test_a_queued_job_is_cancelled_and_never_claimed(repo: QueueRepository) -> None:
    _seed(repo, "q")

    outcome = abort_task(repo, "q")

    assert outcome is not None
    assert (outcome.previous, outcome.state) == ("pending", "cancelled")
    task = repo.get_task("q")
    assert task is not None and task.error is not None
    assert task.error["type"] == "https://gflow-cli.dev/errors/cancelled"
    assert (task.error["charged"], task.error["next_action"]) == ("no", "none")
    assert "status" not in task.error  # no invented HTTP status
    assert repo.claim_task("q", "worker") is None  # a worker can no longer pick it up


def test_a_claimed_job_that_has_not_submitted_is_cancelled_with_nothing_charged(
    repo: QueueRepository,
) -> None:
    _seed(repo, "c")
    repo.claim_task("c", "worker")
    repo.update_checkpoint("c", claimant="worker", phase="claimed", may_have_spent=False)

    outcome = abort_task(repo, "c")

    assert outcome is not None
    assert (outcome.previous, outcome.state) == ("processing", "cancelled")
    task = repo.get_task("c")
    assert task is not None and task.error is not None
    assert task.error["charged"] == "no"


def test_a_submitted_job_is_abandoned_and_may_still_be_charged(repo: QueueRepository) -> None:
    _seed(repo, "r")
    _submitted(repo, "r")

    outcome = abort_task(repo, "r")

    assert outcome is not None
    assert (outcome.previous, outcome.state) == ("processing", "abandoned")
    task = repo.get_task("r")
    assert task is not None and task.error is not None
    assert task.error["type"] == "https://gflow-cli.dev/errors/server-cancel-unavailable"
    assert (task.error["charged"], task.error["next_action"]) == ("maybe", "reconcile")
    assert task.error["retryable"] is False
    assert "status" not in task.error


@pytest.mark.parametrize("submit", [False, True])
def test_a_claim_racing_the_abort_is_not_missed(
    repo: QueueRepository, monkeypatch: pytest.MonkeyPatch, submit: bool
) -> None:
    """A worker (its own connection) claims the row just as the abort starts writing. The
    abort must see the claim, not leave the row ``processing`` behind a "nothing changed"
    report (review of #1214: a stale read, then a guarded update that matched nothing)."""
    _seed(repo, "x")
    worker = QueueRepository(DataStore.open(repo.store.path))
    real_transaction = DataStore.transaction
    fired: list[bool] = []

    def claim_first(self: DataStore, *, immediate: bool = False):  # type: ignore[no-untyped-def]
        if self is repo.store and not fired:
            fired.append(True)
            worker.claim_task("x", "worker")
            if submit:
                worker.update_checkpoint(
                    "x", claimant="worker", phase="submit_attempted", may_have_spent=True
                )
        return real_transaction(self, immediate=immediate)

    monkeypatch.setattr(DataStore, "transaction", claim_first)

    outcome = abort_task(repo, "x")
    worker.store.close()

    expected = "abandoned" if submit else "cancelled"
    assert fired
    assert outcome is not None and outcome.state == expected
    assert _status(repo, "x") == expected


@pytest.mark.parametrize("status", ["completed", "failed", "indeterminate"])
def test_a_finished_job_is_left_as_it_is(repo: QueueRepository, status: str) -> None:
    _seed(repo, "t", status)
    before = repo.get_task("t")

    outcome = abort_task(repo, "t")

    assert outcome is not None
    assert (outcome.previous, outcome.state) == (status, status)
    assert repo.get_task("t") == before  # no write at all, not even updated_at


@pytest.mark.parametrize("submit", [False, True])
def test_a_second_abort_changes_nothing(repo: QueueRepository, submit: bool) -> None:
    _seed(repo, "s")
    if submit:
        _submitted(repo, "s")
    first = abort_task(repo, "s")
    assert first is not None
    before = repo.get_task("s")

    again = abort_task(repo, "s")

    assert again is not None and (again.previous, again.state) == (first.state, first.state)
    assert repo.get_task("s") == before


def test_an_unknown_id_returns_none(repo: QueueRepository) -> None:
    assert abort_task(repo, "nope") is None


def test_a_worker_that_finishes_later_keeps_the_row_abandoned_but_records_its_media(
    repo: QueueRepository,
) -> None:
    """SEP-2663: a cancelled task stays cancelled. The worker's final write may not flip it,
    but the media id it produced still lands for reconcile (#1199)."""
    _seed(repo, "r")
    _submitted(repo, "r")
    abort_task(repo, "r")
    abandoned_error = getattr(repo.get_task("r"), "error", None)

    repo.update_task_status("r", "completed", flow_media_id="media-1")

    task = repo.get_task("r")
    assert task is not None
    assert (task.status, task.flow_media_id) == ("abandoned", "media-1")
    assert task.error == abandoned_error


def test_recovery_after_a_crash_leaves_an_aborted_row_as_it_is(repo: QueueRepository) -> None:
    for task_id, submit in (("c", False), ("a", True)):
        _seed(repo, task_id)
        if submit:
            _submitted(repo, task_id)
        else:
            repo.claim_task(task_id, "worker")
        abort_task(repo, task_id)

    for task_id in ("c", "a"):
        mark_interrupted(repo, task_id, repo.read_checkpoint(task_id), "daemon restart")

    assert (_status(repo, "c"), _status(repo, "a")) == ("cancelled", "abandoned")


def test_the_submit_gate_refuses_an_aborted_row_and_checkpoints_a_live_one(
    repo: QueueRepository,
) -> None:
    for task_id in ("live", "gone"):
        _seed(repo, task_id)
        repo.claim_task(task_id, "worker")
    abort_task(repo, "gone")

    kw = {"claimant": "worker", "phase": "submit_attempted", "may_have_spent": True}
    assert repo.checkpoint_before_submit("live", **kw) is True
    assert repo.checkpoint_before_submit("gone", **kw) is False

    assert (repo.read_checkpoint("live") or {}).get("phase") == "submit_attempted"
    assert repo.read_checkpoint("gone") is None  # nothing written past the abort
