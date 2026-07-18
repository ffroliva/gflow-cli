"""#341 funnel wiring: generation failures persist FAILED operation rows.

These tests drive the real CLI orchestrators (`video t2v`, `image t2i`) with a
FlowApiClient whose generate call raises, and assert against the REAL isolated
catalog DB (the autouse settings fixture points GFLOW_CLI_DB_PATH at a tmp dir)
that a terminal FAILED row landed with the problem_type-derived error_type —
then that the original exit code still surfaced (record-then-re-raise).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from gflow_cli.cli_image import image
from gflow_cli.cli_video import video
from gflow_cli.errors import WafRejectionError


def _failed_ops() -> list[tuple[str, str, str | None, str | None]]:
    db = Path(os.environ["GFLOW_CLI_DB_PATH"])
    if not db.exists():
        return []
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT command, status, error_type, error_detail FROM operations WHERE status='failed'"
        ).fetchall()


def test_video_t2v_failure_persists_failed_operation(tmp_path: Path) -> None:
    from gflow_cli.api.video import VideoStarted

    async def fake_generate_video(
        *,
        req: Any,
        out_dir: Any,
        project_id: Any = None,
        download: Any = None,
        on_started: Any = None,
    ) -> Any:
        if on_started is not None:
            on_started(VideoStarted(media_id="m1", project_id="p1", flow_operation_id="o1"))
        raise WafRejectionError("blocked mid-poll", status=403)

    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.api.client.FlowApiClient.__aenter__", new_callable=AsyncMock) as enter,
        # return_value=False: a truthy AsyncMock return would swallow the
        # in-context exception and defeat the funnel under test.
        patch(
            "gflow_cli.api.client.FlowApiClient.__aexit__",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        from gflow_cli.api.client import FlowApiClient

        fake_client = MagicMock(spec=FlowApiClient)
        fake_client.generate_video = fake_generate_video
        enter.return_value = fake_client

        result = CliRunner().invoke(video, ["t2v", "a slow pan"])

    assert result.exit_code == 10, result.output  # WafRejectionError exit code preserved
    rows = _failed_ops()
    assert len(rows) == 1
    command, status, error_type, error_detail = rows[0]
    assert command == "video t2v"
    assert error_type == "waf-rejection"
    assert error_detail == "blocked mid-poll"
    # The on_started STARTED row was UPDATED in place, not duplicated.
    db = Path(os.environ["GFLOW_CLI_DB_PATH"])
    with sqlite3.connect(db) as conn:
        (total,) = conn.execute("SELECT COUNT(*) FROM operations").fetchone()
    assert total == 1


def test_image_t2i_failure_persists_failed_operation(tmp_path: Path) -> None:
    async def fake_generate_image(*, project_id: Any, req: Any) -> Any:
        raise WafRejectionError("blocked at submit", status=403)

    with (
        patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.api.client.FlowApiClient.__aenter__", new_callable=AsyncMock) as enter,
        patch(
            "gflow_cli.api.client.FlowApiClient.__aexit__",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        from gflow_cli.api.client import FlowApiClient
        from gflow_cli.api.dto import ProjectInfo

        fake_client = MagicMock(spec=FlowApiClient)
        fake_client.generate_image = fake_generate_image
        fake_client.create_project = AsyncMock(return_value=ProjectInfo(project_id="p1", title="t"))
        enter.return_value = fake_client

        result = CliRunner().invoke(image, ["t2i", "a red fox"])

    assert result.exit_code == 10, result.output
    rows = _failed_ops()
    assert len(rows) == 1
    command, status, error_type, _detail = rows[0]
    assert command == "image t2i"
    assert error_type == "waf-rejection"


def test_batch_prompt_failure_persists_failed_operation(tmp_path: Path) -> None:
    """#341: a failed multi-prompt/batch row records a FAILED operation when the
    recorder context is threaded through run_one_image_prompt."""
    import asyncio

    from gflow_cli.data.recorder import OperationRecorder
    from gflow_cli.data.repository import DataRepository
    from gflow_cli.data.store import DataStore
    from gflow_cli.image_batch import BatchPromptItem, run_one_image_prompt

    async def fake_generate_image(*, project_id: Any, req: Any) -> Any:
        raise WafRejectionError("blocked in batch", status=403)

    client = MagicMock()
    client.generate_image = fake_generate_image

    db_path = tmp_path / "gflow.db"
    with DataStore.open(db_path) as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        outcome = asyncio.run(
            run_one_image_prompt(
                client=client,
                project_id="p1",
                idx=0,
                item=BatchPromptItem(text="a red fox"),
                output_dir=tmp_path,
                recorder=recorder,
                profile_name="default",
                profile_dir=tmp_path / "p",
                command="image batch",
            )
        )
        assert outcome.status == "fail"
        row = store.conn.execute(
            "SELECT command, error_type FROM operations WHERE status='failed'"
        ).fetchone()
    assert row is not None
    assert row[0] == "image batch"
    assert row[1] == "waf-rejection"
