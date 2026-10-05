"""A busy catalog after a SUCCESSFUL generation is not a failed generation (#900).

Real ``OperationRecorder`` on a real SQLite file; a second connection takes the write
lock after Flow has (fakely) produced the clip, so recording it times out on
``busy_timeout``. Before #900 the raw ``sqlite3.OperationalError`` missed every
``except DataStoreError`` and the CLI reported -- and recorded -- the paid-for clip as
failed.
"""

from __future__ import annotations

import inspect
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner
from structlog.testing import capture_logs

from gflow_cli.cli_video import video


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    from gflow_cli.config import reset_settings

    path = tmp_path / "catalog.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(path))
    # The real timeout is 5 s; the shape of the failure does not depend on its length.
    monkeypatch.setattr("gflow_cli.data.store.BUSY_TIMEOUT_MS", 100)
    reset_settings()
    yield path
    reset_settings()


def test_a_locked_catalog_after_success_is_a_warning_not_a_failed_run(
    tmp_path: Path, db: Path
) -> None:
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.video import VideoResult, VideoStarted, VideoStatus

    saved = tmp_path / "clip.mp4"
    saved.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    result = VideoResult(
        status=VideoStatus(media_id="m900", status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
        local_path=saved,
        project_id="p1",
        flow_operation_id="o1",
    )
    blockers: list[sqlite3.Connection] = []

    async def generate_video(**kwargs: Any) -> VideoResult:
        started = kwargs["on_started"](
            VideoStarted(media_id="m900", project_id="p1", flow_operation_id="o1")
        )
        if inspect.isawaitable(started):
            await started
        # Another gflow process starts writing just as this clip comes back.
        blocker = sqlite3.connect(db, isolation_level=None)
        blocker.execute("BEGIN IMMEDIATE")
        blockers.append(blocker)
        return result

    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.api.client.FlowApiClient.__aenter__", new_callable=AsyncMock) as enter,
        patch("gflow_cli.api.client.FlowApiClient.__aexit__", new_callable=AsyncMock),
    ):
        client = MagicMock(spec=FlowApiClient)
        client.generate_video = generate_video
        enter.return_value = client
        with capture_logs() as logs:
            run = CliRunner().invoke(video, ["t2v", "x"])

    for blocker in blockers:
        blocker.execute("ROLLBACK")
        blocker.close()
    assert blockers, "the generation never ran"
    assert run.exit_code == 0, run.output
    warned = [e for e in logs if e["event"] == "data.persistence_failed_after_success"]
    assert len(warned) == 1, [e["event"] for e in logs]
    assert warned[0]["flow_media_id"] == "m900"
    assert warned[0]["error_class"] == "DataStoreError"
    assert "locked" in warned[0]["detail"]
    assert not [e for e in logs if e["event"] == "error_unhandled"]
    # The STARTED row written before the lock survives; nothing recorded the clip as failed.
    rows = sqlite3.connect(db).execute("SELECT status FROM operations").fetchall()
    assert rows == [("started",)], rows
