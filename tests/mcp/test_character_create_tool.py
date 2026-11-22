"""``gflow_character_create``: the MCP twin of ``character create --image`` (#639, #691)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.api.character import CharacterCreateResult
from gflow_cli.errors import ConfigurationError
from gflow_cli.mcp import character_tools
from gflow_cli.mcp import server as mcp_server
from gflow_cli.mcp import tools as mcp_tools

_PROJECT = "00000000-0000-4000-8000-000000000003"
_CREATED = CharacterCreateResult(
    entity_id="new-entity",
    project_id=_PROJECT,
    workflow_ids=("w0", "w1"),
    primary_media_ids=("m0", "m1"),
    name="Nora",
    voice="Aoede",
    image_paths=(),
)


@pytest.fixture
def _no_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def always_allow() -> bool:
        return True

    monkeypatch.setattr(mcp_tools._rate_limiter, "acquire", always_allow)  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(mcp_tools, "_resolve_and_validate_profile", lambda p: "testprofile")


def _client() -> Any:
    ctx = AsyncMock()
    ctx.__aenter__.return_value = AsyncMock()
    ctx.__aexit__.return_value = False
    return ctx


@pytest.mark.usefixtures("_no_rate_limit")
async def test_builds_from_files_with_a_canonical_voice(tmp_path: Path) -> None:
    (tmp_path / "f.jpg").write_bytes(b"x")
    (tmp_path / "b.jpg").write_bytes(b"x")
    service = AsyncMock(return_value=_CREATED)
    recorder = MagicMock()
    with (
        patch.object(character_tools, "FlowApiClient", return_value=_client()),
        patch.object(character_tools, "character_create_from_files", service),
        patch.object(character_tools.OperationRecorder, "open", return_value=recorder),
    ):
        result = await character_tools.gflow_character_create(
            project=_PROJECT,
            name="Nora",
            image=str(tmp_path / "f.jpg"),
            body_image=str(tmp_path / "b.jpg"),
            voice="aoede",
            personality="calm",
        )
    assert result["status"] == "ok"
    assert result["character"]["entity_id"] == "new-entity"
    assert result["character"]["workflow_ids"] == ["w0", "w1"]
    kwargs = service.await_args.kwargs
    assert (kwargs["voice"], kwargs["body"], kwargs["portrait"], kwargs["personality"]) == (
        "Aoede",
        (tmp_path / "b.jpg").resolve(),
        (tmp_path / "f.jpg").resolve(),
        "calm",
    )
    assert kwargs["profile_name"] == "testprofile"
    recorder.close.assert_called_once()


@pytest.mark.usefixtures("_no_rate_limit")
async def test_refuses_an_unknown_voice_before_any_browser(tmp_path: Path) -> None:
    (tmp_path / "x.jpg").write_bytes(b"x")

    def explode(*_: Any, **__: Any) -> Any:
        raise AssertionError("an unknown voice must be refused before a client opens")

    with patch.object(character_tools, "FlowApiClient", explode):
        result = await character_tools.gflow_character_create(
            project=_PROJECT, name="Nora", image=str(tmp_path / "x.jpg"), voice="NotAVoice"
        )
    assert result["status"] == "error"
    assert "gflow_character_voices" in result["error"]["detail"]


@pytest.mark.usefixtures("_no_rate_limit")
async def test_a_refused_image_comes_back_as_a_problem_not_a_crash(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_bytes(b"x")
    service = AsyncMock(side_effect=ConfigurationError(detail="cannot use x.txt"))
    with (
        patch.object(character_tools, "FlowApiClient", return_value=_client()),
        patch.object(character_tools, "character_create_from_files", service),
        patch.object(character_tools.OperationRecorder, "open", return_value=MagicMock()),
    ):
        result = await character_tools.gflow_character_create(
            project=_PROJECT, name="Nora", image=str(tmp_path / "x.txt")
        )
    assert result["status"] == "error"
    assert "x.txt" in result["error"]["detail"]


async def test_a_bad_profile_is_returned_before_any_browser(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "x.jpg").write_bytes(b"x")

    async def always_allow() -> bool:
        return True

    refusal = {"status": "error", "error": {"detail": "unknown profile"}}
    monkeypatch.setattr(mcp_tools._rate_limiter, "acquire", always_allow)  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(mcp_tools, "_resolve_and_validate_profile", lambda p: refusal)
    with patch.object(character_tools, "FlowApiClient", side_effect=AssertionError):
        result = await character_tools.gflow_character_create(
            project=_PROJECT, name="Nora", image=str(tmp_path / "x.jpg"), profile="nobody"
        )
    assert result == refusal


def test_the_tool_is_registered_and_survives_the_no_spend_gate() -> None:
    assert "gflow_character_create" in mcp_server.server._tool_manager._tools  # pyright: ignore[reportPrivateUsage]
    assert "gflow_character_create" not in mcp_server._SPEND_TOOLS  # pyright: ignore[reportPrivateUsage]


_VALID_PROJECT = _PROJECT


@pytest.mark.usefixtures("_no_rate_limit")
@pytest.mark.parametrize(
    ("kwargs", "title"),
    [
        ({"project": "not a project", "name": "Nora"}, "Invalid Project Id"),
        ({"project": _VALID_PROJECT, "name": "   "}, "Blank Name"),
        ({"project": _VALID_PROJECT, "name": "Nora", "image": ""}, "Invalid Image"),
        (
            {"project": _VALID_PROJECT, "name": "Nora", "body_image": "/no/such/body.png"},
            "Invalid Body Image",
        ),
    ],
)
async def test_bad_inputs_are_refused_before_any_browser(
    tmp_path: Path, kwargs: dict[str, Any], title: str
) -> None:
    face = tmp_path / "face.png"
    face.write_bytes(b"x")
    args: dict[str, Any] = {"image": str(face), **kwargs}
    with patch.object(character_tools, "FlowApiClient", side_effect=AssertionError):
        result = await character_tools.gflow_character_create(**args)
    assert result["status"] == "error"
    assert result["error"]["title"] == title


@pytest.mark.usefixtures("_no_rate_limit")
async def test_a_directory_is_not_an_image(tmp_path: Path) -> None:
    with patch.object(character_tools, "FlowApiClient", side_effect=AssertionError):
        result = await character_tools.gflow_character_create(
            project=_VALID_PROJECT, name="Nora", image=str(tmp_path)
        )
    assert result["status"] == "error"
