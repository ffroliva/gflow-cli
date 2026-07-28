"""Tests for the character_create saga (services/character_create.py).

All tests use fully-mocked client + recorder — no DB, no live Flow calls.

Scenarios covered:
  happy face-only (#1):  create_entity → record_started → generate(0) →
                          commit → patch → record_completed
  face+body (#2):        two sequential generates, body carries face_media_id,
                          patch gets both workflow ids
  recovery entity (#3):  incomplete row found → create_entity NOT called;
                          both slots missing → both generates called
  recovery face (#4):    incomplete row with face already recorded → face
                          generate NOT called; body still runs (if body given)
  binding error (#5):    generate_character_image raises WireFormatError →
                          patch NOT called, exception re-raised, partial
                          state preserved via record_character_partial
  sequential (#22):      body generate starts only after face commit finishes
                          (call-order assertion, no asyncio.gather leakage)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from gflow_cli.api.character import CharacterCreateResult, CharacterImageRequest
from gflow_cli.errors import WireFormatError
from gflow_cli.services.character_create import character_create

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PROFILE_NAME = "default"
PROFILE_DIR = Path("/tmp/profile_default")
PROJECT_ID = "proj-123"
ENTITY_ID = "entity-abc"
NAME = "Alice"
WF0 = "wf-face-0"
M0 = "media-face-0"
WF1 = "wf-body-1"
M1 = "media-body-1"
ROW_ID = "row-001"

FACE_REQ = CharacterImageRequest(prompt="a fantasy face", model="nano2")
BODY_REQ = CharacterImageRequest(prompt="a fantasy body", model="nano2")


def _make_client(
    *,
    entity_id: str = ENTITY_ID,
    face_result: tuple[str, str, str | None] = (WF0, M0, "/tmp/face_slot0.png"),
    body_result: tuple[str, str, str | None] = (WF1, M1, "/tmp/body_slot1.png"),
    generate_side_effect: Exception | None = None,
) -> MagicMock:
    client = MagicMock()
    client.create_entity = AsyncMock(return_value=entity_id)
    if generate_side_effect is not None:
        client.generate_character_image = AsyncMock(side_effect=generate_side_effect)
    else:
        client.generate_character_image = AsyncMock(side_effect=[face_result, body_result])
    client.commit_workflow = AsyncMock(return_value=None)
    client.patch_entity = AsyncMock(return_value=None)
    return client


def _make_recorder(*, row_id: str = ROW_ID) -> MagicMock:
    recorder = MagicMock()
    recorder.record_character_started = MagicMock(return_value=row_id)
    recorder.record_character_partial = MagicMock(return_value=None)
    recorder.record_character_completed = MagicMock(return_value=None)
    # By default: no prior incomplete op
    recorder.repository = MagicMock()
    recorder.repository.find_incomplete_character = MagicMock(return_value=None)
    return recorder


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _saga_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        profile_name=PROFILE_NAME,
        profile_dir=PROFILE_DIR,
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
# Scenario #1: happy path — face-only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_face_only_call_order() -> None:
    """create_entity → record_started → generate(0) → commit → patch → record_completed."""
    client = _make_client()
    recorder = _make_recorder()

    result = await character_create(client, recorder, **_saga_kwargs())

    # Assert create_entity called once
    client.create_entity.assert_called_once_with(PROJECT_ID)

    # Assert record_started called BEFORE generate
    assert recorder.record_character_started.call_count == 1
    recorder.record_character_started.assert_called_once_with(
        profile_name=PROFILE_NAME,
        profile_dir=PROFILE_DIR,
        project_id=PROJECT_ID,
        entity_id=ENTITY_ID,
        name=NAME,
    )

    # Assert generate called once (face only)
    assert client.generate_character_image.call_count == 1
    client.generate_character_image.assert_called_once_with(
        project_id=PROJECT_ID,
        entity_id=ENTITY_ID,
        req=FACE_REQ,
        image_reference_index=0,
        locale="en-US",
        format_prompt=False,
    )

    # commit_workflow called once with face
    client.commit_workflow.assert_called_once_with(WF0, project_id=PROJECT_ID, primary_media_id=M0)

    # patch called once with face workflow only
    client.patch_entity.assert_called_once_with(
        project_id=PROJECT_ID,
        entity_id=ENTITY_ID,
        display_name=NAME,
        workflow_ids=[WF0],
        voice=None,
        personality=None,
    )

    # record_completed called once, with the downloaded face image path
    recorder.record_character_completed.assert_called_once_with(
        row_id=ROW_ID,
        workflow_ids=[WF0],
        primary_media_ids=[M0],
        voice=None,
        personality=None,
        image_paths=["/tmp/face_slot0.png"],
    )

    # Result DTO is correct
    assert isinstance(result, CharacterCreateResult)
    assert result.entity_id == ENTITY_ID
    assert result.project_id == PROJECT_ID
    assert result.workflow_ids == (WF0,)
    assert result.primary_media_ids == (M0,)
    assert result.name == NAME
    assert result.voice is None
    # The downloaded local image path is threaded into the result (face = slot 0)
    assert result.image_paths == ("/tmp/face_slot0.png",)


# ---------------------------------------------------------------------------
# Scenario #2: face + body — sequential, body carries face_media_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_face_and_body_sequential() -> None:
    """Two generates, body carries face_media_id, patch gets both workflow ids."""
    client = _make_client()
    recorder = _make_recorder()

    result = await character_create(client, recorder, **_saga_kwargs(body=BODY_REQ))

    # Both generates called
    assert client.generate_character_image.call_count == 2

    calls = client.generate_character_image.call_args_list

    # First call: face (idx 0)
    assert calls[0] == call(
        project_id=PROJECT_ID,
        entity_id=ENTITY_ID,
        req=FACE_REQ,
        image_reference_index=0,
        locale="en-US",
        format_prompt=False,
    )

    # Second call: body (idx 1) with face_media_id set
    body_req_sent = calls[1].kwargs["req"]
    assert body_req_sent.image_reference_index == 1
    assert body_req_sent.face_media_id == M0  # face media injected
    assert calls[1].kwargs["image_reference_index"] == 1

    # Two commits
    commit_calls = client.commit_workflow.call_args_list
    assert commit_calls[0] == call(WF0, project_id=PROJECT_ID, primary_media_id=M0)
    assert commit_calls[1] == call(WF1, project_id=PROJECT_ID, primary_media_id=M1)

    # patch gets both workflow ids
    client.patch_entity.assert_called_once_with(
        project_id=PROJECT_ID,
        entity_id=ENTITY_ID,
        display_name=NAME,
        workflow_ids=[WF0, WF1],
        voice=None,
        personality=None,
    )

    # Result carries both
    assert result.workflow_ids == (WF0, WF1)
    assert result.primary_media_ids == (M0, M1)


@pytest.mark.asyncio
async def test_body_generate_starts_after_face_commit() -> None:
    """Scenario #22: body generate must happen AFTER face commit, never concurrently."""
    call_order: list[str] = []

    async def _generate(
        *,
        project_id: str,
        entity_id: str,
        req: CharacterImageRequest,
        image_reference_index: int,
        locale: str,
        format_prompt: bool = False,
    ) -> tuple[str, str, str | None]:
        if image_reference_index == 0:
            call_order.append("generate_face")
            return (WF0, M0, "/tmp/face_slot0.png")
        call_order.append("generate_body")
        return (WF1, M1, "/tmp/body_slot1.png")

    async def _commit(wf_id: str, *, project_id: str, primary_media_id: str) -> None:
        if wf_id == WF0:
            call_order.append("commit_face")
        else:
            call_order.append("commit_body")

    client = MagicMock()
    client.create_entity = AsyncMock(return_value=ENTITY_ID)
    client.generate_character_image = AsyncMock(side_effect=_generate)
    client.commit_workflow = AsyncMock(side_effect=_commit)
    client.patch_entity = AsyncMock(return_value=None)

    recorder = _make_recorder()

    await character_create(client, recorder, **_saga_kwargs(body=BODY_REQ))

    assert call_order == ["generate_face", "commit_face", "generate_body", "commit_body"], (
        f"Expected sequential order, got: {call_order}"
    )


# ---------------------------------------------------------------------------
# Scenario #3: recovery — incomplete row, no ids yet → skip create_entity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_skips_create_entity_and_record_started() -> None:
    """Prior crash left STARTED row with entity_id but no workflow ids yet."""
    client = _make_client()
    recorder = _make_recorder()
    recorder.repository.find_incomplete_character = MagicMock(
        return_value={
            "row_id": ROW_ID,
            "entity_id": ENTITY_ID,
            "workflow_ids": [],
            "primary_media_ids": [],
        }
    )

    result = await character_create(client, recorder, **_saga_kwargs())

    # create_entity NOT called (reusing existing entity_id from row)
    client.create_entity.assert_not_called()
    # record_started NOT called (row already exists)
    recorder.record_character_started.assert_not_called()

    # generate still called for the missing face slot
    assert client.generate_character_image.call_count == 1

    # patch and record_completed still called
    client.patch_entity.assert_called_once()
    recorder.record_character_completed.assert_called_once()

    assert result.entity_id == ENTITY_ID
    assert result.workflow_ids == (WF0,)


# ---------------------------------------------------------------------------
# Scenario #4: recovery — face already recorded, skip face, do body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_skips_face_when_already_recorded() -> None:
    """Prior crash after face-commit: entity + face already in the row."""
    client = _make_client()
    # Only one return value needed: the body generate
    client.generate_character_image = AsyncMock(return_value=(WF1, M1, "/tmp/body_slot1.png"))
    recorder = _make_recorder()
    recorder.repository.find_incomplete_character = MagicMock(
        return_value={
            "row_id": ROW_ID,
            "entity_id": ENTITY_ID,
            "workflow_ids": [WF0],
            "primary_media_ids": [M0],
        }
    )

    result = await character_create(client, recorder, **_saga_kwargs(body=BODY_REQ))

    # create_entity NOT called
    client.create_entity.assert_not_called()

    # generate called exactly ONCE (body only — face skipped)
    assert client.generate_character_image.call_count == 1
    body_call = client.generate_character_image.call_args
    assert body_call.kwargs["image_reference_index"] == 1
    assert body_call.kwargs["req"].face_media_id == M0

    # patch gets both workflow ids
    client.patch_entity.assert_called_once()
    patch_kwargs = client.patch_entity.call_args.kwargs
    assert patch_kwargs["workflow_ids"] == [WF0, WF1]

    assert result.workflow_ids == (WF0, WF1)
    assert result.primary_media_ids == (M0, M1)


# ---------------------------------------------------------------------------
# Scenario #5: WireFormatError → record partial state, patch NOT called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wire_format_error_records_partial_does_not_patch() -> None:
    """generate_character_image raises WireFormatError → patch skipped, exception re-raised."""
    exc = WireFormatError(
        "parentEntityId mismatch: expected entity-abc got entity-other",
        route="test",
    )
    client = _make_client(generate_side_effect=exc)
    recorder = _make_recorder()

    with pytest.raises(WireFormatError):
        await character_create(client, recorder, **_saga_kwargs())

    # patch_entity must NOT have been called
    client.patch_entity.assert_not_called()
    # record_completed must NOT have been called
    recorder.record_character_completed.assert_not_called()
    # partial state persisted (empty list since generate raised before any ids)
    recorder.record_character_partial.assert_called_with(
        row_id=ROW_ID,
        workflow_ids=[],
        primary_media_ids=[],
    )


# ---------------------------------------------------------------------------
# Scenario #5b: WireFormatError on body → face already persisted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wire_format_error_on_body_preserves_face_partial() -> None:
    """WireFormatError on body generate — face partial state must be preserved."""
    exc = WireFormatError(
        f"parentEntityId mismatch: expected {ENTITY_ID} got entity-other",
        route="test",
    )

    async def _generate_side_effect(**kwargs: object) -> tuple[str, str, str | None]:
        if kwargs.get("image_reference_index") == 0:
            return (WF0, M0, "/tmp/face_slot0.png")
        raise exc

    client = MagicMock()
    client.create_entity = AsyncMock(return_value=ENTITY_ID)
    client.generate_character_image = AsyncMock(side_effect=_generate_side_effect)
    client.commit_workflow = AsyncMock(return_value=None)
    client.patch_entity = AsyncMock(return_value=None)

    recorder = _make_recorder()

    with pytest.raises(WireFormatError):
        await character_create(client, recorder, **_saga_kwargs(body=BODY_REQ))

    # patch must NOT be called
    client.patch_entity.assert_not_called()
    # Last partial call must include the face ids
    last_partial = recorder.record_character_partial.call_args
    assert last_partial.kwargs["workflow_ids"] == [WF0]
    assert last_partial.kwargs["primary_media_ids"] == [M0]


# ---------------------------------------------------------------------------
# Scenario: voice and personality forwarded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_and_personality_forwarded() -> None:
    client = _make_client()
    recorder = _make_recorder()

    await character_create(
        client,
        recorder,
        **_saga_kwargs(voice="kore", personality="brave and kind"),
    )

    client.patch_entity.assert_called_once_with(
        project_id=PROJECT_ID,
        entity_id=ENTITY_ID,
        display_name=NAME,
        workflow_ids=[WF0],
        voice="kore",
        personality="brave and kind",
    )
    recorder.record_character_completed.assert_called_once_with(
        row_id=ROW_ID,
        workflow_ids=[WF0],
        primary_media_ids=[M0],
        voice="kore",
        personality="brave and kind",
        image_paths=["/tmp/face_slot0.png"],
    )


# ---------------------------------------------------------------------------
# Scenario: format_prompt forwarded to every generation slot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_format_prompt_defaults_off_and_forwards_when_set() -> None:
    """``format_prompt`` reaches face AND body generation, and is off by default."""
    client = _make_client()
    recorder = _make_recorder()

    await character_create(client, recorder, **_saga_kwargs(body=BODY_REQ))
    assert all(
        c.kwargs["format_prompt"] is False for c in client.generate_character_image.call_args_list
    ), "format_prompt must default to False on every slot"

    client = _make_client()
    recorder = _make_recorder()

    await character_create(
        client,
        recorder,
        **_saga_kwargs(body=BODY_REQ, format_prompt=True),
    )
    calls = client.generate_character_image.call_args_list
    assert len(calls) == 2, "face + body slots both generate"
    assert all(c.kwargs["format_prompt"] is True for c in calls), (
        "format_prompt must be forwarded to both the face and body slot"
    )
