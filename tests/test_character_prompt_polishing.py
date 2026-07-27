"""Unit test suite for Flow character prompt-polishing control (#383).

Verifies that UiAutomationTransport, character_create saga, and Click CLI interface
support --format-prompt / format_prompt for in-editor prompt transformation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from gflow_cli.api.character import CharacterCreateResult, CharacterImageRequest
from gflow_cli.api.transports.ui_automation import (
    PROMPT_FORMAT_SELECTORS,
    UiAutomationTransport,
)
from gflow_cli.cli import main
from gflow_cli.cli_character import character
from gflow_cli.services.character_create import character_create


def test_prompt_format_selectors_include_locale_stable_ligature() -> None:
    """Verify PROMPT_FORMAT_SELECTORS contains personal_recommendations icon ligature."""
    assert any("personal_recommendations" in s for s in PROMPT_FORMAT_SELECTORS)


@pytest.mark.asyncio
async def test_format_character_prompt_clicks_first_visible_selector() -> None:
    """Verify format_character_prompt tries selectors in priority order and clicks."""
    transport = UiAutomationTransport()
    mock_page = MagicMock()
    mock_page.wait_for_timeout = AsyncMock()

    mock_locator = MagicMock()
    mock_locator.is_visible = AsyncMock(return_value=True)
    mock_locator.click = AsyncMock()

    mock_page.locator.return_value.first = mock_locator

    res = await transport.format_character_prompt(mock_page)

    assert res is True
    mock_page.locator.assert_called_with(PROMPT_FORMAT_SELECTORS[0])
    mock_locator.click.assert_called_once()


@pytest.mark.asyncio
async def test_format_character_prompt_returns_false_when_not_found() -> None:
    """Verify format_character_prompt returns False when no selector is visible."""
    transport = UiAutomationTransport()
    mock_page = MagicMock()
    mock_page.wait_for_timeout = AsyncMock()

    mock_locator = MagicMock()
    mock_locator.is_visible = AsyncMock(return_value=False)
    mock_locator.click = AsyncMock()

    mock_page.locator.return_value.first = mock_locator

    res = await transport.format_character_prompt(mock_page)

    assert res is False
    assert mock_locator.click.call_count == 0


@pytest.mark.asyncio
async def test_character_create_saga_forwards_format_prompt(tmp_path: Path) -> None:
    """Verify character_create saga passes format_prompt to generate_character_image."""
    client = MagicMock()
    recorder = MagicMock()
    recorder.repository.find_incomplete_character.return_value = None

    client.create_entity = AsyncMock(return_value="char_entity_123")
    client.create_character_entity = AsyncMock(return_value="char_entity_123")
    client.patch_character_entity = AsyncMock(return_value=None)
    client.patch_entity = AsyncMock(return_value=None)
    client.commit_workflow = AsyncMock(return_value=None)
    client.generate_character_image = AsyncMock(
        return_value=("wf_face_1", "m_face_1", "/tmp/face.png")
    )

    face = CharacterImageRequest(prompt="test face prompt")

    res = await character_create(
        client,
        recorder,
        profile_name="test",
        profile_dir=tmp_path,
        project_id="proj_123",
        name="TestChar",
        face=face,
        format_prompt=True,
    )

    assert isinstance(res, CharacterCreateResult)
    assert res.entity_id == "char_entity_123"
    client.generate_character_image.assert_called_once()
    _, kwargs = client.generate_character_image.call_args
    assert kwargs.get("format_prompt") is True


def test_cli_character_create_parses_format_prompt_flag(tmp_path: Path) -> None:
    """Verify gflow character create --format-prompt flag is parsed."""
    result = CharacterCreateResult(
        entity_id="entity-abc",
        project_id="proj-1",
        name="TestChar",
        workflow_ids=("wf-face-1",),
        primary_media_ids=("media-face-1",),
    )
    mock_settings = MagicMock()
    mock_settings.headless = True
    mock_settings.resolved_db_path.return_value = tmp_path / "gflow.db"
    mock_settings.history_prompts = "hash"

    mock_recorder = MagicMock()
    mock_client_instance = AsyncMock()
    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("gflow_cli.cli_character.get_settings", return_value=mock_settings),
        patch("gflow_cli.cli_character._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_character._make_provider_dir", return_value=tmp_path / "profile"),
        patch("gflow_cli.cli_character.FlowApiClient", return_value=mock_client_cm),
        patch("gflow_cli.cli_character.OperationRecorder.open", return_value=mock_recorder),
        patch(
            "gflow_cli.cli_character.character_create", new=AsyncMock(return_value=result)
        ) as mock_saga,
    ):
        runner = CliRunner()
        cli_result = runner.invoke(
            character,
            [
                "create",
                "--project",
                "proj-1",
                "--name",
                "TestChar",
                "--face-prompt",
                "hero",
                "--format-prompt",
            ],
            catch_exceptions=False,
        )

    assert cli_result.exit_code == 0, cli_result.output
    call_kwargs = mock_saga.call_args.kwargs
    assert call_kwargs.get("format_prompt") is True
