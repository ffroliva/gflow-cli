"""Tests for OperationRecorder.record_character_started / record_character_completed.

Uses a real temp-DB (no mocks) to exercise the full persist-before-spend +
redaction contract described in Phase-2 Task 6 (issue #145).
"""

from __future__ import annotations

import json
from pathlib import Path

from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore

# ---------------------------------------------------------------------------
# record_character_started
# ---------------------------------------------------------------------------


def test_record_character_started_inserts_started_row(tmp_path: Path) -> None:
    """STARTED row is inserted before any credited operation."""
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")

        row_id = recorder.record_character_started(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            project_id="flow-project-char-1",
            entity_id="entity-abc-123",
            name="My Character",
        )

        assert row_id, "must return a non-empty row id"

        row = store.conn.execute(
            "SELECT status, mode, flow_operation_id, metadata_json FROM operations WHERE id = ?",
            (row_id,),
        ).fetchone()
        assert row is not None
        assert row["status"] == "started"
        assert row["mode"] == "character"
        # entity_id stored in flow_operation_id AND in metadata_json
        assert row["flow_operation_id"] == "entity-abc-123"
        meta = json.loads(row["metadata_json"])
        assert meta["entity_id"] == "entity-abc-123"
        assert meta["name"] == "My Character"

        # No asset links yet (no credited op)
        links = store.conn.execute(
            "SELECT * FROM operation_assets WHERE operation_id = ?",
            (row_id,),
        ).fetchall()
        assert links == [], "no asset links should exist at STARTED time"


def test_record_character_started_twice_same_project_succeeds(tmp_path: Path) -> None:
    """Two characters in the SAME already-recorded Flow project must both record.

    Live regression: the second call crashed with ``UNIQUE constraint failed:
    projects.profile_name, projects.flow_project_id`` because ``upsert_project``
    conflicted on ``id`` (a fresh random id) instead of the natural key. This
    test reproduces the exact field failure (2nd character, same project).
    """
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")

        row_id_1 = recorder.record_character_started(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            project_id="flow-project-shared",
            entity_id="entity-1",
            name="Character One",
        )
        # Second character, SAME project — must NOT raise DataIntegrityError.
        row_id_2 = recorder.record_character_started(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            project_id="flow-project-shared",
            entity_id="entity-2",
            name="Character Two",
        )

        assert row_id_1 and row_id_2
        assert row_id_1 != row_id_2

        # Two operation rows.
        op_count = store.conn.execute(
            "SELECT COUNT(*) AS n FROM operations WHERE flow_project_id = ?",
            ("flow-project-shared",),
        ).fetchone()["n"]
        assert op_count == 2

        # Exactly one project row for the shared natural key.
        proj_count = store.conn.execute(
            "SELECT COUNT(*) AS n FROM projects WHERE profile_name = ? AND flow_project_id = ?",
            ("default", "flow-project-shared"),
        ).fetchone()["n"]
        assert proj_count == 1


# ---------------------------------------------------------------------------
# record_character_completed
# ---------------------------------------------------------------------------


def test_record_character_completed_flips_to_succeeded(tmp_path: Path) -> None:
    """STARTED row is updated to SUCCEEDED with workflow + media ids."""
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")

        row_id = recorder.record_character_started(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            project_id="flow-project-char-1",
            entity_id="entity-abc-123",
            name="My Character",
        )

        recorder.record_character_completed(
            row_id=row_id,
            workflow_ids=["wf-1", "wf-2"],
            primary_media_ids=["media-1"],
        )

        row = store.conn.execute(
            "SELECT status, completed_at, metadata_json FROM operations WHERE id = ?",
            (row_id,),
        ).fetchone()
        assert row["status"] == "succeeded"
        assert row["completed_at"] is not None

        meta = json.loads(row["metadata_json"])
        assert meta["workflow_ids"] == ["wf-1", "wf-2"]
        assert meta["primary_media_ids"] == ["media-1"]


# ---------------------------------------------------------------------------
# Scenario #15 — redaction: personality in "redacted" mode
# ---------------------------------------------------------------------------


def test_personality_redacted_in_redacted_mode(tmp_path: Path) -> None:
    """In redacted mode, personality plaintext must NOT be stored anywhere; hash must be set."""
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="redacted")

        row_id = recorder.record_character_started(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            project_id="flow-project-char-2",
            entity_id="entity-pii-999",
            name="PII Character",
        )

        recorder.record_character_completed(
            row_id=row_id,
            workflow_ids=["wf-x"],
            primary_media_ids=["media-x"],
            personality="Secret PII text",
        )

        row = store.conn.execute(
            "SELECT prompt, prompt_hash, prompt_redacted, metadata_json"
            " FROM operations WHERE id = ?",
            (row_id,),
        ).fetchone()

        # Plaintext must be absent
        assert row["prompt"] is None, "plaintext personality must not be stored in redacted mode"
        assert row["prompt_hash"], "hash must be set"
        assert row["prompt_redacted"] == 1, "prompt_redacted flag must be set"

        # Also check metadata doesn't leak the plaintext
        meta_str = row["metadata_json"] or ""
        assert "Secret PII text" not in meta_str


# ---------------------------------------------------------------------------
# Scenario #15b — store mode: personality IS stored
# ---------------------------------------------------------------------------


def test_personality_stored_in_store_mode(tmp_path: Path) -> None:
    """In store mode, personality plaintext is persisted."""
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")

        row_id = recorder.record_character_started(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            project_id="flow-project-char-3",
            entity_id="entity-store-1",
            name="Store Character",
        )

        recorder.record_character_completed(
            row_id=row_id,
            workflow_ids=["wf-store"],
            primary_media_ids=["media-store"],
            personality="Friendly helpful assistant",
        )

        row = store.conn.execute(
            "SELECT prompt, prompt_hash, prompt_redacted FROM operations WHERE id = ?",
            (row_id,),
        ).fetchone()

        assert row["prompt"] == "Friendly helpful assistant"
        assert row["prompt_hash"], "hash must also be set in store mode"
        assert row["prompt_redacted"] == 0


# ---------------------------------------------------------------------------
# Scenario #16 — signed-URL redaction in media_metadata
# ---------------------------------------------------------------------------


def test_signed_url_stripped_from_media_metadata(tmp_path: Path) -> None:
    """Signed URL params must never be stored in metadata (redact_metadata contract)."""
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")

        row_id = recorder.record_character_started(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            project_id="flow-project-char-4",
            entity_id="entity-url-1",
            name="URL Character",
        )

        recorder.record_character_completed(
            row_id=row_id,
            workflow_ids=["wf-url"],
            primary_media_ids=["media-url"],
            media_metadata={
                "fife_url": "https://x/i.png?signature=ABC&Expires=999",
                "display_name": "character.png",
            },
        )

        row = store.conn.execute(
            "SELECT metadata_json FROM operations WHERE id = ?",
            (row_id,),
        ).fetchone()
        meta_str = row["metadata_json"] or ""

        # None of these signed-URL markers must appear in stored metadata
        assert "signature=ABC" not in meta_str.lower()
        assert "Expires=999" not in meta_str
        assert "fifeUrl" not in meta_str
        # The safe field should still be present
        meta = json.loads(meta_str)
        assert meta.get("media_metadata", {}).get("display_name") == "character.png"


# ---------------------------------------------------------------------------
# Regression — duplicate flow_media_id across slots (agentic slot-add fallback)
# ---------------------------------------------------------------------------


def test_record_character_completed_duplicate_media_id_is_idempotent(tmp_path: Path) -> None:
    """Two slots sharing one flow_media_id must not crash the recorder.

    Under the agentic cohort the classic character-editor slot-add control is
    absent, so the body prompt is submitted to the still-active face slot and
    both slots report the SAME ``flow_media_id``.  ``_record_character_local_files``
    minted a fresh ``id`` per slot, so the second slot violated
    ``UNIQUE(profile_name, flow_media_id)`` -> ``DataIntegrityError``.  The recorder
    must reuse the existing asset id (mirroring ``record_completed_video``).
    See spike 2026-07-09.
    """
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")

        row_id = recorder.record_character_started(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            project_id="flow-project-dup",
            entity_id="entity-dup-1",
            name="Dup Character",
        )

        face = tmp_path / "face.png"
        face.write_bytes(b"\x89PNG\r\n\x1a\nface-bytes")
        body = tmp_path / "body.png"
        body.write_bytes(b"\x89PNG\r\n\x1a\nbody-bytes")

        # Both slots carry the SAME media id (the agentic fallback bug).
        recorder.record_character_completed(
            row_id=row_id,
            workflow_ids=["wf-dup", "wf-dup"],
            primary_media_ids=["dup-media", "dup-media"],
            image_paths=[str(face), str(body)],
        )

        # Must not raise; exactly ONE asset row for the duplicated media id.
        n = store.conn.execute(
            "SELECT COUNT(*) AS n FROM assets WHERE profile_name = ? AND flow_media_id = ?",
            ("default", "dup-media"),
        ).fetchone()["n"]
        assert n == 1, "duplicate flow_media_id must upsert to a single asset row"
