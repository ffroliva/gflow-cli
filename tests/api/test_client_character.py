"""Tests for FlowApiClient character entity methods (issue #145, Task 4).

Mocks the transport layer (_post_json / _get_json) so no Playwright / network
calls are made.  Mirrors the fake-page pattern used in test_client_scene.py.
"""

from __future__ import annotations

import json as _json
from urllib.parse import quote as _quote

import pytest

from gflow_cli.api import routes
from gflow_cli.api.character import Character
from gflow_cli.api.client import FlowApiClient, _unwrap_trpc
from gflow_cli.errors import ConfigurationError, WireFormatError

# ---------------------------------------------------------------------------
# Fake transport helpers (mirror test_client_scene.py pattern)
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status: int, text: str) -> None:
        self.status = status
        self._text = text
        self.headers: dict[str, str] = {}

    async def text(self) -> str:
        return self._text


class _FakeRequest:
    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp
        self.calls: list[tuple[str, str, dict]] = []

    async def get(self, url: str, **kw: object) -> _FakeResp:
        self.calls.append(("GET", url, dict(kw)))
        return self._resp

    async def post(self, url: str, **kw: object) -> _FakeResp:
        self.calls.append(("POST", url, dict(kw)))
        return self._resp


class _FakePage:
    def __init__(self, resp: _FakeResp) -> None:
        self.request = _FakeRequest(resp)


def _client_with(page: _FakePage) -> FlowApiClient:
    c = FlowApiClient.__new__(FlowApiClient)
    c._page = page
    c._page_queue = None
    c._context = None
    c._access_token = "ya29.test"
    c._access_token_exp = 9_999_999_999
    return c


# ---------------------------------------------------------------------------
# tRPC wire shapes
# ---------------------------------------------------------------------------


def _trpc_wrap(payload: dict) -> dict:
    """Build a standard tRPC v10 envelope around *payload*."""
    return {"result": {"data": {"json": payload}}}


def _one_character_response(project_id: str, entity_id: str = "e1") -> dict:
    """Minimal projectInitialData tRPC reply containing one CHARACTER entity."""
    return _trpc_wrap(
        {
            "projectContents": {
                "entities": [
                    {
                        "entityId": entity_id,
                        "projectId": project_id,
                        "entityInfo": {
                            "entityType": "CHARACTER",
                            "displayName": "Alice",
                            "characterInfo": {
                                "personalityNotes": "friendly",
                                "imageReferences": [{"workflowId": "wf-1"}],
                            },
                        },
                    }
                ]
            }
        }
    )


# ---------------------------------------------------------------------------
# _unwrap_trpc unit tests
# ---------------------------------------------------------------------------


def test_unwrap_trpc_standard_shape():
    data = _trpc_wrap({"entityId": "abc"})
    assert _unwrap_trpc(data) == {"entityId": "abc"}


def test_unwrap_trpc_missing_result_raises():
    with pytest.raises(WireFormatError, match="missing 'result'"):
        _unwrap_trpc({"something": "else"})


def test_unwrap_trpc_non_dict_raises():
    with pytest.raises(WireFormatError):
        _unwrap_trpc([1, 2, 3])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# create_entity
# ---------------------------------------------------------------------------


async def test_create_entity_posts_correct_url_and_body():
    resp = _FakeResp(200, _json.dumps(_trpc_wrap({"entityId": "e-new"})))
    page = _FakePage(resp)
    client = _client_with(page)

    entity_id = await client.create_entity("proj-1")

    assert entity_id == "e-new"
    method, url, kw = page.request.calls[-1]
    assert method == "POST"
    assert url == routes.CREATE_ENTITY_URL
    body = _json.loads(kw["data"])
    assert body == {"json": {"projectId": "proj-1"}}


async def test_create_entity_uses_application_json_content_type():
    resp = _FakeResp(200, _json.dumps(_trpc_wrap({"entityId": "e-ct"})))
    page = _FakePage(resp)
    client = _client_with(page)

    await client.create_entity("proj-ct")

    _, _, kw = page.request.calls[-1]
    assert kw["headers"]["content-type"] == "application/json"


async def test_create_entity_missing_entity_id_raises_wire_format_error():
    resp = _FakeResp(200, _json.dumps(_trpc_wrap({"someOtherKey": "value"})))
    page = _FakePage(resp)
    client = _client_with(page)

    with pytest.raises(WireFormatError, match="entityId"):
        await client.create_entity("proj-bad")


# ---------------------------------------------------------------------------
# list_characters
# ---------------------------------------------------------------------------


async def test_list_characters_returns_character_list():
    proj = "proj-list"
    resp = _FakeResp(200, _json.dumps(_one_character_response(proj)))
    page = _FakePage(resp)
    client = _client_with(page)

    chars = await client.list_characters(proj)

    assert len(chars) == 1
    assert isinstance(chars[0], Character)
    assert chars[0].entity_id == "e1"
    assert chars[0].display_name == "Alice"


async def test_list_characters_get_url_has_correct_input_encoding():
    proj = "proj-enc"
    resp = _FakeResp(200, _json.dumps(_one_character_response(proj)))
    page = _FakePage(resp)
    client = _client_with(page)

    await client.list_characters(proj)

    method, url, _ = page.request.calls[-1]
    assert method == "GET"
    expected_input = _quote(
        _json.dumps({"json": {"projectId": proj}}, separators=(",", ":")),
        safe="",
    )
    assert url == f"{routes.PROJECT_INITIAL_DATA_URL}?input={expected_input}"


async def test_list_characters_empty_project_returns_empty_list():
    resp = _FakeResp(
        200,
        _json.dumps(_trpc_wrap({"projectContents": {"entities": []}})),
    )
    page = _FakePage(resp)
    client = _client_with(page)

    chars = await client.list_characters("proj-empty")
    assert chars == []


# ---------------------------------------------------------------------------
# get_character
# ---------------------------------------------------------------------------


async def test_get_character_by_entity_id_found():
    proj = "proj-gc"
    resp = _FakeResp(200, _json.dumps(_one_character_response(proj, entity_id="e42")))
    page = _FakePage(resp)
    client = _client_with(page)

    char = await client.get_character(proj, entity_id="e42")
    assert char.entity_id == "e42"


async def test_get_character_by_name_found():
    proj = "proj-gc-name"
    resp = _FakeResp(200, _json.dumps(_one_character_response(proj)))
    page = _FakePage(resp)
    client = _client_with(page)

    char = await client.get_character(proj, name="Alice")
    assert char.display_name == "Alice"


async def test_get_character_by_entity_id_not_found_raises():
    proj = "proj-nf"
    resp = _FakeResp(200, _json.dumps(_one_character_response(proj, entity_id="e1")))
    page = _FakePage(resp)
    client = _client_with(page)

    with pytest.raises(ConfigurationError, match="character not found"):
        await client.get_character(proj, entity_id="does-not-exist")


async def test_get_character_by_name_not_found_raises():
    proj = "proj-nf-name"
    resp = _FakeResp(200, _json.dumps(_one_character_response(proj)))
    page = _FakePage(resp)
    client = _client_with(page)

    with pytest.raises(ConfigurationError, match="character not found"):
        await client.get_character(proj, name="Bob")


async def test_get_character_by_name_ambiguous_raises():
    proj = "proj-amb"
    # Two characters with the same display name
    payload = _trpc_wrap(
        {
            "projectContents": {
                "entities": [
                    {
                        "entityId": "e-x",
                        "projectId": proj,
                        "entityInfo": {
                            "entityType": "CHARACTER",
                            "displayName": "Alice",
                            "characterInfo": {"personalityNotes": "", "imageReferences": []},
                        },
                    },
                    {
                        "entityId": "e-y",
                        "projectId": proj,
                        "entityInfo": {
                            "entityType": "CHARACTER",
                            "displayName": "Alice",
                            "characterInfo": {"personalityNotes": "", "imageReferences": []},
                        },
                    },
                ]
            }
        }
    )
    resp = _FakeResp(200, _json.dumps(payload))
    page = _FakePage(resp)
    client = _client_with(page)

    with pytest.raises(ConfigurationError, match=r"ambiguous.*e-x"):
        await client.get_character(proj, name="Alice")


async def test_get_character_no_args_raises_value_error():
    proj = "proj-noarg"
    resp = _FakeResp(200, _json.dumps(_one_character_response(proj)))
    page = _FakePage(resp)
    client = _client_with(page)

    with pytest.raises(ValueError, match="entity_id or name"):
        await client.get_character(proj)


# ---------------------------------------------------------------------------
# routes constants sanity
# ---------------------------------------------------------------------------


def test_route_constants_correct_bases():
    assert routes.CREATE_ENTITY_URL == "https://labs.google/fx/api/trpc/flow.createEntity"
    assert routes.PROJECT_INITIAL_DATA_URL == (
        "https://labs.google/fx/api/trpc/flow.projectInitialData"
    )
    assert routes.FLOW_ENTITIES_URL == ("https://aisandbox-pa.googleapis.com/v1/flow/entities")
