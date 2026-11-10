# SPDX-License-Identifier: MIT
"""The real `gflow cancel` binary path against a seeded queue, one job per state (#1198).

Offline and $0, so it runs in CI: `cancel` reads and writes only the local queue table
under a temporary GFLOW_CLI_HOME, with no browser and no Flow call. The subprocess pass
proves the exit code per state, and that stdout carries exactly one JSON document (no
Rich or log leak).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from gflow_cli.data.store import DataStore
from gflow_cli.worker.queue import QueueRepository


@pytest.fixture
def home(tmp_path: Path) -> Path:
    with DataStore.open(tmp_path / "gflow.db") as store:
        store.conn.execute(
            "INSERT INTO profiles (name, first_seen_at) VALUES ('default', CURRENT_TIMESTAMP)"
        )
        repo = QueueRepository(store)
        for task_id in ("q", "c", "r", "d"):
            repo.enqueue_task(
                task_id=task_id,
                profile_name="default",
                task_type="t2i",
                payload={"prompt": "p", "aspect": "1:1", "count": 1},
            )
        repo.claim_task("c", "worker")
        repo.claim_task("r", "worker")
        repo.update_checkpoint(
            "r", claimant="worker", phase="submit_attempted", may_have_spent=True
        )
        repo.update_task_status("d", "completed")
    return tmp_path


def _cancel(home: Path, task_id: str) -> tuple[int, dict[str, object]]:
    env = {**os.environ, "GFLOW_CLI_HOME": str(home), "GFLOW_CLI_UPDATE_CHECK": "0"}
    env.pop("GFLOW_CLI_DB_PATH", None)
    proc = subprocess.run(
        [sys.executable, "-m", "gflow_cli", "cancel", task_id, "--json"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return proc.returncode, json.loads(proc.stdout)


def test_cancel_per_state_through_the_real_entrypoint(home: Path) -> None:
    code, queued = _cancel(home, "q")
    assert (code, queued["state"], queued["charged"]) == (0, "cancelled", "no")

    code, claimed = _cancel(home, "c")
    assert (code, claimed["state"], claimed["charged"]) == (0, "cancelled", "no")

    for _ in range(2):  # a second cancel of an abandoned job says the same
        code, running = _cancel(home, "r")
        assert code == 22
        assert running["state"] == "abandoned"
        assert running["cancel.server"] == "no"
        assert (running["charged"], running["next_action"]) == ("maybe", "reconcile")

    code, done = _cancel(home, "d")
    assert (code, done["state"]) == (0, "completed")

    code, missing = _cancel(home, "nope")
    assert code == 1
    assert missing["error"]["class"] == "TaskNotFoundError"  # type: ignore[index]

    with DataStore.open(home / "gflow.db") as store:
        repo = QueueRepository(store)
        states = {t: getattr(repo.get_task(t), "status", None) for t in ("q", "c", "r", "d")}
    assert states == {"q": "cancelled", "c": "cancelled", "r": "abandoned", "d": "completed"}
