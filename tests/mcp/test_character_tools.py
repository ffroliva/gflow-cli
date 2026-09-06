# SPDX-License-Identifier: MIT
"""MCP character discovery tools (#689).

Attaching a saved CHARACTER over MCP already worked -- `@Name` in the prompt and now
`reference_entities` by id both reach `referenceEntities`. What did not work was
*finding out what to attach*: `character list` and `character show` were exempt, so an
agent could only use a name a human had told it.

`_MCP_EXEMPT` records why the previous attempt was withdrawn: "the old MCP stub
returned a misleading empty list (#499)". An empty list is indistinguishable from a
project with no characters, so an agent silently concludes there is nothing to attach.
The first test here is therefore about exactly that failure -- the tool must return what
the client returns, and must never manufacture emptiness.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from gflow_cli.api.character import Character
from gflow_cli.mcp import tools as mcp_tools

_PROJECT = "proj-1234"
_ALDOUS = Character(
    entity_id="11111111-2222-3333-4444-555555555555",
    display_name="Aldous",
    project_id=_PROJECT,
    workflow_ids=("wf-1",),
    voice="calm baritone",
    personality="weary",
    thumbnail_media_id="media-1",
)
_MIRA = Character(
    entity_id="66666666-7777-8888-9999-000000000000",
    display_name="Mira",
    project_id=_PROJECT,
    workflow_ids=(),
    voice=None,
    personality=None,
    thumbnail_media_id=None,
)


@pytest.fixture
def _no_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def always_allow() -> bool:
        return True

    monkeypatch.setattr(mcp_tools._rate_limiter, "acquire", always_allow)
    monkeypatch.setattr(mcp_tools, "_resolve_and_validate_profile", lambda p: "testprofile")


def _client_returning(**methods: Any) -> Any:
    """A FlowApiClient stand-in usable as an async context manager."""
    client = AsyncMock()
    for name, value in methods.items():
        setattr(client, name, AsyncMock(return_value=value))
    ctx = AsyncMock()
    ctx.__aenter__.return_value = client
    ctx.__aexit__.return_value = False
    return ctx, client


@pytest.mark.usefixtures("_no_rate_limit")
async def test_character_list_returns_the_projects_characters() -> None:
    """The regression #499 left behind: real rows must survive to the caller."""
    ctx, client = _client_returning(list_characters=[_ALDOUS, _MIRA])
    with patch.object(mcp_tools, "FlowApiClient", return_value=ctx):
        result: dict[str, Any] = await mcp_tools.gflow_character_list(project=_PROJECT)

    assert result["status"] == "ok"
    assert result["count"] == 2
    names = [c["display_name"] for c in result["characters"]]
    assert names == ["Aldous", "Mira"]
    # The id is the point of the tool: it is what reference_entities takes.
    assert result["characters"][0]["entity_id"] == _ALDOUS.entity_id
    client.list_characters.assert_awaited_once_with(_PROJECT)


@pytest.mark.usefixtures("_no_rate_limit")
async def test_character_list_reports_empty_as_empty_not_as_absence() -> None:
    """A genuinely empty project is fine -- it just must come from the client."""
    ctx, client = _client_returning(list_characters=[])
    with patch.object(mcp_tools, "FlowApiClient", return_value=ctx):
        result = await mcp_tools.gflow_character_list(project=_PROJECT)

    assert result["status"] == "ok"
    assert result["characters"] == []
    assert result["count"] == 0
    client.list_characters.assert_awaited_once()


@pytest.mark.usefixtures("_no_rate_limit")
async def test_character_list_never_leaks_a_signed_url() -> None:
    """Character deliberately excludes credential-bearing CDN URLs; keep it that way."""
    ctx, _ = _client_returning(list_characters=[_ALDOUS])
    with patch.object(mcp_tools, "FlowApiClient", return_value=ctx):
        result = await mcp_tools.gflow_character_list(project=_PROJECT)

    blob = repr(result).lower()
    for leaked in ("fifeurl", "thumbnailurl", "http://", "https://"):
        assert leaked not in blob, f"character payload carries {leaked!r}"


@pytest.mark.usefixtures("_no_rate_limit")
async def test_character_show_by_id() -> None:
    ctx, client = _client_returning(get_character=_ALDOUS)
    with patch.object(mcp_tools, "FlowApiClient", return_value=ctx):
        result = await mcp_tools.gflow_character_show(project=_PROJECT, entity_id=_ALDOUS.entity_id)

    assert result["status"] == "ok"
    assert result["character"]["entity_id"] == _ALDOUS.entity_id
    assert result["character"]["voice"] == "calm baritone"
    client.get_character.assert_awaited_once()


@pytest.mark.usefixtures("_no_rate_limit")
async def test_character_show_requires_exactly_one_selector() -> None:
    """Mirrors the CLI's UsageError: neither, or both, is a caller bug."""
    for kwargs in ({}, {"entity_id": "x", "name": "y"}):
        result = await mcp_tools.gflow_character_show(project=_PROJECT, **kwargs)
        assert result["status"] == "error", kwargs
        assert result["error"]["status"] == 400


@pytest.mark.usefixtures("_no_rate_limit")
async def test_character_voices_lists_the_preset_voices() -> None:
    """`voices` reads a static in-process tuple: no network, no browser, no cost.

    It was exempt from MCP as a "character mutation", which it is not — and it is the
    lookup an agent needs to choose a voice, so the omission compounded.
    """
    from gflow_cli.api.character import VOICES

    result: dict[str, Any] = await mcp_tools.gflow_character_voices()

    assert result["status"] == "ok"
    assert result["count"] == len(VOICES)
    names = [v["name"] for v in result["voices"]]
    assert names == [v.name for v in VOICES]
    assert all("description" in v for v in result["voices"])


@pytest.mark.usefixtures("_no_rate_limit")
async def test_character_voices_needs_no_browser_or_profile() -> None:
    """A static lookup must not open a session — patching the client to explode proves it."""

    def explode(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("gflow_character_voices must not construct a FlowApiClient")

    with patch.object(mcp_tools, "FlowApiClient", explode):
        result = await mcp_tools.gflow_character_voices()

    assert result["status"] == "ok"
    assert result["count"] > 0
