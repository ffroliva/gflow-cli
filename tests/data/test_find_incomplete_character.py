"""Real-SQLite tests for DataRepository.find_incomplete_character.

These tests exercise the actual SQL (json_extract, mode='character',
status='started') that mock-based saga tests cannot verify.
"""

from __future__ import annotations

from pathlib import Path

from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open(tmp_path: Path) -> tuple[DataStore, OperationRecorder, DataRepository]:
    store = DataStore.open(tmp_path / "gflow.db")
    repo = DataRepository(store)
    recorder = OperationRecorder(repo, prompt_mode="store")
    return store, recorder, repo


def _start(
    recorder: OperationRecorder,
    tmp_path: Path,
    *,
    project_id: str,
    entity_id: str,
    name: str,
) -> str:
    return recorder.record_character_started(
        profile_name="default",
        profile_dir=tmp_path / "profile_default",
        project_id=project_id,
        entity_id=entity_id,
        name=name,
    )


# ---------------------------------------------------------------------------
# Test: STARTED row is returned with correct entity_id and ids
# ---------------------------------------------------------------------------


def test_finds_started_row_returns_entity_id_and_empty_ids(tmp_path: Path) -> None:
    """A STARTED row for (project, name) is returned; entity_id matches flow_operation_id."""
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        recorder = OperationRecorder(repo, prompt_mode="store")

        _start(recorder, tmp_path, project_id="P1", entity_id="ent-001", name="Ana")

        result = repo.find_incomplete_character("P1", "Ana")
        assert result is not None
        assert result["entity_id"] == "ent-001"
        assert result["workflow_ids"] == []
        assert result["primary_media_ids"] == []
        assert result["row_id"]  # non-empty


def test_finds_started_row_returns_recorded_workflow_and_media_ids(tmp_path: Path) -> None:
    """After record_character_partial, workflow_ids and primary_media_ids survive the query."""
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        recorder = OperationRecorder(repo, prompt_mode="store")

        row_id = _start(recorder, tmp_path, project_id="P1", entity_id="ent-002", name="Ana")
        recorder.record_character_partial(
            row_id=row_id,
            workflow_ids=["wf-face", "wf-body"],
            primary_media_ids=["media-face"],
        )

        result = repo.find_incomplete_character("P1", "Ana")
        assert result is not None
        assert result["entity_id"] == "ent-002"
        assert result["workflow_ids"] == ["wf-face", "wf-body"]
        assert result["primary_media_ids"] == ["media-face"]


# ---------------------------------------------------------------------------
# Test: SUCCEEDED row is NOT returned (only incomplete/STARTED)
# ---------------------------------------------------------------------------


def test_succeeded_row_not_returned(tmp_path: Path) -> None:
    """A SUCCEEDED row for the same (project, name) must not come back.

    Recovery is only for STARTED rows.
    """
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        recorder = OperationRecorder(repo, prompt_mode="store")

        row_id = _start(recorder, tmp_path, project_id="P1", entity_id="ent-003", name="Ana")
        recorder.record_character_completed(
            row_id=row_id,
            workflow_ids=["wf-1"],
            primary_media_ids=["media-1"],
        )

        result = repo.find_incomplete_character("P1", "Ana")
        assert result is None, "SUCCEEDED row must not be returned"


# ---------------------------------------------------------------------------
# Test: STARTED for a different name is NOT returned
# ---------------------------------------------------------------------------


def test_different_name_not_returned(tmp_path: Path) -> None:
    """STARTED row for a different character name is not returned."""
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        recorder = OperationRecorder(repo, prompt_mode="store")

        _start(recorder, tmp_path, project_id="P1", entity_id="ent-004", name="Bob")

        result = repo.find_incomplete_character("P1", "Ana")
        assert result is None


# ---------------------------------------------------------------------------
# Test: STARTED for a different project is NOT returned
# ---------------------------------------------------------------------------


def test_different_project_not_returned(tmp_path: Path) -> None:
    """STARTED row for a different project_id is not returned."""
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        recorder = OperationRecorder(repo, prompt_mode="store")

        _start(recorder, tmp_path, project_id="OTHER", entity_id="ent-005", name="Ana")

        result = repo.find_incomplete_character("P1", "Ana")
        assert result is None


# ---------------------------------------------------------------------------
# Test: No matching op returns None
# ---------------------------------------------------------------------------


def test_no_matching_op_returns_none(tmp_path: Path) -> None:
    """Empty DB returns None (no crash, no stale data)."""
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)

        result = repo.find_incomplete_character("P1", "Ana")
        assert result is None


# ---------------------------------------------------------------------------
# Test: Two STARTED ops — most recent by started_at is returned
# ---------------------------------------------------------------------------


def test_most_recent_started_row_returned(tmp_path: Path) -> None:
    """When two STARTED rows exist for (project, name), the most recent is returned.

    The second row is inserted directly (bypassing upsert_project) because the
    (profile_name, flow_project_id) unique constraint prevents a second upsert
    for the same project — which is correct prod behaviour; we just need two
    STARTED operations rows for the ordering assertion.
    """
    import time

    from gflow_cli.data.models import OperationKind, OperationRecord, OperationStatus

    def _new_id() -> str:
        import uuid

        return str(uuid.uuid4())

    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        recorder = OperationRecorder(repo, prompt_mode="store")

        # First crash — uses the full recorder path (inserts profile + project)
        _start(recorder, tmp_path, project_id="P1", entity_id="ent-old", name="Ana")

        # Small sleep to ensure distinct started_at timestamps
        time.sleep(0.02)

        # Second crash — insert operation row directly (project already exists)
        op_id = _new_id()
        repo.insert_operation(
            OperationRecord(
                id=op_id,
                profile_name="default",
                flow_project_id="P1",
                command="character create",
                mode=OperationKind.CHARACTER,
                status=OperationStatus.STARTED,
                flow_operation_id="ent-new",
                flow_batch_id=None,
                prompt=None,
                prompt_hash=None,
                prompt_redacted=False,
                model=None,
                aspect_ratio=None,
                error_type=None,
                error_detail=None,
            )
        )
        repo.set_operation_metadata(op_id, {"entity_id": "ent-new", "name": "Ana"})

        result = repo.find_incomplete_character("P1", "Ana")
        assert result is not None
        assert result["entity_id"] == "ent-new", (
            "ORDER BY started_at DESC LIMIT 1 must return the most recent row"
        )
