"""`gflow cancel <id>`: local abort, honest that Flow cannot cancel what it has (#1198)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gflow_cli.cli import main
from gflow_cli.data.store import DataStore
from gflow_cli.errors import EXIT_CODE_MAP, ServerCancelUnavailableError
from gflow_cli.worker.queue import QueueRepository

_ABANDON_EXIT = EXIT_CODE_MAP[ServerCancelUnavailableError]


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "queue.db"
    with DataStore.open(path) as store:
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
        repo.claim_task("c", "worker")  # claimed, not submitted yet
        repo.claim_task("r", "worker")
        repo.update_checkpoint(
            "r", claimant="worker", phase="submit_attempted", may_have_spent=True
        )
        repo.update_task_status("d", "completed")
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(path))
    return path


def _status(db: Path, task_id: str) -> str:
    with DataStore.open(db) as store:
        task = QueueRepository(store).get_task(task_id)
    assert task is not None
    return task.status


def _run(*args: str) -> tuple[int, dict[str, object]]:
    result = CliRunner().invoke(main, ["cancel", *args, "--json"])
    return result.exit_code, json.loads(result.stdout)  # stderr carries the log events


def test_a_queued_job_is_cancelled(db: Path) -> None:
    code, doc = _run("q")

    assert code == 0
    assert doc["state"] == "cancelled" and doc["previous_state"] == "pending"
    assert doc["charged"] == "no"
    assert _status(db, "q") == "cancelled"


def test_a_claimed_job_that_has_not_submitted_is_cancelled(db: Path) -> None:
    code, doc = _run("c")

    assert code == 0
    assert (doc["state"], doc["previous_state"], doc["charged"]) == (
        "cancelled",
        "processing",
        "no",
    )


def test_a_submitted_job_is_abandoned_and_says_flow_keeps_going(db: Path) -> None:
    code, doc = _run("r")

    assert code == _ABANDON_EXIT
    assert doc["state"] == "abandoned"
    assert doc["cancel.server"] == "no"
    assert doc["charged"] == "maybe"
    assert doc["next_action"] == "reconcile"
    error = doc["error"]
    assert isinstance(error, dict)
    assert error["operation_id"] == "r"
    assert error["exit_code"] == _ABANDON_EXIT
    assert _status(db, "r") == "abandoned"


def test_a_submitted_job_in_plain_output_never_claims_flow_stopped(db: Path) -> None:
    result = CliRunner().invoke(main, ["cancel", "r"])

    assert result.exit_code == _ABANDON_EXIT
    text = " ".join(result.output.split())
    assert "may still be charged" in text
    assert "cancelled" not in text.lower()


def test_a_second_cancel_of_an_abandoned_job_still_says_it_may_be_charged(db: Path) -> None:
    """`abandoned` is not final: the generation may still be running (review of #1214)."""
    _run("r")

    code, doc = _run("r")

    assert code == _ABANDON_EXIT
    assert (doc["state"], doc["previous_state"]) == ("abandoned", "abandoned")
    assert (doc["charged"], doc["next_action"]) == ("maybe", "reconcile")


def test_a_finished_job_is_echoed_and_left_alone(db: Path) -> None:
    code, doc = _run("d")

    assert code == 0
    assert (doc["state"], doc["previous_state"]) == ("completed", "completed")
    assert _status(db, "d") == "completed"


def test_an_unknown_id_is_a_typed_not_found_error(db: Path) -> None:
    code, doc = _run("nope")

    assert code == 1
    assert doc["status"] == "fail"
    error = doc["error"]
    assert isinstance(error, dict)
    assert error["type"] == "https://gflow-cli.dev/errors/task-not-found"
    assert error["class"] == "TaskNotFoundError"
    assert (error["exit_code"], error["retryable"]) == (1, False)
    assert (error["charged"], error["next_action"]) == ("no", "fix_input")


def test_a_store_failure_is_a_json_error_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(tmp_path))  # a directory, not a database

    code, doc = _run("q")

    assert code == 16
    assert doc["status"] == "fail"
    error = doc["error"]
    assert isinstance(error, dict)
    assert error["exit_code"] == 16


def test_an_abandoned_job_is_not_described_as_no_longer_waited_for(db: Path) -> None:
    """A gflow worker in another process may still be polling it, to record the result for
    reconcile, so "gflow stopped waiting" would be false (CodeRabbit on #1214)."""
    plain = CliRunner().invoke(main, ["cancel", "r"]).stdout
    code, doc = _run("r")

    error = doc["error"]
    assert isinstance(error, dict)
    for text in (" ".join(plain.split()), str(error["remediation_hint"]), str(error["detail"])):
        assert "stopped waiting" not in text.lower()
    assert "record" in str(error["remediation_hint"])
