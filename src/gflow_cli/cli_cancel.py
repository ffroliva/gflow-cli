"""``gflow cancel <id>``: abort a queued job locally (#1198).

Two verbs on purpose: **abort** is local (gflow drops a queued job, or stops a claimed one
before it submits), **cancel** is server-side (Flow stops a generation it has). gflow can
only abort. A job a gflow worker already submitted may keep running in Flow and may still
be charged, so that case exits non-zero and says so. The MCP twin is ``tasks/cancel``;
both call :func:`gflow_cli.worker.queue.abort_task`.
"""

from __future__ import annotations

import asyncio
import sys

import click
from rich.console import Console
from rich.markup import escape

from gflow_cli import json_output
from gflow_cli._cli_helpers import run_with_handlers
from gflow_cli.config import get_settings
from gflow_cli.data.store import DataStore
from gflow_cli.errors import ServerCancelUnavailableError, TaskNotFoundError
from gflow_cli.worker.queue import AbortOutcome, QueueRepository, abort_task

_console = Console()


@click.command("cancel")
@click.argument("task_id")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON result.")
def cancel(task_id: str, as_json: bool) -> None:
    """Abort a queued job by id. Flow cannot cancel a generation once it has it.

    \b
    queued, or claimed but not submitted -> state cancelled, nothing charged, exit 0
    submitted by a gflow worker -> state abandoned: gflow stops waiting, but the
                 generation may keep running and may still be charged; exit 22
    finished  -> left as it is, state echoed, exit 0
    """

    async def _run() -> None:
        # The queue write blocks on SQLite's lock (BEGIN IMMEDIATE), so it runs off the loop.
        _report(task_id, await asyncio.to_thread(_abort, task_id), as_json)

    run_with_handlers(_run, cli_command="cancel", as_json=as_json)


def _abort(task_id: str) -> AbortOutcome | None:
    with DataStore.open(get_settings().resolved_db_path()) as store:
        return abort_task(QueueRepository(store), task_id)


def _report(task_id: str, outcome: AbortOutcome | None, as_json: bool) -> None:
    if outcome is None:
        raise TaskNotFoundError(detail=f"No queued job with id {task_id!r}.")
    if outcome.state == "abandoned":
        _abandoned(task_id, outcome.previous, as_json)
    else:
        _settled(task_id, outcome.state, outcome.previous, as_json)


def _abandoned(task_id: str, previous: str, as_json: bool) -> None:
    exc = ServerCancelUnavailableError(
        detail=f"A gflow worker had already submitted job {task_id} to Flow.",
        operation_id=task_id,
    )
    if as_json:
        json_output.emit(
            {
                **json_output.error_payload(exc),
                "task_id": task_id,
                "state": "abandoned",
                "previous_state": previous,
                "cancel.server": "no",
                "charged": exc.charged,
                "next_action": exc.resolved_next_action(),
            }
        )
    else:
        _console.print(f"[yellow]Stopped waiting for {escape(task_id)}.[/yellow]")
        _console.print(
            "A gflow worker had already submitted it, and Flow cannot be told to stop it: "
            "it may keep running and may still be charged. Check the Flow project.",
            markup=False,
        )
    sys.exit(json_output.exit_code_for(exc))


def _settled(task_id: str, state: str, previous: str, as_json: bool) -> None:
    """Cancelled now (nothing reached Flow), or already settled and left untouched."""
    cancelled_now = state == "cancelled" and previous != "cancelled"
    if as_json:
        doc = {"status": "ok", "task_id": task_id, "state": state, "previous_state": previous}
        if state == "cancelled":
            doc["charged"] = "no"
        json_output.emit(doc)
    elif cancelled_now:
        _console.print(
            f"Aborted {task_id} before it was submitted to Flow; nothing was charged.",
            markup=False,
        )
    else:
        _console.print(f"{task_id} is {state}. Nothing changed.", markup=False)
