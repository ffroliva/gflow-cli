"""E2E: editing a character's name, voice and personality on flow.google.com (#639).

On an account Flow serves from flow.google.com the aisandbox REST PATCH answers 401
(measured 2026-10-16), so ``FlowApiClient.patch_entity`` falls back to the character
editor: each field is committed by its own gesture and confirmed from its ``rzMKMb``
echo. Only a real editor shows that the gestures still commit and that a later project
read reflects them. Plan: docs/superpowers/plans/2026-10-16-character-portability, Task 5.

Opt-in: ``-m e2e_character`` with ``GFLOW_CLI_E2E_RUN_CHARACTER=1``,
``GFLOW_CLI_E2E_PROFILE`` and ``GFLOW_CLI_E2E_CHARACTER_PROJECT`` — a throwaway project
holding at least one character. The first character is edited and then restored.

Cost: $0 — edits and reads only.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest

from gflow_cli.api.character import Character
from gflow_cli.api.client import FlowApiClient

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_character]


def _project() -> str:
    if os.environ.get("GFLOW_CLI_E2E_RUN_CHARACTER", "").strip() != "1":
        pytest.skip("set GFLOW_CLI_E2E_RUN_CHARACTER=1 to run the character e2e tests")
    project = os.environ.get("GFLOW_CLI_E2E_CHARACTER_PROJECT", "").strip()
    if not project:
        pytest.skip("set GFLOW_CLI_E2E_CHARACTER_PROJECT to a throwaway project with a character")
    return project


async def _settled(client: FlowApiClient, project: str, entity: str, ok: object) -> Character:
    """Project reads lag writes by seconds (spike Addendum 1): poll until ``ok``."""
    last: Character | None = None
    for _ in range(8):
        last = next(c for c in await client.list_characters(project) if c.entity_id == entity)
        if ok(last):  # type: ignore[operator]
            return last
        await asyncio.sleep(8)
    raise AssertionError(f"the edit never showed up on a project read: {last}")


async def test_name_voice_and_personality_commit_through_the_editor(
    e2e_profile_dir: Path,
) -> None:
    project = _project()
    async with FlowApiClient(profile_dir=e2e_profile_dir) as client:
        original = (await client.list_characters(project))[0]
        token = uuid.uuid4().hex[:6]
        voice = "Charon" if original.voice != "Charon" else "Kore"
        try:
            await client.patch_entity(
                project_id=project,
                entity_id=original.entity_id,
                display_name=f"{original.display_name} {token}",
                workflow_ids=list(original.workflow_ids),
                voice=voice,
                personality=f"edited {token}",
            )
            edited = await _settled(
                client,
                project,
                original.entity_id,
                lambda c: c.personality == f"edited {token}" and c.voice == voice,
            )
            assert edited.display_name == f"{original.display_name} {token}"
            assert edited.workflow_ids == original.workflow_ids
        finally:
            await client.patch_entity(
                project_id=project,
                entity_id=original.entity_id,
                display_name=original.display_name,
                workflow_ids=list(original.workflow_ids),
                voice=original.voice,
                personality=original.personality,
            )
