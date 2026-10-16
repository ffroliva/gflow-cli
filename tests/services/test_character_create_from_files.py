"""`character_create_from_files`: a character built from local files, recorded (#639, #737)."""

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
