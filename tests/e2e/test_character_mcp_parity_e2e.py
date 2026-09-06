"""Live proof that the MCP character tools agree with the CLI (#689).

AGENTS.md § "MCP parity is not a chore, it is the contract" says a twin is a separate
surface, so verifying the CLI does not verify MCP. This drives **both** against the same
live project and asserts they return the same entities.

That is the assertion that matters here, because the exemption these tools replaced
recorded exactly how a previous attempt failed: "the old MCP stub returned a misleading
empty list (#499)". An empty list is indistinguishable from a project with no characters,
so an agent silently concludes there is nothing to attach. Offline tests cannot catch
that -- a stub returning `[]` passes them all.

Opt-in: ``-m e2e_auth`` with ``GFLOW_CLI_E2E_PROFILE`` and
``GFLOW_CLI_E2E_CHARACTER_PROJECT`` set. Read-only; spends nothing.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.config import get_settings
from gflow_cli.mcp import tools as mcp_tools

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]

_PROJECT_ENV = "GFLOW_CLI_E2E_CHARACTER_PROJECT"


@pytest.fixture
def character_project() -> str:
    project = os.environ.get(_PROJECT_ENV, "").strip()
    if not project:
        pytest.skip(f"set {_PROJECT_ENV} to a Flow project id that has saved characters")
    return project


async def test_mcp_character_list_matches_the_cli_client(
    character_project: str,
    e2e_profile_dir: Any,
) -> None:
    """Both surfaces must report the same entities for the same project."""
    profile = os.environ["GFLOW_CLI_E2E_PROFILE"].strip()

    # The CLI's own path: FlowApiClient.list_characters, exactly what `character list` runs.
    settings = get_settings()
    async with FlowApiClient(
        profile_dir=settings.profile_subdir(profile),
        headless=settings.headless,
    ) as client:
        via_client = await client.list_characters(character_project)

    result: dict[str, Any] = await mcp_tools.gflow_character_list(
        project=character_project, profile=profile
    )

    assert result["status"] == "ok", result
    assert result["count"] == len(via_client)
    assert {c["entity_id"] for c in result["characters"]} == {c.entity_id for c in via_client}
    assert {c["display_name"] for c in result["characters"]} == {c.display_name for c in via_client}
    # The regression this replaces: a stub that always answered "none".
    assert via_client, (
        f"{_PROJECT_ENV} points at a project with no characters — pick one that has some, "
        "or this test cannot distinguish a working tool from the #499 empty-list stub"
    )


async def test_mcp_character_show_resolves_by_id_and_by_name(
    character_project: str,
    e2e_profile_dir: Any,
) -> None:
    """`show` must find the same entity by either selector, live."""
    profile = os.environ["GFLOW_CLI_E2E_PROFILE"].strip()

    listed = await mcp_tools.gflow_character_list(project=character_project, profile=profile)
    assert listed["status"] == "ok" and listed["characters"], listed
    first = listed["characters"][0]

    by_id = await mcp_tools.gflow_character_show(
        project=character_project, entity_id=first["entity_id"], profile=profile
    )
    assert by_id["status"] == "ok", by_id
    assert by_id["character"]["entity_id"] == first["entity_id"]

    by_name = await mcp_tools.gflow_character_show(
        project=character_project, name=first["display_name"], profile=profile
    )
    assert by_name["status"] == "ok", by_name
    assert by_name["character"]["entity_id"] == first["entity_id"]


async def test_mcp_character_payload_carries_no_signed_url(
    character_project: str,
    e2e_profile_dir: Any,
) -> None:
    """Live shape check: Character excludes credential-bearing CDN URLs by design."""
    profile = os.environ["GFLOW_CLI_E2E_PROFILE"].strip()
    result = await mcp_tools.gflow_character_list(project=character_project, profile=profile)

    blob = repr(result).lower()
    for leaked in ("http://", "https://", "fifeurl", "googleusercontent"):
        assert leaked not in blob, f"live character payload carries {leaked!r}"
