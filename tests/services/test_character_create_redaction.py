"""End-to-end redaction hardening + observability tests for character_create saga.

Uses a real temp-DB ``OperationRecorder`` (no recorder mock) so that redaction
contracts are exercised through the full stack — saga → recorder → DB.
The FlowClient is mocked: ``generate_character_image`` returns only a safe
``(workflow_id, media_id)`` tuple.  No ``fifeUrl`` / signed URL ever travels
through the saga to the recorder, which is itself the Scenario-#16 guarantee.

Scenarios covered:
  #15  personality redaction end-to-end (redacted mode)
  #15b personality stored end-to-end (store mode, complementary)
  #16  signed URL never persisted — saga only records safe ids; no fifeUrl
       ever reaches record_character_completed (documented + asserted)
  obs  structlog events character.create_started + character.create_completed
       emitted with entity_id; personality and signed URLs never appear in any
       logged event.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from gflow_cli.api.character import CharacterImageRequest
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.services.character_create import character_create

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROFILE_NAME = "default"
PROJECT_ID = "proj-e2e-redact"
ENTITY_ID = "entity-e2e-001"
NAME = "E2E Character"
WF0 = "wf-e2e-face"
M0 = "media-e2e-face"

FACE_REQ = CharacterImageRequest(prompt="a test face", model="nano2")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(entity_id: str = ENTITY_ID, *, face_path: str | None = None) -> MagicMock:
    """Minimal async client mock; generate returns safe (wf_id, media_id, path) only.

    The 3rd element is a LOCAL file path (or None) — never a signed CDN URL.
    That's the Scenario-#16 guarantee at the client boundary: the signed fifeUrl
    is consumed inside the client (during download) and never surfaces here.
    """
    client = MagicMock()
    client.create_entity = AsyncMock(return_value=entity_id)
    client.generate_character_image = AsyncMock(return_value=(WF0, M0, face_path))
    client.commit_workflow = AsyncMock(return_value=None)
    client.patch_entity = AsyncMock(return_value=None)
    return client


def _saga_kwargs(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        profile_name=PROFILE_NAME,
        profile_dir=tmp_path / "profile_default",
        project_id=PROJECT_ID,
        name=NAME,
        face=FACE_REQ,
        body=None,
        voice=None,
        personality=None,
        locale="en-US",
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Scenario #15 — personality redaction end-to-end (redacted mode)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_personality_redacted_end_to_end(tmp_path: Path) -> None:
    """Full saga with real DB: personality plaintext must not appear anywhere in DB.

    Uses prompt_mode='redacted' so the recorder hashes+strips the personality.
    After the saga completes, the raw personality string must be absent from
    every column in the operations row.
    """
    secret = "SECRET_PII_12345"

    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="redacted")
        client = _make_client()

        await character_create(client, recorder, **_saga_kwargs(tmp_path, personality=secret))

        row = store.conn.execute(
            "SELECT prompt, prompt_hash, prompt_redacted, metadata_json"
            " FROM operations WHERE flow_operation_id = ?",
            (ENTITY_ID,),
        ).fetchone()
        assert row is not None, "operation row must exist after saga completes"

        # Plaintext must NOT be stored anywhere
        assert row["prompt"] is None, "personality plaintext must not be stored in redacted mode"
        meta_str = row["metadata_json"] or ""
        assert secret not in meta_str, f"'{secret}' must not appear anywhere in metadata_json"

        # Redaction markers must be set
        assert row["prompt_hash"], "hash must be set even in redacted mode"
        assert row["prompt_redacted"] == 1, "prompt_redacted flag must be 1"


# ---------------------------------------------------------------------------
# Scenario #15b — personality stored end-to-end (store mode, complementary)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_personality_stored_end_to_end(tmp_path: Path) -> None:
    """Full saga with real DB + store mode: personality IS persisted as plaintext."""
    text = "Friendly and curious"

    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        client = _make_client()

        await character_create(client, recorder, **_saga_kwargs(tmp_path, personality=text))

        row = store.conn.execute(
            "SELECT prompt, prompt_hash, prompt_redacted FROM operations"
            " WHERE flow_operation_id = ?",
            (ENTITY_ID,),
        ).fetchone()
        assert row is not None

        assert row["prompt"] == text, "plaintext personality must be stored in store mode"
        assert row["prompt_hash"], "hash must also be set in store mode"
        assert row["prompt_redacted"] == 0


# ---------------------------------------------------------------------------
# Scenario #16 — signed URL never persisted (saga records only safe ids)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signed_url_never_reaches_recorder(tmp_path: Path) -> None:
    """Scenario #16: generate_character_image returns only (wf_id, media_id).

    The signed fifeUrl (signature=.../Expires=...) lives in the raw gen
    response JSON but is deliberately NOT surfaced by the client method's
    return type.  Consequently, record_character_completed is called with
    NO media_metadata argument, and no signed URL can ever reach the DB.

    This test verifies:
    1. The DB contains no signed-URL markers after a full saga run.
    2. record_character_completed is invoked without media_metadata, confirming
       the safe-ids-only contract at the saga boundary.
    """
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        # Wrap recorder to spy on record_character_completed calls
        original_completed = recorder.record_character_completed
        completed_calls: list[dict[str, Any]] = []

        def _spy_completed(**kwargs: Any) -> None:
            completed_calls.append(kwargs)
            return original_completed(**kwargs)

        recorder.record_character_completed = _spy_completed  # type: ignore[method-assign]

        client = _make_client()
        await character_create(client, recorder, **_saga_kwargs(tmp_path))

        # 1. record_character_completed must have been called exactly once
        assert len(completed_calls) == 1, "record_character_completed must be called once"

        # 2. No media_metadata kwarg passed (safe-ids-only contract)
        call_kwargs = completed_calls[0]
        assert "media_metadata" not in call_kwargs or call_kwargs.get("media_metadata") is None, (
            "Saga must not pass media_metadata to record_character_completed; "
            "generate_character_image only returns (wf_id, media_id), never a fifeUrl"
        )

        # 3. DB row contains no signed-URL markers
        row = store.conn.execute(
            "SELECT metadata_json FROM operations WHERE flow_operation_id = ?",
            (ENTITY_ID,),
        ).fetchone()
        meta_str = (row["metadata_json"] or "") if row else ""
        assert "signature=" not in meta_str.lower(), "signature= must not appear in DB"
        assert "Expires=" not in meta_str, "Expires= must not appear in DB"
        assert "fifeUrl" not in meta_str, "fifeUrl must not appear in DB"

        # 4. Safe ids ARE recorded
        meta = json.loads(meta_str)
        assert WF0 in meta.get("workflow_ids", []), "workflow_id must be recorded"
        assert M0 in meta.get("primary_media_ids", []), "media_id must be recorded"


# ---------------------------------------------------------------------------
# Scenario #16 (download) — local image path persisted, signed URL never
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_downloaded_image_path_persisted_no_signed_url(tmp_path: Path) -> None:
    """The downloaded LOCAL image path is persisted as a local_files row; the
    signed CDN URL is never stored anywhere in the DB (scenario #16).

    The client returns only (wf_id, media_id, local_path) — a local path, not a
    signed URL — so the saga records the on-disk artifact while the DB stays free
    of any ``Signature=`` / ``Expires=`` / ``fifeUrl`` marker.
    """
    face_file = tmp_path / "characters" / "character_face_slot0.png"
    face_file.parent.mkdir(parents=True, exist_ok=True)
    face_file.write_bytes(b"\x89PNG\r\n\x1a\nFAKE")

    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        client = _make_client(face_path=str(face_file))

        await character_create(client, recorder, **_saga_kwargs(tmp_path))

        # A local_files row exists pointing at the downloaded on-disk image.
        lf_rows = store.conn.execute(
            "SELECT path FROM local_files WHERE path IS NOT NULL"
        ).fetchall()
        paths = [r["path"] for r in lf_rows]
        assert any(str(face_file.resolve()) == p for p in paths), (
            f"downloaded image path not persisted; got {paths}"
        )

        # No signed-URL markers anywhere in the DB (operations + assets).
        dump = ""
        for tbl in ("operations", "assets", "local_files"):
            for row in store.conn.execute(f"SELECT * FROM {tbl}").fetchall():
                dump += json.dumps([str(v) for v in tuple(row)])
        assert "signature=" not in dump.lower(), "signature= must not appear in DB"
        assert "Expires=" not in dump, "Expires= must not appear in DB"
        assert "fifeUrl" not in dump, "fifeUrl must not appear in DB"


# ---------------------------------------------------------------------------
# Observability — character.create_started + character.create_completed events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observability_events_emitted(
    tmp_path: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """character.create_started and character.create_completed must be emitted.

    Both events must carry entity_id.  Neither must contain personality
    plaintext nor any signed-URL marker.
    """
    secret_personality = "SUPER_SECRET_PERSONALITY"

    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="redacted")
        client = _make_client()

        await character_create(
            client,
            recorder,
            **_saga_kwargs(tmp_path, personality=secret_personality),
        )

    events = install_log_capture.entries
    event_names = [e["event"] for e in events]

    # Both high-level observability events must be present
    assert "character.create_started" in event_names, (
        f"character.create_started not emitted; got: {event_names}"
    )
    assert "character.create_completed" in event_names, (
        f"character.create_completed not emitted; got: {event_names}"
    )

    # character.create_started must carry entity_id + project_id
    started = next(e for e in events if e["event"] == "character.create_started")
    assert started["entity_id"] == ENTITY_ID
    assert started["project_id"] == PROJECT_ID

    # character.create_completed must carry entity_id + workflow_count
    completed = next(e for e in events if e["event"] == "character.create_completed")
    assert completed["entity_id"] == ENTITY_ID
    assert completed["workflow_count"] == 1

    # No event must contain the personality plaintext or signed-URL markers
    all_events_str = json.dumps(events)
    assert secret_personality not in all_events_str, (
        "personality plaintext must not appear in any log event"
    )
    assert "signature=" not in all_events_str.lower(), (
        "signed-URL marker 'signature=' must not appear in any log event"
    )
    assert "fifeUrl" not in all_events_str, (
        "signed-URL marker 'fifeUrl' must not appear in any log event"
    )
