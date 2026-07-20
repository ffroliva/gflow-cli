"""Two-process contention proof for atomic claims (Task C3, design spec §4).

Two REAL subprocesses race to claim the same single pending row against the
same SQLite file. ``BEGIN IMMEDIATE`` + ``busy_timeout`` must serialize them
so exactly one wins the task and the other observes ``processing`` and gets
``None``. Windows note: connections are closed promptly in the child so file
locks release; timeouts are generous (WAL + 5s busy_timeout).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from gflow_cli.data.store import DataStore
from gflow_cli.worker.queue import QueueRepository

_CHILD = """\
import sys
from pathlib import Path
from gflow_cli.data.store import DataStore
from gflow_cli.worker.queue import QueueRepository

db_path, claimant = sys.argv[1], sys.argv[2]
store = DataStore.open(Path(db_path))
try:
    task = QueueRepository(store).claim_next_pending("profile", claimant=claimant)
    print(task.task_id if task is not None else "NONE")
finally:
    store.close()
"""


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "race.db"
    store = DataStore.open(db_path)
    store.conn.execute(
        "INSERT INTO profiles(name, profile_dir, first_seen_at) "
        "VALUES ('profile', 'C:/profiles/profile', '2026-07-19T00:00:00Z')"
    )
    QueueRepository(store).enqueue_task("race-task", "profile", "t2i", {"prompt": "go"})
    store.close()  # release all locks before the race
    return db_path


def test_two_processes_exactly_one_wins(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    child = tmp_path / "claim_child.py"
    child.write_text(_CHILD, encoding="utf-8")

    procs = [
        subprocess.Popen(
            [sys.executable, str(child), str(db_path), f"worker-{i}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for i in range(2)
    ]
    outputs: list[str] = []
    for p in procs:
        out, err = p.communicate(timeout=60)
        assert p.returncode == 0, f"child failed: {err}"
        outputs.append(out.strip())

    winners = [o for o in outputs if o == "race-task"]
    losers = [o for o in outputs if o == "NONE"]
    assert len(winners) == 1, f"expected exactly one winner, got {outputs}"
    assert len(losers) == 1, f"expected exactly one loser, got {outputs}"

    # DB reflects a single durable claim
    store = DataStore.open(db_path)
    try:
        task = QueueRepository(store).get_task("race-task")
        assert task is not None
        assert task.status == "processing"
        assert task.claimant in {"worker-0", "worker-1"}
    finally:
        store.close()
