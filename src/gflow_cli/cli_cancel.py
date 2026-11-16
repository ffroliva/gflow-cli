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
from gflow_cli._cli_helpers import (
    OutputFormat,
    output_format_option,
    run_with_handlers,
    spends_credits,
)
from gflow_cli.config import get_settings
from gflow_cli.data.store import DataStore
from gflow_cli.errors import ServerCancelUnavailableError, TaskNotFoundError
from gflow_cli.worker.queue import AbortOutcome, QueueRepository, abort_task

_console = Console()


@spends_credits(False)
@click.command("cancel")
@click.argument("task_id")
@output_format_option(help="Output format (json|table|id).")
def cancel(task_id: str, as_json: bool, output_format: str = "table") -> None:
    """Abort a queued job by id. Flow cannot cancel a generation once it has it.

    \b
    queued, or claimed but not submitted -> state cancelled, nothing charged, exit 0
    submitted by a gflow worker -> state abandoned: Flow cannot cancel it, so the
                 generation may keep running and may still be charged; exit 22
    finished  -> left as it is, state echoed, exit 0
    """
    is_json = as_json or output_format == OutputFormat.JSON.value

    async def _run() -> None:
        # The queue write blocks on SQLite's lock (BEGIN IMMEDIATE), so it runs off the loop.
        _report(task_id, await asyncio.to_thread(_abort, task_id), is_json, output_format)

    run_with_handlers(
        _run,
        cli_command="cancel",
        as_json=is_json,
        output_format=output_format,
    )


def _abort(task_id: str) -> AbortOutcome | None:
    with DataStore.open(get_settings().resolved_db_path()) as store:
        return abort_task(QueueRepository(store), task_id)


def _report(task_id: str, outcome: AbortOutcome | None, as_json: bool, output_format: str) -> None:
    if outcome is None:
        raise TaskNotFoundError(detail=f"No queued job with id {task_id!r}.")
    if outcome.state == "abandoned":
        _abandoned(task_id, outcome.previous, as_json, output_format)
    else:
        _settled(task_id, outcome.state, outcome.previous, as_json, output_format)


def _abandoned(
    task_id: str,
    previous: str,
    as_json: bool,
    output_format: str = OutputFormat.TABLE.value,
) -> None:
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
        c = Console(stderr=True) if output_format == OutputFormat.ID.value else _console
        c.print(f"[yellow]Marked {escape(task_id)} abandoned.[/yellow]")
        c.print(
            "A gflow worker had already submitted it, and Flow cannot be told to stop it: "
            "it may keep running and may still be charged. A worker still running it keeps "
            "polling so it can record the result; check the Flow project.",
            markup=False,
        )
    sys.exit(json_output.exit_code_for(exc))


def _settled(task_id: str, state: str, previous: str, as_json: bool, output_format: str) -> None:
    """Cancelled now (nothing reached Flow), or already settled and left untouched."""
    if output_format == OutputFormat.ID.value:
        click.echo(task_id)
        return
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
