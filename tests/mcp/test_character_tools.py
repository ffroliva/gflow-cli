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


# --- flow.google.com Zzl0ze project read through MCP (#875, #987) ---------------------


def _migrated_client_ctx(tmp_path: Any) -> Any:
    """Real FlowApiClient instance configured to fall back on retired labs projectInitialData."""
    from unittest.mock import MagicMock

    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.config import Settings
    from gflow_cli.errors import WireFormatError

    client = FlowApiClient(
        profile_dir=tmp_path / "prof",
        settings=Settings(flow_host="auto"),
    )
    client._page = MagicMock()  # noqa: SLF001
    client._get_json = AsyncMock(  # type: ignore[method-assign]
        side_effect=WireFormatError(
            detail="Flow RPCs have been deprecated and disabled.", status=404
        )
    )
    ctx = AsyncMock()
    ctx.__aenter__.return_value = client
    ctx.__aexit__.return_value = False
    return ctx


@pytest.mark.usefixtures("_no_rate_limit")
async def test_character_list_on_flow_google_com_reads_zzl0ze_instead_of_exiting_7(
    tmp_path: Any,
) -> None:
    from gflow_cli.api.transports.migrated_character import ProjectRead

    ctx = _migrated_client_ctx(tmp_path)
    read = ProjectRead(characters=(_ALDOUS, _MIRA), voices=())
    with (
        patch.object(mcp_tools, "FlowApiClient", return_value=ctx),
        patch(
            "gflow_cli.api.transports.migrated_character.read_project",
            new=AsyncMock(return_value=read),
        ) as read_mock,
    ):
        result: dict[str, Any] = await mcp_tools.gflow_character_list(project=_PROJECT)

    assert result["status"] == "ok"
    assert result["count"] == 2
    assert [c["display_name"] for c in result["characters"]] == ["Aldous", "Mira"]
    read_mock.assert_awaited_once()


@pytest.mark.usefixtures("_no_rate_limit")
async def test_character_show_on_flow_google_com_reads_zzl0ze_and_reports_missing_as_exit_11(
    tmp_path: Any,
) -> None:
    from gflow_cli.api.transports.migrated_character import ProjectRead

    ctx = _migrated_client_ctx(tmp_path)
    read = ProjectRead(characters=(_ALDOUS,), voices=())
    with (
        patch.object(mcp_tools, "FlowApiClient", return_value=ctx),
        patch(
            "gflow_cli.api.transports.migrated_character.read_project",
            new=AsyncMock(return_value=read),
        ),
    ):
        ok_res: dict[str, Any] = await mcp_tools.gflow_character_show(
            project=_PROJECT, name="Aldous"
        )
        missing_res: dict[str, Any] = await mcp_tools.gflow_character_show(
            project=_PROJECT, name="Nobody"
        )

    assert ok_res["status"] == "ok"
    assert ok_res["character"]["entity_id"] == _ALDOUS.entity_id
    assert missing_res["status"] == "error"
    assert missing_res["error"]["type"].endswith("/configuration")
    assert "character not found" in missing_res["error"]["detail"]


def _assert_changelog_has_character_read_entry(changelog: str) -> None:
    import re

    blocks = re.split(r"\n(?=- \*\*|## )", changelog)
    entry = next(
        (b for b in blocks if "gflow character list" in b and "#987" in b),
        None,
    )
    assert entry is not None, "CHANGELOG.md is missing the #987 character read entry"
    assert "Enceladus" in entry
    assert "@ziyedbe" in entry
    for internal_name in ("Zzl0ze", "read_project", "parse_project_read", "voice_from_wire"):
        assert internal_name not in entry


@pytest.mark.parametrize("tool_name", ["gflow_character_list", "gflow_character_show"])
def test_character_read_tool_descriptions_document_flow_google_com_support(
    tool_name: str,
) -> None:
    from pathlib import Path

    tool = mcp_tools.server._tool_manager._tools[tool_name]  # noqa: SLF001
    assert "flow.google.com" in tool.description
    assert "Zzl0ze" not in tool.description
    assert "labs.google" not in tool.description
    assert "#875" not in tool.description

    changelog = (Path(__file__).resolve().parents[2] / "CHANGELOG.md").read_text(encoding="utf-8")
    _assert_changelog_has_character_read_entry(changelog)


def test_changelog_character_read_entry_survives_release_heading_move(tmp_path: Any) -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "CHANGELOG.md").read_text(encoding="utf-8")
    renamed = tmp_path / "CHANGELOG_renamed.md"
    renamed.write_text(src.replace("## [Unreleased]", "## [0.100.0]", 1), encoding="utf-8")
    _assert_changelog_has_character_read_entry(renamed.read_text(encoding="utf-8"))

    released = tmp_path / "CHANGELOG_released.md"
    released.write_text(
        src.replace("## [Unreleased]", "## [Unreleased]\n\n## [0.100.0] - 2026-11-15", 1),
        encoding="utf-8",
    )
    _assert_changelog_has_character_read_entry(released.read_text(encoding="utf-8"))


def test_character_list_tool_description_states_flow_google_com_mention_limits() -> None:
    desc = mcp_tools.server._tool_manager._tools["gflow_character_list"].description  # noqa: SLF001
    assert "works as @Name in a prompt" not in desc
    for phrase in (
        "first word",
        "@ picker",
        "exit 29",
        "mention index unavailable",
        "video r2v",
        "reference_entities",
        "reference_entity_names",
    ):
        assert phrase in desc, f"gflow_character_list description lacks {phrase!r}"


def test_mcp_doc_character_list_states_flow_google_com_mention_limits() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for rel in ("docs/MCP.md", "website/docs/MCP.md"):
        doc = (root / rel).read_text(encoding="utf-8")
        line = next(
            ln for ln in doc.splitlines() if ln.startswith("* `gflow_character_list(project,")
        )
        assert "can be used as an `@Name` mention" not in line, rel
        for phrase in (
            "migrated_read=False",
            "first whitespace-delimited word",
            "exit 29",
            "mention index unavailable",
            "video r2v",
            "reference_entities",
            "reference_entity_names",
        ):
            assert phrase in line, f"{rel} gflow_character_list line lacks {phrase!r}"
