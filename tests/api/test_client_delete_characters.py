"""Tests for FlowApiClient.delete_characters (#150).

Mocks _post_json so no Playwright / network calls are made.
Mirrors the style of test_client_patch_entity.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from gflow_cli.api import routes
from gflow_cli.api.client import FlowApiClient


def _client_with_mock() -> tuple[FlowApiClient, AsyncMock]:
    """Return (client, mock) where mock captures every _post_json call."""
    c = FlowApiClient.__new__(FlowApiClient)
    c._page = None  # type: ignore[assignment]
    c._page_queue = None
    c._context = None
    c._access_token = "ya29.test"
    c._access_token_exp = 9_999_999_999

    mock = AsyncMock(return_value={})
    c._post_json = mock  # type: ignore[method-assign]
    return c, mock


async def test_delete_characters_posts_batch_delete():
    """POST flow:batchDeleteAssets with {projectId, entityIds}."""
    client, mock = _client_with_mock()

    result = await client.delete_characters("proj-1", ["e1", "e2"])

    mock.assert_awaited_once()
    url, body = mock.call_args.args
    assert url == routes.BATCH_DELETE_ASSETS_URL
    assert body == {"projectId": "proj-1", "entityIds": ["e1", "e2"]}
    assert mock.call_args.kwargs.get("route_name") == "batchDeleteAssets"
    assert result is None


async def test_delete_characters_rejects_empty():
    """Empty entity_ids is a programming error — raise before any network call."""
    client, mock = _client_with_mock()

    with pytest.raises(ValueError, match="non-empty"):
        await client.delete_characters("proj-1", [])

    mock.assert_not_awaited()
