"""#1035: a failed image download must not lose the generation.

Flow has generated the image and spent the quota; only the local copy failed. Each
surface must leave exactly ONE operation row (FAILED, with the project id) whose output
asset carries the media id, with no local-file row for a file that was never written.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from gflow_cli.api.dto import GeneratedImage, ProjectInfo
from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.config import get_settings
from gflow_cli.data.models import OperationStatus
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from tests.cli.test_cli_image import _make_generated_image, _make_i2i_client, _make_t2i_client

PROJECT = "11111111-1111-1111-1111-111111111111"


def _ops(store: DataStore) -> list[Any]:
    return store.conn.execute("SELECT id, status, flow_project_id, mode FROM operations").fetchall()


def _asset(store: DataStore, media_id: str) -> Any:
    return store.conn.execute(
        "SELECT a.id, a.status, a.flow_project_id, oa.operation_id, "
        "(SELECT COUNT(*) FROM local_files lf WHERE lf.asset_id = a.id) AS copies "
        "FROM assets a JOIN operation_assets oa ON oa.asset_id = a.id "
        "WHERE a.flow_media_id = ?",
        (media_id,),
    ).fetchone()


def _assert_one_failed_op_holding(store: DataStore, media_ids: list[str]) -> None:
    ops = _ops(store)
    assert len(ops) == 1, [tuple(o) for o in ops]
    assert ops[0]["status"] == OperationStatus.FAILED.value
    assert ops[0]["flow_project_id"] == PROJECT
    for media_id in media_ids:
        asset = _asset(store, media_id)
        assert asset is not None, f"{media_id} lost"
        assert asset["operation_id"] == ops[0]["id"]
        assert asset["status"] == "ready"
        assert asset["copies"] == 0


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


def _image(media: str) -> GeneratedImage:
    return GeneratedImage(
        media_name=media,
        workflow_id=f"wf-{media}",
        seed=1,
        prompt="p",
        model_name_type="NARWHAL",
        aspect_ratio="IMAGE_ASPECT_RATIO_SQUARE",
        fife_url=f"https://flow-content.google/{media}?Signature=abc",
        dimensions=(1024, 1024),
    )


def _record(store: DataStore, tmp_path: Path, images: list[GeneratedImage], paths: list[Path]):
    OperationRecorder(DataRepository(store), prompt_mode="store").record_generated_images(
        profile_name="default",
        profile_dir=tmp_path / "profile_default",
        project=ProjectInfo(project_id=PROJECT, title="t"),
        request=GenerateImageRequest(prompt="p", aspect=Aspect.SQUARE, model=Model.NARWHAL),
        images=images,
        saved_paths=paths,
        input_media_ids=[],
        operation_kind="t2i",
        status=OperationStatus.STARTED,
    )


def test_an_image_with_no_file_still_gets_its_asset_row(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        _record(store, tmp_path, [_image("m1")], [])
        ops = _ops(store)
        assert [o["status"] for o in ops] == ["started"]
        asset = _asset(store, "m1")
        assert asset is not None
        assert asset["copies"] == 0


def test_partial_paths_stay_aligned_with_their_images(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    first.write_bytes(b"\x89PNG")
    with DataStore.open(tmp_path / "gflow.db") as store:
        _record(store, tmp_path, [_image("m1"), _image("m2")], [first])
        assert _asset(store, "m1")["copies"] == 1
        assert _asset(store, "m2")["copies"] == 0
        path = store.conn.execute(
            "SELECT lf.path FROM local_files lf JOIN assets a ON a.id = lf.asset_id "
            "WHERE a.flow_media_id = 'm1'"
        ).fetchone()["path"]
        assert Path(path) == first.resolve()


# ---------------------------------------------------------------------------
# CLI t2i / i2i
# ---------------------------------------------------------------------------


def _failing_download(client: MagicMock) -> MagicMock:
    client.create_project = AsyncMock(return_value=ProjectInfo(project_id=PROJECT, title="t"))
    client.download_image = AsyncMock(side_effect=PermissionError(13, "Permission denied"))
    client.transport.last_image_route = "migrated"
    return client


def _invoke(args: list[str], client: MagicMock, tmp_path: Path) -> Any:
    from gflow_cli.cli import main

    with (
        patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
        patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
        patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
    ):
        return CliRunner().invoke(main, args)


def _store() -> DataStore:
    return DataStore.open(get_settings().resolved_db_path())


def _assert_recovery_hint(stderr: str, media_id: str) -> None:
    assert media_id in stderr
    assert f"https://flow.google.com/project/{PROJECT}" in stderr
    assert "Signature" not in stderr
    assert "flow-content.google" not in stderr
    assert "?" not in stderr


@pytest.mark.parametrize("as_json", [False, True])
def test_t2i_download_failure_keeps_the_generation(tmp_path: Path, as_json: bool) -> None:
    client = _failing_download(_make_t2i_client(images=[_make_generated_image(media_name="m1")]))
    args = ["image", "t2i", "a cat", "--out", str(tmp_path / "out")]
    result = _invoke([*args, "--json"] if as_json else args, client, tmp_path)

    assert result.exit_code == 1, result.output
    _assert_recovery_hint(result.stderr, "m1")
    if as_json:
        assert "m1" not in result.stdout
    with _store() as store:
        _assert_one_failed_op_holding(store, ["m1"])


def test_an_unwritable_out_exits_11_not_unexpected(tmp_path: Path) -> None:
    """#1091: the real storage error, through the CLI, is classified after the salvage."""
    from gflow_cli.storage import write_asset_async

    blocker = tmp_path / "not-a-dir"
    blocker.write_bytes(b"x")

    async def _download(_img: GeneratedImage, target: Path) -> Path:
        await write_asset_async(blocker / target.name, b"png")
        return target

    client = _failing_download(_make_t2i_client(images=[_make_generated_image(media_name="m4")]))
    client.download_image = AsyncMock(side_effect=_download)
    result = _invoke(["image", "t2i", "a cat", "--out", str(tmp_path / "out")], client, tmp_path)

    assert result.exit_code == 11, result.output
    assert "Unexpected error" not in result.output
    _assert_recovery_hint(result.stderr, "m4")


def test_i2i_download_failure_keeps_the_generation(tmp_path: Path) -> None:
    ref = "ddb6ef97-262d-49f4-8269-4a28c0fae6a2"
    client = _failing_download(_make_i2i_client(images=[_make_generated_image(media_name="m2")]))
    result = _invoke(
        ["image", "i2i", "make it blue", "--ref", ref, "--out", str(tmp_path / "out")],
        client,
        tmp_path,
    )

    assert result.exit_code == 1, result.output
    _assert_recovery_hint(result.stderr, "m2")

    with _store() as store:
        _assert_one_failed_op_holding(store, ["m2"])


def test_the_hint_links_the_labs_host_when_the_image_was_made_there(tmp_path: Path) -> None:
    client = _failing_download(_make_t2i_client(images=[_make_generated_image(media_name="m3")]))
    client.transport.last_image_route = "labs"
    result = _invoke(["image", "t2i", "a cat", "--out", str(tmp_path / "out")], client, tmp_path)

    assert result.exit_code == 1, result.output
    assert f"https://labs.google/fx/tools/flow/project/{PROJECT}" in result.stderr
    assert "flow.google.com" not in result.stderr


def test_an_attribution_failure_records_no_asset(tmp_path: Path) -> None:
    """The guard runs before the download: a collision must never write the id again."""
    with _store() as store:
        _record(store, tmp_path, [_image("m1")], [])
        before = store.conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    client = _failing_download(_make_t2i_client(images=[_make_generated_image(media_name="m1")]))
    result = _invoke(["image", "t2i", "a cat", "--out", str(tmp_path / "out")], client, tmp_path)

    assert result.exit_code == 26, result.output
    client.download_image.assert_not_called()
    assert "download failed" not in result.stderr
    with _store() as store:
        assert store.conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == before


# ---------------------------------------------------------------------------
# MCP worker
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db(tmp_path: Path) -> Iterator[DataStore]:
    store = DataStore.open(tmp_path / "worker.db")
    store.conn.execute(
        "INSERT INTO profiles(name, profile_dir, first_seen_at) "
        "VALUES ('default', 'C:/profiles/default', '2026-06-24T00:00:00Z')"
    )
    store.conn.commit()
    try:
        yield store
    finally:
        store.close()


def _worker_client(images: list[GeneratedImage], fail_from: int) -> MagicMock:
    """Downloads succeed for the first ``fail_from`` images, then raise."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.generate_image = AsyncMock(return_value=images[0])
    client.generate_images_batch = AsyncMock(return_value=images)
    calls: list[int] = []

    async def _download(image: GeneratedImage, target: Path) -> Path:
        calls.append(1)
        if len(calls) > fail_from:
            raise PermissionError(13, "Permission denied")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x89PNG")
        return target

    client.download_image = AsyncMock(side_effect=_download)
    return client


def test_worker_download_failure_keeps_every_image_at_its_position(
    temp_db: DataStore, tmp_path: Path
) -> None:
    from gflow_cli.worker.daemon import FlowWorker
    from gflow_cli.worker.queue import QueueRepository

    repo = QueueRepository(temp_db)
    task = repo.enqueue_task(
        task_id="task-1035",
        profile_name="default",
        task_type="t2i",
        payload={
            "prompt": "a cat",
            "count": 2,
            "project_id": PROJECT,
            "output_file": str(tmp_path / "out" / "cat.png"),
        },
    )
    worker = FlowWorker("default", str(temp_db.path))
    client = _worker_client([_image("w1"), _image("w2")], fail_from=1)
    with patch("gflow_cli.worker.daemon.FlowApiClient", return_value=client):
        asyncio.run(worker.process_task(task))
    worker.close()

    row = repo.get_task("task-1035")
    assert row is not None
    assert row.status == "failed"
    assert row.flow_media_id == "w1"
    ops = _ops(temp_db)
    assert len(ops) == 1
    assert ops[0]["status"] == "failed"
    assert ops[0]["flow_project_id"] == PROJECT
    assert _asset(temp_db, "w1")["copies"] == 1
    assert _asset(temp_db, "w2")["copies"] == 0
    path = temp_db.conn.execute(
        "SELECT lf.path FROM local_files lf JOIN assets a ON a.id = lf.asset_id "
        "WHERE a.flow_media_id = 'w1'"
    ).fetchone()["path"]
    assert Path(path).name == "cat_1.png"


def test_mcp_failed_reply_carries_the_media_and_project_id(
    temp_db: DataStore, tmp_path: Path
) -> None:
    from gflow_cli.mcp.tools import _TokenBucket, gflow_generate_image

    client = _worker_client([_image("w1")], fail_from=0)
    with (
        patch("gflow_cli.mcp.tools._rate_limiter", _TokenBucket(capacity=8, refill_rate=0.0)),
        patch("gflow_cli.mcp.tools._resolve_and_validate_profile", return_value="default"),
        patch("gflow_cli.worker.daemon.FlowApiClient", return_value=client),
        patch(
            "gflow_cli.mcp.tools.get_settings",
            return_value=MagicMock(
                resolved_db_path=lambda: temp_db.path,
                profile_subdir=lambda _: tmp_path / "profile_default",
                timeout_seconds=30,
            ),
        ),
    ):
        result = asyncio.run(gflow_generate_image(prompt="a cat", project=PROJECT))

    assert result["status"] == "failed", result
    assert result["flow_media_id"] == "w1"
    assert result["flow_project_id"] == PROJECT


# ---------------------------------------------------------------------------
# image batch / gflow run
# ---------------------------------------------------------------------------


def test_batch_download_failure_keeps_the_generation(tmp_path: Path) -> None:
    from gflow_cli.image_batch import BatchPromptItem, run_image_batch
    from tests.test_batch_outcomes import FakeClient

    client = FakeClient(fail_generate=set(), fail_download={"a"})
    with DataStore.open(tmp_path / "batch.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        outcomes = asyncio.run(
            run_image_batch(
                profile_dir=tmp_path,
                transport=None,
                prompts=(BatchPromptItem(text="a", index=0),),
                output_dir=tmp_path / "out",
                continue_on_error=True,
                project_title="t",
                client_factory=lambda **_: client,
                jitter_range=(0, 0),
                _profile_name="default",
                _recorder=recorder,
                _command="run",
            )
        )
        assert [o.status for o in outcomes] == ["fail"]
        _assert_one_failed_op_holding(store, ["media-a"])


def test_manifest_batch_download_failure_keeps_the_generation(tmp_path: Path) -> None:
    from gflow_cli.api.dto import BatchSubmissionResult
    from gflow_cli.image_batch import BatchPromptItem, _download_results
    from tests.test_batch_outcomes import FakeClient

    client = FakeClient(fail_generate=set(), fail_download={"a"})
    item = BatchPromptItem(text="a", index=0)
    result = BatchSubmissionResult(
        status="ok", project_id=PROJECT, prompt_idx=0, prompt_hash="h", images=(_image("media-a"),)
    )
    with DataStore.open(tmp_path / "manifest.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        with pytest.raises(ValueError, match="scripted download failure"):
            asyncio.run(
                _download_results(
                    client=client,
                    prompts=(item,),
                    results=[result],
                    output_dir=tmp_path / "out",
                    profile_name="default",
                    profile_dir=tmp_path,
                    recorder=recorder,
                )
            )
        _assert_one_failed_op_holding(store, ["media-a"])
