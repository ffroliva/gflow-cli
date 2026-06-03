"""Tests for FlowApiClient.patch_entity (issue #145, Phase-2 Task 3).

Mocks _patch_json so no Playwright / network calls are made.
Mirrors the style of test_client_character.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

from gflow_cli.api import routes
from gflow_cli.api.client import FlowApiClient

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"
_RESP = json.loads((FIXTURES / "patch_entity_response.json").read_text())


# ---------------------------------------------------------------------------
# Helper: build a minimal FlowApiClient with _patch_json mocked
# ---------------------------------------------------------------------------


def _client_with_mock() -> tuple[FlowApiClient, AsyncMock]:
    """Return (client, mock) where mock captures every _patch_json call."""
    c = FlowApiClient.__new__(FlowApiClient)
    c._page = None  # type: ignore[assignment]
    c._page_queue = None
    c._context = None
    c._access_token = "ya29.test"
    c._access_token_exp = 9_999_999_999

    mock = AsyncMock(return_value=_RESP)
    c._patch_json = mock  # type: ignore[method-assign]
    return c, mock


# ---------------------------------------------------------------------------
# test_patch_entity_minimal
# ---------------------------------------------------------------------------


async def test_patch_entity_minimal():
    """display_name + workflow_ids only — voice/personality absent."""
    client, mock = _client_with_mock()

    result = await client.patch_entity(
        project_id="p",
        entity_id="e",
        display_name="Ana",
        workflow_ids=["w1"],
    )

    mock.assert_awaited_once()
    url, body = mock.call_args.args
    assert url == routes.FLOW_ENTITIES_URL

    ent = body["entity"]
    assert ent["projectId"] == "p"
    assert ent["entityId"] == "e"
    assert ent["entityInfo"]["displayName"] == "Ana"
    assert ent["entityInfo"]["characterInfo"]["imageReferences"] == [{"workflowId": "w1"}]

    # updateMask must include display name AND imageReferences
    mask_parts = body["updateMask"].split(",")
    assert "entityInfo.displayName" in mask_parts
    assert "entityInfo.characterInfo.imageReferences" in mask_parts

    # voice and personality must NOT appear
    assert "entityInfo.characterInfo.personalityNotes" not in mask_parts
    assert "entityInfo.characterInfo.audioReferences" not in mask_parts

    # method returns None
    assert result is None


# ---------------------------------------------------------------------------
# test_patch_entity_with_voice_and_personality
# ---------------------------------------------------------------------------


async def test_patch_entity_with_voice_and_personality():
    """All optional fields supplied — all four paths must appear in updateMask."""
    client, mock = _client_with_mock()

    await client.patch_entity(
        project_id="p2",
        entity_id="e2",
        display_name="Bob",
        workflow_ids=["wA", "wB"],
        voice="gacrux",
        personality="brave",
    )

    url, body = mock.call_args.args
    assert url == routes.FLOW_ENTITIES_URL

    char_info = body["entity"]["entityInfo"]["characterInfo"]
    assert char_info["audioReferences"] == [{"presetVoiceId": "gacrux"}]
    assert char_info["personalityNotes"] == "brave"
    assert char_info["imageReferences"] == [{"workflowId": "wA"}, {"workflowId": "wB"}]

    mask_parts = body["updateMask"].split(",")
    assert "entityInfo.displayName" in mask_parts
    assert "entityInfo.characterInfo.imageReferences" in mask_parts
    assert "entityInfo.characterInfo.personalityNotes" in mask_parts
    assert "entityInfo.characterInfo.audioReferences" in mask_parts


# ---------------------------------------------------------------------------
# test_patch_entity_omits_absent_fields
# ---------------------------------------------------------------------------


async def test_patch_entity_omits_absent_fields():
    """No voice/personality → those keys must be absent from body AND updateMask."""
    client, mock = _client_with_mock()

    await client.patch_entity(
        project_id="p3",
        entity_id="e3",
        display_name="Carol",
        workflow_ids=["wZ"],
    )

    _, body = mock.call_args.args
    char_info = body["entity"]["entityInfo"]["characterInfo"]

    assert "personalityNotes" not in char_info
    assert "audioReferences" not in char_info

    mask_parts = body["updateMask"].split(",")
    assert "entityInfo.characterInfo.personalityNotes" not in mask_parts
    assert "entityInfo.characterInfo.audioReferences" not in mask_parts


# ---------------------------------------------------------------------------
# test_patch_entity_route_name_kwarg
# ---------------------------------------------------------------------------


async def test_patch_entity_passes_route_name():
    """_patch_json must be called with route_name='patchEntity'."""
    client, mock = _client_with_mock()

    await client.patch_entity(
        project_id="p4",
        entity_id="e4",
        display_name="Dave",
        workflow_ids=["wX"],
    )

    assert mock.call_args.kwargs.get("route_name") == "patchEntity"
