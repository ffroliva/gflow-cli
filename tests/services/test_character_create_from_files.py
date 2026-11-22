"""`character_create_from_files`: a character built from local files, recorded (#639)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from gflow_cli.api.transports.migrated_character import CreatedCharacter
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.services.character_create import character_create_from_files

_CREATED = CreatedCharacter(
    entity_id="entity-1", workflow_ids=("wf-0", "wf-1"), media_ids=("m-0", "m-1")
)


async def test_the_operation_is_recorded_with_which_character_it_made(tmp_path: Path) -> None:
    client = MagicMock()
    client.create_character_from_files = AsyncMock(return_value=_CREATED)
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")

        result = await character_create_from_files(
            client,
            recorder,
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            project_id="proj-1",
            name="Nora",
            portrait=tmp_path / "face.jpg",
            body=tmp_path / "body.jpg",
            voice="Aoede",
            personality="calm",
        )

        (status, raw) = store.conn.execute(
            "SELECT status, metadata_json FROM operations WHERE mode = 'character'"
        ).fetchone()
    assert (result.entity_id, result.workflow_ids, result.voice) == (
        "entity-1",
        ("wf-0", "wf-1"),
        "Aoede",
    )
    assert result.image_paths == ()
    meta = json.loads(raw)
    assert status == "succeeded"
    assert (meta["entity_id"], meta["name"], meta["voice"]) == ("entity-1", "Nora", "Aoede")
    assert meta["workflow_ids"] == ["wf-0", "wf-1"]


async def test_a_catalog_failure_after_the_create_still_returns_the_character(
    tmp_path: Path,
) -> None:
    """The character exists in Flow once the transport returns. A local write failing then
    must warn and continue (the exit-16 contract), never hide the new id and invite a
    duplicating re-run."""
    from gflow_cli.errors import DataStoreError

    client = MagicMock()
    client.create_character_from_files = AsyncMock(return_value=_CREATED)
    recorder = MagicMock()
    recorder.record_character_started.return_value = "row-1"
    recorder.record_character_completed.side_effect = DataStoreError(detail="database is locked")

    result = await character_create_from_files(
        client,
        recorder,
        profile_name="default",
        profile_dir=tmp_path / "profile_default",
        project_id="proj-1",
        name="Nora",
        portrait=tmp_path / "face.jpg",
    )

    assert result.entity_id == "entity-1"
    recorder.record_character_completed.assert_called_once()


async def test_a_row_the_catalog_could_not_complete_is_not_left_for_a_resume(
    tmp_path: Path,
) -> None:
    """A STARTED row would be resumed by the next prompt-generated create of the same name,
    which spends credits on an entity this run already finished. It is marked failed."""
    from gflow_cli.errors import DataStoreError

    client = MagicMock()
    client.create_character_from_files = AsyncMock(return_value=_CREATED)
    recorder = MagicMock()
    recorder.record_character_started.return_value = "row-1"
    failure = DataStoreError(detail="database is locked")
    recorder.record_character_completed.side_effect = failure

    await character_create_from_files(
        client,
        recorder,
        profile_name="default",
        profile_dir=tmp_path / "profile_default",
        project_id="proj-1",
        name="Nora",
        portrait=tmp_path / "face.jpg",
    )

    recorder.record_character_abandoned.assert_called_once_with(row_id="row-1", exc=failure)


async def test_a_catalog_that_cannot_start_the_row_leaves_nothing_to_mark(
    tmp_path: Path,
) -> None:
    from gflow_cli.errors import DataStoreError

    client = MagicMock()
    client.create_character_from_files = AsyncMock(return_value=_CREATED)
    recorder = MagicMock()
    recorder.record_character_started.side_effect = DataStoreError(detail="read-only")

    result = await character_create_from_files(
        client,
        recorder,
        profile_name="default",
        profile_dir=tmp_path / "profile_default",
        project_id="proj-1",
        name="Nora",
        portrait=tmp_path / "face.jpg",
    )

    assert result.entity_id == "entity-1"
    recorder.record_character_abandoned.assert_not_called()


async def test_a_file_built_row_left_started_is_never_resumed(tmp_path: Path) -> None:
    """Even if both the completion and the abandonment writes fail, the STARTED row of a
    file-built character is not offered to the prompt saga's resume, which would spend
    credits on a character this run already finished."""
    from gflow_cli.errors import DataStoreError

    client = MagicMock()
    client.create_character_from_files = AsyncMock(return_value=_CREATED)
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        recorder = OperationRecorder(repo, prompt_mode="store")
        failure = DataStoreError(detail="database is locked")
        recorder.record_character_completed = MagicMock(side_effect=failure)  # type: ignore[method-assign]
        recorder.record_character_abandoned = MagicMock(side_effect=failure)  # type: ignore[method-assign]

        await character_create_from_files(
            client,
            recorder,
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            project_id="proj-1",
            name="Nora",
            portrait=tmp_path / "face.jpg",
        )

        (status,) = store.conn.execute(
            "SELECT status FROM operations WHERE mode = 'character'"
        ).fetchone()
        assert status == "started"
        assert repo.find_incomplete_character("proj-1", "Nora") is None
