"""A cancel while a direct submit or its UI fallback runs reports ``abandoned`` (exit 22).

Row 13: the retry loop writes ``submit_attempted`` through the #1198 gate immediately before
the billed POST (direct route) and before the UI submit (fallback), so ``gflow cancel``
landing after that point sees a submitted job: ``abandoned``, ``charged: maybe``, exit 22.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.api._flow_retry import run_with_retry
from gflow_cli.api.dto import GenerationCheckpoint
from gflow_cli.api.submit_policy import (
    BrowserVisibility,
    DirectAttemptEnded,
    NotSent,
    VisibilitySetting,
)
from gflow_cli.data.store import DataStore
from gflow_cli.errors import ServerCancelUnavailableError, TaskCancelledError
from gflow_cli.json_output import exit_code_for
from gflow_cli.worker.daemon import FlowWorker
from gflow_cli.worker.queue import QueueRepository, abort_task

_CLAIMANT = "worker:profile:1"


class _Host:
    visibility = BrowserVisibility.HEADED

    async def relaunch(self, visibility: BrowserVisibility) -> None:
        self.visibility = visibility


@pytest.fixture
def worker(tmp_path: Path) -> Iterator[tuple[FlowWorker, QueueRepository]]:
    db_path = tmp_path / "cancel.db"
    with DataStore.open(db_path) as store:
        store.conn.execute(
            "INSERT INTO profiles(name, profile_dir, first_seen_at) "
            "VALUES ('profile', 'C:/profiles/profile', '2026-11-28T00:00:00Z')"
        )
        repo = QueueRepository(store)
        repo.enqueue_task("job", "profile", "t2i", {"prompt": "p"})
        assert repo.claim_task("job", claimant=_CLAIMANT) is not None
        flow_worker = FlowWorker("profile", str(db_path))
        try:
            yield flow_worker, repo
        finally:
            flow_worker.close()


def _observer(flow_worker: FlowWorker) -> Callable[[GenerationCheckpoint], None]:
    def save_checkpoint(phase: str, *, may_have_spent: bool, **_: Any) -> None:
        flow_worker.repo.update_checkpoint(
            "job", claimant=_CLAIMANT, phase=phase, may_have_spent=may_have_spent
        )

    return flow_worker._checkpoint_observer(  # pyright: ignore[reportPrivateUsage]
        "job", claimant=_CLAIMANT, project_id=["proj"], save_checkpoint=save_checkpoint
    )


def _assert_abandoned_exit_22(repo: QueueRepository) -> None:
    outcome = abort_task(repo, "job")
    assert outcome is not None
    assert outcome.state == "abandoned"
    task = repo.get_task("job")
    assert task is not None and task.error is not None
    assert task.error["charged"] == "maybe"
    assert exit_code_for(ServerCancelUnavailableError(detail="submitted")) == 22


@pytest.mark.asyncio
async def test_cancel_during_direct_post_reports_abandoned_exit_22(
    worker: tuple[FlowWorker, QueueRepository],
) -> None:
    flow_worker, repo = worker

    async def direct(before: Callable[[], None] | None) -> str:
        assert before is not None
        before()  # immediately before the billed POST
        _assert_abandoned_exit_22(repo)  # the cancel lands while the POST is in flight
        return "image"

    async def ui() -> str:
        raise AssertionError("the UI path must not run")

    await run_with_retry(
        _Host(),
        direct=direct,
        ui=ui,
        setting=VisibilitySetting.AUTO,
        on_checkpoint=_observer(flow_worker),
    )


@pytest.mark.asyncio
async def test_cancel_during_ui_fallback_reports_abandoned_exit_22(
    worker: tuple[FlowWorker, QueueRepository],
) -> None:
    flow_worker, repo = worker

    async def direct(before: Callable[[], None] | None) -> str:
        raise DirectAttemptEnded(NotSent("unsupported_shape"))

    async def ui() -> str:
        _assert_abandoned_exit_22(repo)  # the cancel lands while the UI submit runs
        return "ui-image"

    assert (
        await run_with_retry(
            _Host(),
            direct=direct,
            ui=ui,
            setting=VisibilitySetting.AUTO,
            on_checkpoint=_observer(flow_worker),
        )
        == "ui-image"
    )


@pytest.mark.asyncio
async def test_cancel_before_the_ui_fallback_stops_it_with_nothing_charged(
    worker: tuple[FlowWorker, QueueRepository],
) -> None:
    flow_worker, repo = worker
    outcome = abort_task(repo, "job")
    assert outcome is not None and outcome.state == "cancelled"
    ui_ran: list[bool] = []

    async def direct(before: Callable[[], None] | None) -> str:
        raise DirectAttemptEnded(NotSent("unsupported_shape"))

    async def ui() -> str:
        ui_ran.append(True)
        return "ui-image"

    with pytest.raises(TaskCancelledError):
        await run_with_retry(
            _Host(),
            direct=direct,
            ui=ui,
            setting=VisibilitySetting.AUTO,
            on_checkpoint=_observer(flow_worker),
        )
    assert ui_ran == []
