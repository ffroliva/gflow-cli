# SPDX-License-Identifier: MIT
"""Unit tests for the ``gflow_instructions_*`` MCP tools.

FlowApiClient is faked so the tests run offline: each fake records the
``patch_agent_info`` calls, letting the tests assert the read-modify-write
contract (full card set sent, ids preserved) that the CLI counterparts pin
in tests/test_cli_instructions*.py.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from gflow_cli.api.image import AgentInstruction, ProjectBrief

_PROJECT = "12345678-1234-1234-1234-123456789abc"


class _FakeClient:
    """Minimal FlowApiClient stand-in recording brief reads/patches."""

    def __init__(self, brief: ProjectBrief):
        self.brief = brief
        self.patch_calls: list[dict[str, Any]] = []
        self.uploads: list[str] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def get_agent_info(self, project_id: str) -> ProjectBrief:
        return self.brief

    async def patch_agent_info(
        self, project_id: str, *, enabled: bool, cards: tuple[AgentInstruction, ...] | None = None
    ) -> None:
        self.patch_calls.append({"project_id": project_id, "enabled": enabled, "cards": cards})

    async def upload_image(self, project_id: str, path: Any) -> SimpleNamespace:
        self.uploads.append(str(path))
        return SimpleNamespace(name="dddddddd-dddd-dddd-dddd-dddddddddddd")


def _patched(fake: _FakeClient):
    """Patch client construction + profile resolution for one tool call."""
    return (
        patch("gflow_cli.mcp.tools.FlowApiClient", MagicMock(return_value=fake)),
        patch("gflow_cli.mcp.tools._resolve_and_validate_profile", return_value="testprof"),
    )


def _brief(*cards: AgentInstruction, enabled: bool = True) -> ProjectBrief:
    return ProjectBrief(enabled=enabled, cards=cards)


_CARD_A = AgentInstruction(text="stay on-model", title="Hero look", id="card-a")
_CARD_B = AgentInstruction(text="crayon style", title="Style", id="card-b", enabled=False)


async def _call(tool_name: str, fake: _FakeClient, /, **kwargs: Any) -> dict[str, Any]:
    from gflow_cli.mcp import tools as mcp_tools

    tool = getattr(mcp_tools, tool_name)
    p1, p2 = _patched(fake)
    with p1, p2:
        return await tool(**kwargs)


class TestInstructionsList:
    @pytest.mark.asyncio
    async def test_returns_cards_payload(self) -> None:
        fake = _FakeClient(_brief(_CARD_A, _CARD_B))
        result = await _call("gflow_instructions_list", fake, project=_PROJECT)
        assert result["status"] == "ok"
        assert result["project_id"] == _PROJECT
        assert result["enabled"] is True
        titles = [c["title"] for c in result["cards"]]
        assert titles == ["Hero look", "Style"]
        assert result["cards"][0]["id"] == "card-a"
        assert fake.patch_calls == []  # read-only

    @pytest.mark.asyncio
    async def test_rejects_invalid_project_id(self) -> None:
        # Underscores/spaces fail the CLI's _FLOW_ID_RE — the MCP tool mirrors it.
        fake = _FakeClient(_brief())
        result = await _call("gflow_instructions_list", fake, project="bad_id with spaces")
        assert result["status"] == "error"
        assert result["error"]["status"] == 400

    @pytest.mark.asyncio
    async def test_rejects_empty_project_id(self) -> None:
        fake = _FakeClient(_brief())
        result = await _call("gflow_instructions_list", fake, project="")
        assert result["status"] == "error"
        assert result["error"]["status"] == 400


class TestInstructionsAdd:
    @pytest.mark.asyncio
    async def test_appends_card_and_patches_full_set(self) -> None:
        fake = _FakeClient(_brief(_CARD_A))
        result = await _call(
            "gflow_instructions_add",
            fake,
            project=_PROJECT,
            title="New rule",
            text="always night scenes",
        )
        assert result["status"] == "ok"
        assert result["card"]["title"] == "New rule"
        (call,) = fake.patch_calls
        assert call["enabled"] is True
        assert [c.id for c in call["cards"][:1]] == ["card-a"]  # existing kept, id preserved
        assert call["cards"][-1].text == "always night scenes"

    @pytest.mark.asyncio
    async def test_uuid_ref_becomes_image_reference(self) -> None:
        fake = _FakeClient(_brief())
        ref = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
        result = await _call(
            "gflow_instructions_add",
            fake,
            project=_PROJECT,
            title="Ref card",
            text="use the mood board",
            refs=[ref],
        )
        assert result["status"] == "ok"
        assert result["card"]["image_media_ids"] == [ref]

    @pytest.mark.asyncio
    async def test_empty_card_is_bad_parameter(self) -> None:
        fake = _FakeClient(_brief())
        result = await _call(
            "gflow_instructions_add", fake, project=_PROJECT, title="Empty", text="   "
        )
        assert result["status"] == "error"
        assert result["error"]["status"] == 400
        assert fake.patch_calls == []


class TestInstructionsSetEnabled:
    @pytest.mark.asyncio
    async def test_enables_target_by_title_preserving_others(self) -> None:
        fake = _FakeClient(_brief(_CARD_A, _CARD_B))
        result = await _call(
            "gflow_instructions_set_enabled",
            fake,
            project=_PROJECT,
            title="Style",
            enabled=True,
        )
        assert result["status"] == "ok"
        assert result["card"]["enabled"] is True
        assert result["card"]["id"] == "card-b"  # id preserved across the flip
        (call,) = fake.patch_calls
        assert [(c.id, c.enabled) for c in call["cards"]] == [("card-a", True), ("card-b", True)]

    @pytest.mark.asyncio
    async def test_requires_exactly_one_selector(self) -> None:
        fake = _FakeClient(_brief(_CARD_A))
        result = await _call("gflow_instructions_set_enabled", fake, project=_PROJECT, enabled=True)
        assert result["status"] == "error"
        assert result["error"]["status"] == 400
        both = await _call(
            "gflow_instructions_set_enabled",
            fake,
            project=_PROJECT,
            title="Hero look",
            card_id="card-a",
            enabled=True,
        )
        assert both["status"] == "error"

    @pytest.mark.asyncio
    async def test_unknown_title_is_bad_parameter(self) -> None:
        fake = _FakeClient(_brief(_CARD_A))
        result = await _call(
            "gflow_instructions_set_enabled",
            fake,
            project=_PROJECT,
            title="Nope",
            enabled=False,
        )
        assert result["status"] == "error"
        assert result["error"]["status"] == 400
        assert fake.patch_calls == []


class TestInstructionsRm:
    @pytest.mark.asyncio
    async def test_removes_by_id_and_sends_remaining_set(self) -> None:
        fake = _FakeClient(_brief(_CARD_A, _CARD_B))
        result = await _call("gflow_instructions_rm", fake, project=_PROJECT, card_id="card-a")
        assert result["status"] == "ok"
        assert result["card"]["id"] == "card-a"
        (call,) = fake.patch_calls
        assert [c.id for c in call["cards"]] == ["card-b"]


class TestInstructionsToggleMode:
    @pytest.mark.asyncio
    async def test_patches_master_switch_without_cards(self) -> None:
        fake = _FakeClient(_brief(_CARD_A))
        result = await _call(
            "gflow_instructions_toggle_mode", fake, project=_PROJECT, enabled=False
        )
        assert result["status"] == "ok"
        assert result["agent_mode_enabled"] is False
        (call,) = fake.patch_calls
        assert call["enabled"] is False
        assert call["cards"] is None  # cards untouched


class TestInstructionsApply:
    @pytest.mark.asyncio
    async def test_full_replaces_brief_from_entries(self) -> None:
        fake = _FakeClient(_brief(_CARD_A))
        result = await _call(
            "gflow_instructions_apply",
            fake,
            project=_PROJECT,
            cards=[
                {"title": "One", "text": "first"},
                {"title": "Two", "text": "second", "enabled": False},
            ],
        )
        assert result["status"] == "ok"
        assert [c["title"] for c in result["cards"]] == ["One", "Two"]
        (call,) = fake.patch_calls
        assert [c.title for c in call["cards"]] == ["One", "Two"]  # _CARD_A replaced away
        assert call["cards"][1].enabled is False

    @pytest.mark.asyncio
    async def test_malformed_entry_is_bad_parameter(self) -> None:
        fake = _FakeClient(_brief())
        result = await _call(
            "gflow_instructions_apply",
            fake,
            project=_PROJECT,
            cards=[{"title": "Bad", "text": "x", "ref": "not-a-list"}],
        )
        assert result["status"] == "error"
        assert result["error"]["status"] == 400
        assert fake.patch_calls == []
