# SPDX-License-Identifier: MIT
"""A ``wait=false`` generation actually runs, and can be read back and cancelled (#1097).

Before the fix, ``wait=false`` saved the job and returned ``pending``. No shipped
command ever ran it, ``tasks/get`` said ``working`` forever, and ``tasks/cancel`` wrote
``failed`` without stopping anything. These tests drive the real tool,
``FlowWorker.process_task`` and its ``CancelledError`` handler. Only ``FlowApiClient``
is faked, so no browser starts.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, CancelTaskRequestParams, GetTaskRequestParams
from structlog.testing import capture_logs

from gflow_cli.data.store import DataStore
from gflow_cli.mcp import tasks_extension, tools
from gflow_cli.mcp.tasks_extension import TasksExtension
from gflow_cli.worker.queue import QueueRepository, make_checkpoint_document

from .test_tools_wired import _FakeFlowApiClient  # pyright: ignore[reportPrivateUsage]


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[DataStore]:
    store = DataStore.open(tmp_path / "gflow.db")
    store.conn.execute(
        "INSERT INTO profiles(name, profile_dir, first_seen_at) "
        "VALUES ('default', '/profiles/default', '2026-10-25T00:00:00Z')"
    )
    store.conn.commit()
    try:
        yield store
    finally:
        store.close()


@pytest.fixture()
def wired(db: DataStore, tmp_path: Path) -> Iterator[dict[str, Any]]:
    """Route the tool and the worker at ``db``; ``client`` is the fake Flow client."""
    client = _FakeFlowApiClient()
    with (
        patch("gflow_cli.mcp.tools._resolve_and_validate_profile", return_value="default"),
        patch("gflow_cli.worker.daemon.FlowApiClient", return_value=client),
        patch(
            "gflow_cli.mcp.tools.get_settings",
            return_value=MagicMock(
                resolved_db_path=lambda: db.path,
                profile_subdir=lambda _: tmp_path / "profile_default",
                timeout_seconds=30,
            ),
        ),
        patch(
            "gflow_cli.worker.daemon.get_settings",
            return_value=MagicMock(
                profile_subdir=lambda _: tmp_path / "profile_default",
                headless=True,
                transport=None,
                output_dir=tmp_path / "out",
            ),
        ),
    ):
        yield {"client": client}
    tasks_extension.DETACHED.clear()


def _status(db: DataStore, task_id: str) -> str:
    task = QueueRepository(db).get_task(task_id)
    assert task is not None
    return task.status


async def _finish(task_id: str) -> None:
    task = tasks_extension.DETACHED.get(task_id)
    if task is not None:
        await asyncio.wait({task}, timeout=5)


def _hang_after_submit(db: DataStore, client: Any) -> asyncio.Event:
    """Make the fake client record a submit, then never return; the event marks the submit."""
    submitted = asyncio.Event()

    async def hang(*_: Any, **__: Any) -> Any:
        (task_id,) = db.conn.execute(
            "SELECT task_id FROM generation_queue WHERE status = 'processing'"
        ).fetchone()
        QueueRepository(db).write_checkpoint(
            task_id,
            make_checkpoint_document(
                claimant="mcp:default",
                phase="submit_attempted",
                may_have_spent=True,
                project_id="proj-abc",
            ),
        )
        submitted.set()
        await asyncio.Event().wait()

    client.generate_images_batch = AsyncMock(side_effect=hang)
    client.generate_image = AsyncMock(side_effect=hang)
    return submitted


async def test_a_wait_false_job_runs_to_completion(db: DataStore, wired: dict[str, Any]) -> None:
    reply = await tools.gflow_generate_image(prompt="a lighthouse", wait=False)

    assert reply["status"] == "pending"
    await _finish(reply["task_id"])
    assert _status(db, reply["task_id"]) == "completed"
    assert reply["task_id"] not in tasks_extension.DETACHED


async def test_an_old_pending_row_is_never_claimed(db: DataStore, wired: dict[str, Any]) -> None:
    """Rows queued by the broken releases must not start billing after an upgrade."""
    QueueRepository(db).enqueue_task(
        task_id="old-row", profile_name="default", task_type="t2i", payload={"prompt": "old"}
    )

    reply = await tools.gflow_generate_image(prompt="new", wait=False)
    await _finish(reply["task_id"])

    assert _status(db, "old-row") == "pending"


@pytest.mark.parametrize("entry", ["run_stdio", "run_http", "run_sse"])
def test_no_entry_point_recovers_processing_rows(entry: str) -> None:
    """A second stdio server must not fail the first one's live job (predict STOP)."""
    from gflow_cli.mcp import server

    assert "recover_processing" not in inspect.getsource(getattr(server, entry))


async def test_a_detached_job_and_a_waiting_call_share_the_profile(
    db: DataStore, wired: dict[str, Any]
) -> None:
    active = peak = 0

    image = wired["client"].generate_image.return_value
    calls = 0

    async def held(*_: Any, **__: Any) -> Any:
        nonlocal active, peak, calls
        active += 1
        calls += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        # A media id seen twice trips the attribution guard, so each job gets its own.
        return replace(image, media_name=f"media-{calls}")

    async def held_batch(*a: Any, **k: Any) -> Any:
        return [await held(*a, **k)]

    wired["client"].generate_images_batch.side_effect = held_batch
    wired["client"].generate_image.side_effect = held

    queued = await tools.gflow_generate_image(prompt="a", wait=False)
    waited = await tools.gflow_generate_image(prompt="b")
    await _finish(queued["task_id"])

    assert peak == 1
    assert _status(db, queued["task_id"]) == "completed"
    assert waited["status"] == "completed", waited


async def test_a_detached_crash_is_logged_and_fails_the_row(
    db: DataStore, wired: dict[str, Any]
) -> None:
    with (
        patch("gflow_cli.mcp.tools.FlowWorker", side_effect=RuntimeError("boom")),
        capture_logs() as logs,
    ):
        reply = await tools.gflow_generate_image(prompt="x", wait=False)
        await _finish(reply["task_id"])

    assert _status(db, reply["task_id"]) == "failed"
    assert reply["task_id"] not in tasks_extension.DETACHED
    events = [e for e in logs if e["event"] == "mcp.task.detached_failed"]
    assert events and events[0]["error_class"] == "RuntimeError"
    assert "payload" not in events[0]


async def _get(db: DataStore, task_id: str) -> dict[str, Any]:
    return await TasksExtension(data_store=db)._handle_get_task(  # pyright: ignore[reportPrivateUsage]
        None, GetTaskRequestParams(task_id=task_id)
    )


async def _cancel(db: DataStore, task_id: str) -> dict[str, Any]:
    return await TasksExtension(data_store=db)._handle_cancel_task(  # pyright: ignore[reportPrivateUsage]
        None, CancelTaskRequestParams(task_id=task_id)
    )


async def test_cancel_after_submit_leaves_the_row_indeterminate(
    db: DataStore, wired: dict[str, Any]
) -> None:
    """A credit may already be spent, so the row must never read as a clean failure."""
    submitted = _hang_after_submit(db, wired["client"])
    reply = await tools.gflow_generate_image(prompt="x", wait=False)
    await asyncio.wait_for(submitted.wait(), timeout=5)

    assert await _cancel(db, reply["task_id"]) == {}

    # The cancel waited for the job's own handler: the very next read is settled.
    assert _status(db, reply["task_id"]) == "indeterminate"
    assert (await _get(db, reply["task_id"]))["status"] == "cancelled"
    assert reply["task_id"] not in tasks_extension.DETACHED


@pytest.mark.sharpit(allow_n_plus_one=True)  # polls a row until its state changes
async def test_cancel_of_a_row_nobody_runs_fails_only_a_pending_row(db: DataStore) -> None:
    repo = QueueRepository(db)
    repo.enqueue_task(task_id="p", profile_name="default", task_type="t2i", payload={})
    repo.enqueue_task(task_id="c", profile_name="default", task_type="t2i", payload={})
    repo.update_task_status("c", "completed")

    await _cancel(db, "p")
    await _cancel(db, "c")

    assert (_status(db, "p"), (await _get(db, "p"))["status"]) == ("failed", "cancelled")
    assert (_status(db, "c"), (await _get(db, "c"))["status"]) == ("completed", "completed")


async def test_tasks_get_carries_what_wait_true_returns(
    db: DataStore, wired: dict[str, Any]
) -> None:
    reply = await tools.gflow_generate_image(prompt="a lighthouse", wait=False)
    await _finish(reply["task_id"])

    got = await _get(db, reply["task_id"])

    assert got["status"] == "completed"
    assert got["result"]["isError"] is False
    payload = got["result"]["structuredContent"]
    assert payload["status"] == "completed"
    assert payload["task_id"] == reply["task_id"]
    assert payload["files"]


async def test_an_unfinished_task_carries_no_result(db: DataStore) -> None:
    QueueRepository(db).enqueue_task(
        task_id="busy", profile_name="default", task_type="t2i", payload={}
    )

    got = await _get(db, "busy")
    assert got["status"] == "working" and "result" not in got


async def test_draining_on_shutdown_classifies_a_running_job(
    db: DataStore, wired: dict[str, Any]
) -> None:
    """uvicorn re-raises the stop signal, so the HTTP lifespan must drain (spike 2026-10-25)."""
    submitted = _hang_after_submit(db, wired["client"])
    reply = await tools.gflow_generate_image(prompt="x", wait=False)
    await asyncio.wait_for(submitted.wait(), timeout=5)

    await tasks_extension.drain_detached()

    assert _status(db, reply["task_id"]) == "indeterminate"


async def test_the_http_app_drains_detached_tasks_on_shutdown() -> None:
    from gflow_cli.mcp.server import build_app

    app = build_app(transport="http")
    with patch("gflow_cli.mcp.tasks_extension.drain_detached", new=AsyncMock()) as drain:
        async with app.router.lifespan_context(app):
            pass

    drain.assert_awaited_once()


async def test_the_video_tool_runs_wait_false_through_the_same_path(
    db: DataStore, wired: dict[str, Any]
) -> None:
    reply = await tools.gflow_generate_video(prompt="a quiet street", wait=False)

    assert reply["status"] == "pending", reply
    await _finish(reply["task_id"])
    assert _status(db, reply["task_id"]) == "completed"


async def test_an_expired_session_fails_the_row_and_tasks_get_says_why(
    db: DataStore, wired: dict[str, Any]
) -> None:
    from gflow_cli.errors import AuthExpiredError

    wired["client"].generate_images_batch.side_effect = AuthExpiredError(detail="session gone")
    wired["client"].generate_image.side_effect = AuthExpiredError(detail="session gone")
    reply = await tools.gflow_generate_image(prompt="x", wait=False)
    await _finish(reply["task_id"])

    got = await _get(db, reply["task_id"])
    assert _status(db, reply["task_id"]) == "failed"
    assert got["status"] == "completed"  # a tool error, not a JSON-RPC one (SEP-2663)
    assert got["result"]["isError"] is True
    error = got["result"]["structuredContent"]["error"]
    assert error["type"].endswith("/auth-expired") and error["exit_code"] == 3, error


@pytest.mark.sharpit(allow_n_plus_one=True)  # polls a row until its state changes
async def test_cancel_of_a_finished_job_reports_it_as_finished(
    db: DataStore, wired: dict[str, Any]
) -> None:
    """A cancel arriving after the job is done must neither rewrite nor relabel it."""
    done = await tools.gflow_generate_image(prompt="a", wait=False)
    await _finish(done["task_id"])
    repo = QueueRepository(db)
    repo.enqueue_task(task_id="f", profile_name="default", task_type="t2i", payload={})
    repo.update_task_status("f", "failed")

    await _cancel(db, done["task_id"])
    await _cancel(db, "f")

    ok, failed = await _get(db, done["task_id"]), await _get(db, "f")
    assert (_status(db, done["task_id"]), ok["status"]) == ("completed", "completed")
    assert ok["result"]["isError"] is False
    assert (_status(db, "f"), failed["status"]) == ("failed", "completed")
    assert failed["result"]["isError"] is True


async def test_stdio_drains_even_when_the_server_run_raises() -> None:
    """At stdin EOF asyncio.run cancels every task, Playwright's connection included, so a
    job's browser then cannot close (40 s+ of timeouts, live 2026-10-25). Drain first, also
    when the transport dies with an error (a client that closes stdout first)."""
    from contextlib import asynccontextmanager

    from gflow_cli.mcp import server

    @asynccontextmanager
    async def fake_stdio(**_: Any) -> Any:
        yield (None, None)

    lowlevel = MagicMock(run=AsyncMock(side_effect=BrokenPipeError))
    with (
        patch("mcp.server.stdio.stdio_server", fake_stdio),
        patch.object(server, "sys", MagicMock()),
        patch.object(server, "io", MagicMock()),
        patch.object(server, "_apply_mcp_lease_wait_default"),
        patch.object(server, "_configure_utf8_pipes"),
        patch.object(server, "_redirect_stdout_to_stderr"),
        patch.object(server, "_register_surfaces"),
        patch.object(server.server, "_lowlevel_server", lowlevel, create=True),
        patch("gflow_cli.mcp.tasks_extension.drain_detached", new=AsyncMock()) as drain,
        pytest.raises(BrokenPipeError),
    ):
        await server.run_stdio()

    drain.assert_awaited_once()


async def test_a_job_cancelled_before_its_first_step_still_settles_its_row(
    db: DataStore, wired: dict[str, Any]
) -> None:
    """asyncio skips a coroutine's own handler when the task is cancelled before it starts."""
    reply = await tools.gflow_generate_image(prompt="x", wait=False)
    await tasks_extension.drain_detached()  # same tick as the detach: the job never ran

    assert _status(db, reply["task_id"]) == "failed"


@pytest.mark.sharpit(allow_n_plus_one=True)  # polls a row until its state changes
async def test_a_cancelled_job_stays_cancelled_in_tasks_get(
    db: DataStore, wired: dict[str, Any]
) -> None:
    submitted = _hang_after_submit(db, wired["client"])
    reply = await tools.gflow_generate_image(prompt="x", wait=False)
    await asyncio.wait_for(submitted.wait(), timeout=5)
    QueueRepository(db).enqueue_task(
        task_id="p", profile_name="default", task_type="t2i", payload={}
    )

    for task_id in (reply["task_id"], "p"):
        await _cancel(db, task_id)
        assert (await _get(db, task_id))["status"] == "cancelled", task_id
    assert "credit" in (await _get(db, reply["task_id"]))["statusMessage"]


@pytest.mark.parametrize("method", ["_handle_get_task", "_handle_cancel_task"])
async def test_an_unknown_task_id_is_invalid_params(db: DataStore, method: str) -> None:
    params = GetTaskRequestParams(task_id="nope")
    with pytest.raises(MCPError) as caught:
        await getattr(TasksExtension(data_store=db), method)(None, params)
    assert caught.value.code == INVALID_PARAMS
