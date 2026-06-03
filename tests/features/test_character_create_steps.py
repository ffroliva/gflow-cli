"""Step bindings for character_create.feature.

Scoped to this feature only — pytest-bdd uses module-scoped step registries
(per-conftest ``scenarios()`` call) so step phrases here don't leak into other
features.

Layer split (called out explicitly per the task's escalation clause):

* **CLI-level** ("happy create", "foreign workflow -> error"): we drive the real
  ``gflow character create`` command via Click's ``CliRunner`` and patch the same
  six seams the unit test patches (``tests/cli/test_cli_character_create.py``):
  ``get_settings`` / ``_resolve_profile`` / ``_make_provider_dir`` /
  ``FlowApiClient`` / ``OperationRecorder.open`` / ``character_create`` (the saga).
  This mirrors ``character_read.feature``'s infra-bypass pattern exactly: the CLI
  reaches the patched seam instead of bailing during profile/DB discovery, and we
  assert on the production exit code + output.

* **SAGA-level** ("resume - no re-spend"): the resume invariant (#3/#4) is a
  property of the saga's interaction with the *recorder* and *client*, not of the
  CLI. The CLI test already mocks the whole saga, so routing resume through the CLI
  would re-mock away the very behaviour under test and assert nothing. We therefore
  drive the **real** ``character_create`` saga with a mocked client + recorder whose
  ``find_incomplete_character`` returns an in-progress row carrying the face
  workflow, then assert ``generate_character_image`` is NOT called for the face
  (zero second credit). This mirrors
  ``tests/services/test_character_create_saga.py::test_recovery_skips_face_when_already_recorded``.

All scenarios use mocked client/recorder/saga — no live Playwright (the
``_forbid_live_playwright`` tripwire in conftest.py would fail otherwise).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner
from pytest_bdd import given, scenarios, then, when

from gflow_cli.api.character import CharacterCreateResult, CharacterImageRequest
from gflow_cli.cli_character import character
from gflow_cli.errors import WireFormatError
from gflow_cli.services.character_create import character_create

scenarios("character_create.feature")


# ---------------------------------------------------------------------------
# Constants shared across scenarios
# ---------------------------------------------------------------------------

_ENTITY_ID = "entity-bdd-1"
_WORKFLOW_ID = "wf-face-bdd-1"
_MEDIA_ID = "media-face-bdd-1"
_PROJECT_ID = "proj-bdd"
_NAME = "Knight"
_ROW_ID = "row-bdd-1"

# Patch seams (identical to tests/cli/test_cli_character_create.py)
_SAGA = "gflow_cli.cli_character.character_create"
_RECORDER_OPEN = "gflow_cli.cli_character.OperationRecorder.open"
_CLIENT = "gflow_cli.cli_character.FlowApiClient"
_RESOLVE_PROFILE = "gflow_cli.cli_character._resolve_profile"
_MAKE_PROVIDER_DIR = "gflow_cli.cli_character._make_provider_dir"
_GET_SETTINGS = "gflow_cli.cli_character.get_settings"


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cli_result_holder() -> dict[str, Any]:
    return {}


@pytest.fixture
def saga_state() -> dict[str, Any]:
    """Holds the saga AsyncMock + its raise behaviour for CLI-level scenarios,
    and the client/recorder mocks for the saga-level resume scenario."""
    return {}


# ---------------------------------------------------------------------------
# CLI-level scenario: happy create
# ---------------------------------------------------------------------------


@given("a mocked saga that returns a created character")
def _saga_returns_character(saga_state: dict[str, Any]) -> None:
    saga_state["result"] = CharacterCreateResult(
        entity_id=_ENTITY_ID,
        project_id=_PROJECT_ID,
        name=_NAME,
        workflow_ids=(_WORKFLOW_ID,),
        primary_media_ids=(_MEDIA_ID,),
        voice=None,
    )
    saga_state["raise"] = None


@given("a mocked saga that raises a foreign-workflow wire error")
def _saga_raises_wire_error(saga_state: dict[str, Any]) -> None:
    # parentEntityId mismatch => WireFormatError (scenario #5). The saga itself
    # records partial state and re-raises; the CLI must map this to exit 7 and
    # never reach the success/PATCH-confirmation output.
    saga_state["result"] = None
    saga_state["raise"] = WireFormatError(
        f"workflow parentEntityId 'foreign-eid' != entity {_ENTITY_ID!r}",
        route="generateCharacterImage",
    )


def _invoke_create(runner: CliRunner, saga_state: dict[str, Any], tmp_path: Path) -> Any:
    """Patch the six CLI seams and invoke ``character create`` with the configured
    saga behaviour (return value OR side-effect)."""
    mock_settings = MagicMock()
    mock_settings.headless = True
    mock_settings.resolved_db_path.return_value = tmp_path / "gflow.db"
    mock_settings.history_prompts = "hash"

    mock_recorder = MagicMock()
    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    if saga_state.get("raise") is not None:
        saga_mock = AsyncMock(side_effect=saga_state["raise"])
    else:
        saga_mock = AsyncMock(return_value=saga_state["result"])
    saga_state["saga_mock"] = saga_mock

    with (
        patch(_GET_SETTINGS, return_value=mock_settings),
        patch(_RESOLVE_PROFILE, return_value="default"),
        patch(_MAKE_PROVIDER_DIR, return_value=tmp_path / "profiles" / "default"),
        patch(_CLIENT, return_value=mock_client_cm),
        patch(_RECORDER_OPEN, return_value=mock_recorder),
        patch(_SAGA, new=saga_mock),
    ):
        return runner.invoke(
            character,
            ["create", "--project", "P", "--name", "X", "--face-prompt", "a face"],
        )


@when('I run "gflow character create --project P --name X --face-prompt a face"')
def _run_create(
    runner: CliRunner,
    cli_result_holder: dict[str, Any],
    saga_state: dict[str, Any],
    tmp_path: Path,
) -> None:
    cli_result_holder["result"] = _invoke_create(runner, saga_state, tmp_path)


@then("the create exit code is 0")
def _exit_0(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert result.exit_code == 0, f"output:\n{result.output}\nexc={result.exception!r}"


@then("the create exit code is 7")
def _exit_7(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    # WireFormatError maps to exit 7 per EXIT_CODE_MAP. Assert SystemExit so we
    # prove run_with_handlers caught + formatted the error (not a raw traceback).
    assert isinstance(result.exception, SystemExit), (
        f"expected SystemExit but got: {result.exception!r}\n{result.output}"
    )
    assert result.exit_code == 7, result.output


@then("the output contains the created entity id")
def _output_has_entity(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert _ENTITY_ID in result.output, f"expected {_ENTITY_ID!r} in:\n{result.output}"


@then("the output contains the created workflow id")
def _output_has_workflow(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert _WORKFLOW_ID in result.output, f"expected {_WORKFLOW_ID!r} in:\n{result.output}"


@then("the entity is not patched")
def _entity_not_patched(saga_state: dict[str, Any]) -> None:
    # The saga raised before completing; the CLI never invoked it more than once
    # and never reached a success path. The PATCH itself lives inside the saga
    # (proven NOT-called at the saga layer in test_character_create_saga.py); at
    # the CLI layer the observable invariant is that the failing saga produced a
    # non-zero exit and no success output. We assert the saga was invoked exactly
    # once (no silent retry that could double-spend).
    saga_mock = saga_state["saga_mock"]
    assert saga_mock.await_count == 1


# ---------------------------------------------------------------------------
# SAGA-level scenario: resume — no re-spend on the already-generated face
# ---------------------------------------------------------------------------


@given("an incomplete prior character op with the face workflow already recorded")
def _incomplete_prior_face(saga_state: dict[str, Any]) -> None:
    """Build a mocked client + recorder where the recorder's resume read returns
    an in-progress row whose face slot is already recorded."""
    client = MagicMock()
    client.create_entity = AsyncMock(return_value=_ENTITY_ID)
    # If the face were (wrongly) re-generated, this is the call that would fire
    # and spend a second credit. We assert it stays at 0 calls.
    client.generate_character_image = AsyncMock(return_value=(_WORKFLOW_ID, _MEDIA_ID, None))
    client.commit_workflow = AsyncMock(return_value=None)
    client.patch_entity = AsyncMock(return_value=None)

    recorder = MagicMock()
    recorder.record_character_started = MagicMock(return_value=_ROW_ID)
    recorder.record_character_partial = MagicMock(return_value=None)
    recorder.record_character_completed = MagicMock(return_value=None)
    recorder.repository = MagicMock()
    recorder.repository.find_incomplete_character = MagicMock(
        return_value={
            "row_id": _ROW_ID,
            "entity_id": _ENTITY_ID,
            "workflow_ids": [_WORKFLOW_ID],
            "primary_media_ids": [_MEDIA_ID],
        }
    )

    saga_state["client"] = client
    saga_state["recorder"] = recorder


@when("the create saga runs again for the same project and name")
def _run_saga_resume(saga_state: dict[str, Any]) -> None:
    client = saga_state["client"]
    recorder = saga_state["recorder"]
    face = CharacterImageRequest(prompt="a face", model="nano2")

    saga_state["saga_result"] = asyncio.run(
        character_create(
            client,
            recorder,
            profile_name="default",
            profile_dir=Path("/tmp/profile_default"),
            project_id=_PROJECT_ID,
            name=_NAME,
            face=face,
            body=None,
            voice=None,
            personality=None,
            locale="en-US",
        )
    )


@then("the face image is not generated a second time")
def _face_not_regenerated(saga_state: dict[str, Any]) -> None:
    client = saga_state["client"]
    # Face already recorded => generate_character_image must NOT fire for the face.
    # With a face-only character (body=None) that means ZERO generate calls total.
    assert client.generate_character_image.call_count == 0, (
        "resume re-generated the already-recorded face — a second credit would be spent"
    )
    # create_entity is also skipped (entity_id reused from the recovered row).
    client.create_entity.assert_not_called()


@then("no second credit is spent on the face")
def _no_second_credit(saga_state: dict[str, Any]) -> None:
    client = saga_state["client"]
    # Belt-and-suspenders: the face workflow is never committed again either, and
    # the saga still completes with the recovered face workflow id in the result.
    for c in client.commit_workflow.call_args_list:
        pos = c.args
        assert not (pos and pos[0] == _WORKFLOW_ID), (
            "face workflow was committed a second time on resume"
        )
    result: CharacterCreateResult = saga_state["saga_result"]
    assert result.workflow_ids == (_WORKFLOW_ID,)
