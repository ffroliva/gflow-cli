"""Tests for `gflow character create` CLI command.

Scenario coverage:
  #18  accented --personality / --face-prompt round-trip on Windows
       (NOTE: on Windows, set PYTHONUTF8=1 in the environment so Click's
        stdin/stdout codec handles non-ASCII without mojibake)
  #21  headless/non-Chrome profile → non-zero exit with clear error

All tests use Click's CliRunner + mock the saga service so no real API calls
are made.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from gflow_cli.api.character import CharacterCreateResult, CharacterImageRequest
from gflow_cli.cli_character import character
from gflow_cli.errors import ConfigurationError, WireFormatError

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_result(
    entity_id: str = "entity-abc",
    project_id: str = "proj-1",
    name: str = "Knight",
    workflow_ids: tuple[str, ...] = ("wf-face-1",),
    primary_media_ids: tuple[str, ...] = ("media-face-1",),
    voice: str | None = None,
    image_paths: tuple[str | None, ...] = (),
) -> CharacterCreateResult:
    return CharacterCreateResult(
        entity_id=entity_id,
        project_id=project_id,
        name=name,
        workflow_ids=workflow_ids,
        primary_media_ids=primary_media_ids,
        voice=voice,
        image_paths=image_paths,
    )


# ---------------------------------------------------------------------------
# Helpers to patch the relevant surface in cli_character
# ---------------------------------------------------------------------------

_SAGA = "gflow_cli.cli_character.character_create"
_RECORDER_OPEN = "gflow_cli.cli_character.OperationRecorder.open"
_CLIENT = "gflow_cli.cli_character.FlowApiClient"
_RESOLVE_PROFILE = "gflow_cli.cli_character._resolve_profile"
_MAKE_PROVIDER_DIR = "gflow_cli.cli_character._make_provider_dir"
_GET_SETTINGS = "gflow_cli.cli_character.get_settings"


# ---------------------------------------------------------------------------
# Happy path — face only, human output
# ---------------------------------------------------------------------------


def test_create_happy_face_only(tmp_path: Path) -> None:
    """Basic create with face prompt only exits 0 and prints entity_id + workflow."""
    result = _make_result()
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
        patch(_GET_SETTINGS, return_value=mock_settings),
        patch(_RESOLVE_PROFILE, return_value="default"),
        patch(_MAKE_PROVIDER_DIR, return_value=tmp_path / "profiles" / "default"),
        patch(_CLIENT, return_value=mock_client_cm),
        patch(_RECORDER_OPEN, return_value=mock_recorder),
        patch(_SAGA, new=AsyncMock(return_value=result)) as mock_saga,
    ):
        runner = CliRunner()
        cli_result = runner.invoke(
            character,
            ["create", "--project", "proj-1", "--name", "Knight", "--face-prompt", "knight"],
            catch_exceptions=False,
        )

    assert cli_result.exit_code == 0, cli_result.output
    assert "entity-abc" in cli_result.output
    assert "wf-face-1" in cli_result.output

    # Verify saga received the correct face CharacterImageRequest
    call_kwargs = mock_saga.call_args.kwargs
    assert isinstance(call_kwargs["face"], CharacterImageRequest)
    assert call_kwargs["face"].prompt == "knight"
    assert call_kwargs["face"].image_reference_index == 0
    # Default model alias is nano2 (Nano Banana 2).
    assert call_kwargs["face"].model == "nano2"
    assert call_kwargs["body"] is None
    assert call_kwargs["project_id"] == "proj-1"
    assert call_kwargs["name"] == "Knight"


# ---------------------------------------------------------------------------
# Happy path — JSON output
# ---------------------------------------------------------------------------


def test_create_json_output(tmp_path: Path) -> None:
    """--json flag emits parseable {'status':'ok','character':{...}}."""
    result = _make_result()
    mock_settings = MagicMock()
    mock_settings.headless = True
    mock_settings.resolved_db_path.return_value = tmp_path / "gflow.db"
    mock_settings.history_prompts = "hash"

    mock_recorder = MagicMock()
    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(_GET_SETTINGS, return_value=mock_settings),
        patch(_RESOLVE_PROFILE, return_value="default"),
        patch(_MAKE_PROVIDER_DIR, return_value=tmp_path / "profiles" / "default"),
        patch(_CLIENT, return_value=mock_client_cm),
        patch(_RECORDER_OPEN, return_value=mock_recorder),
        patch(_SAGA, new=AsyncMock(return_value=result)),
    ):
        runner = CliRunner()
        cli_result = runner.invoke(
            character,
            [
                "create",
                "--project",
                "proj-1",
                "--name",
                "Knight",
                "--face-prompt",
                "knight",
                "--json",
            ],
            catch_exceptions=False,
        )

    assert cli_result.exit_code == 0, cli_result.output
    parsed = json.loads(cli_result.output)
    assert parsed["status"] == "ok"
    char = parsed["character"]
    assert char["entity_id"] == "entity-abc"
    assert char["workflow_ids"] == ["wf-face-1"]
    assert char["primary_media_ids"] == ["media-face-1"]


# ---------------------------------------------------------------------------
# Saved image paths — printed in human output AND emitted in --json
# ---------------------------------------------------------------------------


def _invoke_create_with_paths(
    tmp_path: Path,
    image_paths: tuple[str | None, ...],
    *,
    extra_args: list[str] | None = None,
) -> str:
    """Run `character create` with a stubbed saga result carrying image_paths.

    Returns the CLI output.
    """
    result = _make_result(
        workflow_ids=("wf-face-1", "wf-body-1"),
        primary_media_ids=("media-face-1", "media-body-1"),
        image_paths=image_paths,
    )
    mock_settings = MagicMock()
    mock_settings.headless = True
    mock_settings.resolved_db_path.return_value = tmp_path / "gflow.db"
    mock_settings.history_prompts = "hash"

    mock_recorder = MagicMock()
    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(_GET_SETTINGS, return_value=mock_settings),
        patch(_RESOLVE_PROFILE, return_value="default"),
        patch(_MAKE_PROVIDER_DIR, return_value=tmp_path / "profiles" / "default"),
        patch(_CLIENT, return_value=mock_client_cm),
        patch(_RECORDER_OPEN, return_value=mock_recorder),
        patch(_SAGA, new=AsyncMock(return_value=result)),
    ):
        runner = CliRunner()
        cli_result = runner.invoke(
            character,
            [
                "create",
                "--project",
                "proj-1",
                "--name",
                "Knight",
                "--face-prompt",
                "knight face",
                *(extra_args or []),
            ],
            catch_exceptions=False,
        )
    assert cli_result.exit_code == 0, cli_result.output
    return cli_result.output


def test_create_prints_saved_image_paths_human(tmp_path: Path) -> None:
    """Human output shows `face: <path>` and `body: <path>` for saved images."""
    face_p = str(tmp_path / "characters" / "character_e_slot0.png")
    body_p = str(tmp_path / "characters" / "character_e_slot1.png")
    output = _invoke_create_with_paths(tmp_path, (face_p, body_p))
    assert "face:" in output
    assert "body:" in output
    # Rich may soft-wrap long paths across lines; collapse whitespace before
    # matching so the assertion is independent of terminal width.
    collapsed = "".join(output.split())
    assert "".join(face_p.split()) in collapsed
    assert "".join(body_p.split()) in collapsed


def test_create_json_includes_image_paths(tmp_path: Path) -> None:
    """--json emits an `image_paths` array (local paths only)."""
    face_p = str(tmp_path / "characters" / "character_e_slot0.png")
    output = _invoke_create_with_paths(tmp_path, (face_p, None), extra_args=["--json"])
    parsed = json.loads(output)
    assert parsed["character"]["image_paths"] == [face_p, None]


# ---------------------------------------------------------------------------
# Body prompt — saga receives non-None body CharacterImageRequest
# ---------------------------------------------------------------------------


def test_create_with_body_prompt(tmp_path: Path) -> None:
    """--body-prompt passes a non-None body CharacterImageRequest to the saga."""
    result = _make_result(workflow_ids=("wf-face-1", "wf-body-1"))
    mock_settings = MagicMock()
    mock_settings.headless = True
    mock_settings.resolved_db_path.return_value = tmp_path / "gflow.db"
    mock_settings.history_prompts = "hash"

    mock_recorder = MagicMock()
    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(_GET_SETTINGS, return_value=mock_settings),
        patch(_RESOLVE_PROFILE, return_value="default"),
        patch(_MAKE_PROVIDER_DIR, return_value=tmp_path / "profiles" / "default"),
        patch(_CLIENT, return_value=mock_client_cm),
        patch(_RECORDER_OPEN, return_value=mock_recorder),
        patch(_SAGA, new=AsyncMock(return_value=result)) as mock_saga,
    ):
        runner = CliRunner()
        cli_result = runner.invoke(
            character,
            [
                "create",
                "--project",
                "proj-1",
                "--name",
                "Knight",
                "--face-prompt",
                "knight face",
                "--body-prompt",
                "full body",
            ],
            catch_exceptions=False,
        )

    assert cli_result.exit_code == 0, cli_result.output
    call_kwargs = mock_saga.call_args.kwargs
    assert call_kwargs["body"] is not None
    assert isinstance(call_kwargs["body"], CharacterImageRequest)
    assert call_kwargs["body"].prompt == "full body"


# ---------------------------------------------------------------------------
# Scenario #18 — accented unicode round-trip (Windows PYTHONUTF8=1 note)
# ---------------------------------------------------------------------------


def test_create_accented_personality_and_face_prompt(tmp_path: Path) -> None:
    """Scenario #18: accented --personality and --face-prompt reach saga intact.

    On Windows, the PYTHONUTF8=1 env var must be set so Click's stdin/stdout
    codec handles non-ASCII without mojibake.  CliRunner uses in-process
    strings so the codec issue doesn't surface here, but the intent is that
    the CLI passes the unicode value through without modification.
    """
    accented_personality = "Café à façade"
    accented_face_prompt = "guerrière élégante"

    result = _make_result()
    mock_settings = MagicMock()
    mock_settings.headless = True
    mock_settings.resolved_db_path.return_value = tmp_path / "gflow.db"
    mock_settings.history_prompts = "hash"

    mock_recorder = MagicMock()
    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(_GET_SETTINGS, return_value=mock_settings),
        patch(_RESOLVE_PROFILE, return_value="default"),
        patch(_MAKE_PROVIDER_DIR, return_value=tmp_path / "profiles" / "default"),
        patch(_CLIENT, return_value=mock_client_cm),
        patch(_RECORDER_OPEN, return_value=mock_recorder),
        patch(_SAGA, new=AsyncMock(return_value=result)) as mock_saga,
    ):
        runner = CliRunner()
        cli_result = runner.invoke(
            character,
            [
                "create",
                "--project",
                "proj-1",
                "--name",
                "Guerrière",
                "--face-prompt",
                accented_face_prompt,
                "--personality",
                accented_personality,
            ],
            catch_exceptions=False,
        )

    assert cli_result.exit_code == 0, cli_result.output
    call_kwargs = mock_saga.call_args.kwargs
    # Exact unicode values must reach the saga unchanged
    assert call_kwargs["face"].prompt == accented_face_prompt
    assert call_kwargs["personality"] == accented_personality


# ---------------------------------------------------------------------------
# Scenario #21 — headless/profile error → non-zero exit
# ---------------------------------------------------------------------------


def test_create_configuration_error_exits_nonzero(tmp_path: Path) -> None:
    """Scenario #21: ConfigurationError from saga maps to non-zero exit code (11)."""
    mock_settings = MagicMock()
    mock_settings.headless = True
    mock_settings.resolved_db_path.return_value = tmp_path / "gflow.db"
    mock_settings.history_prompts = "hash"

    mock_recorder = MagicMock()
    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    err = ConfigurationError("No headless-compatible Chrome profile found")

    with (
        patch(_GET_SETTINGS, return_value=mock_settings),
        patch(_RESOLVE_PROFILE, return_value="default"),
        patch(_MAKE_PROVIDER_DIR, return_value=tmp_path / "profiles" / "default"),
        patch(_CLIENT, return_value=mock_client_cm),
        patch(_RECORDER_OPEN, return_value=mock_recorder),
        patch(_SAGA, new=AsyncMock(side_effect=err)),
    ):
        runner = CliRunner()
        cli_result = runner.invoke(
            character,
            ["create", "--project", "proj-1", "--name", "X", "--face-prompt", "test"],
        )

    assert cli_result.exit_code != 0
    # ConfigurationError maps to exit code 11 per EXIT_CODE_MAP
    assert cli_result.exit_code == 11


def test_create_wire_format_error_exits_nonzero(tmp_path: Path) -> None:
    """WireFormatError from saga maps to exit code 7."""
    mock_settings = MagicMock()
    mock_settings.headless = True
    mock_settings.resolved_db_path.return_value = tmp_path / "gflow.db"
    mock_settings.history_prompts = "hash"

    mock_recorder = MagicMock()
    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    err = WireFormatError("parentEntityId mismatch")

    with (
        patch(_GET_SETTINGS, return_value=mock_settings),
        patch(_RESOLVE_PROFILE, return_value="default"),
        patch(_MAKE_PROVIDER_DIR, return_value=tmp_path / "profiles" / "default"),
        patch(_CLIENT, return_value=mock_client_cm),
        patch(_RECORDER_OPEN, return_value=mock_recorder),
        patch(_SAGA, new=AsyncMock(side_effect=err)),
    ):
        runner = CliRunner()
        cli_result = runner.invoke(
            character,
            ["create", "--project", "proj-1", "--name", "X", "--face-prompt", "test"],
        )

    assert cli_result.exit_code != 0
    assert cli_result.exit_code == 7


# ---------------------------------------------------------------------------
# Voice option reaches the saga
# ---------------------------------------------------------------------------


def _invoke_with_voice(tmp_path: Path, voice_arg: str) -> tuple[int, str, object]:
    """Run create with the given --voice; return (exit_code, output, voice kwarg)."""
    result = _make_result(voice="Gacrux")
    mock_settings = MagicMock()
    mock_settings.headless = True
    mock_settings.resolved_db_path.return_value = tmp_path / "gflow.db"
    mock_settings.history_prompts = "hash"

    mock_recorder = MagicMock()
    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(_GET_SETTINGS, return_value=mock_settings),
        patch(_RESOLVE_PROFILE, return_value="default"),
        patch(_MAKE_PROVIDER_DIR, return_value=tmp_path / "profiles" / "default"),
        patch(_CLIENT, return_value=mock_client_cm),
        patch(_RECORDER_OPEN, return_value=mock_recorder),
        patch(_SAGA, new=AsyncMock(return_value=result)) as mock_saga,
    ):
        runner = CliRunner()
        cli_result = runner.invoke(
            character,
            [
                "create",
                "--project",
                "proj-1",
                "--name",
                "Knight",
                "--face-prompt",
                "knight",
                "--voice",
                voice_arg,
            ],
        )
    voice_kwarg = mock_saga.call_args.kwargs["voice"] if mock_saga.call_args is not None else None
    return cli_result.exit_code, cli_result.output, voice_kwarg


def test_create_voice_passed_to_saga(tmp_path: Path) -> None:
    """--voice Charon (canonical) is forwarded to character_create unchanged."""
    code, _out, voice = _invoke_with_voice(tmp_path, "Charon")
    assert code == 0
    assert voice == "Charon"


def test_create_voice_normalizes_lowercase(tmp_path: Path) -> None:
    """--voice charon (lowercase) normalizes to the canonical 'Charon'."""
    code, _out, voice = _invoke_with_voice(tmp_path, "charon")
    assert code == 0
    assert voice == "Charon"


def test_create_voice_case_insensitive_mixed(tmp_path: Path) -> None:
    """--voice cHaRoN (mixed case) normalizes to 'Charon'."""
    code, _out, voice = _invoke_with_voice(tmp_path, "cHaRoN")
    assert code == 0
    assert voice == "Charon"


def test_create_unknown_voice_rejected(tmp_path: Path) -> None:
    """An unknown voice fails with a non-zero exit and a helpful message."""
    code, out, _voice = _invoke_with_voice(tmp_path, "notavoice")
    assert code != 0
    # Language-agnostic guidance points at the voices command
    assert "gflow character voices" in out


# ---------------------------------------------------------------------------
# Missing required options → exit 2 (Click usage error)
# ---------------------------------------------------------------------------


def test_create_missing_required_options() -> None:
    """Missing --project, --name, or --face-prompt each cause exit code 2."""
    runner = CliRunner()

    # Missing --project
    r1 = runner.invoke(character, ["create", "--name", "X", "--face-prompt", "p"])
    assert r1.exit_code == 2

    # Missing --name
    r2 = runner.invoke(character, ["create", "--project", "P", "--face-prompt", "p"])
    assert r2.exit_code == 2

    # Missing --face-prompt
    r3 = runner.invoke(character, ["create", "--project", "P", "--name", "X"])
    assert r3.exit_code == 2


# ---------------------------------------------------------------------------
# Model / aspect surface — characters have no ratio; model is a 2-value Choice
# ---------------------------------------------------------------------------


def test_create_aspect_option_removed() -> None:
    """--aspect no longer exists — passing it is a Click usage error (exit 2)."""
    runner = CliRunner()
    r = runner.invoke(
        character,
        [
            "create",
            "--project",
            "P",
            "--name",
            "X",
            "--face-prompt",
            "p",
            "--aspect",
            "9:16",
        ],
    )
    assert r.exit_code == 2
    assert "no such option" in r.output.lower()


def test_create_model_choice_rejects_invalid() -> None:
    """--model only accepts nano2 / nanopro; anything else is exit 2."""
    runner = CliRunner()
    r = runner.invoke(
        character,
        [
            "create",
            "--project",
            "P",
            "--name",
            "X",
            "--face-prompt",
            "p",
            "--model",
            "narwhal",
        ],
    )
    assert r.exit_code == 2
    assert "invalid value" in r.output.lower() or "is not one of" in r.output.lower()


def _invoke_with_model(tmp_path: Path, model_arg: str) -> tuple[int, CharacterImageRequest]:
    """Run create with the given --model and return (exit_code, face request)."""
    result = _make_result()
    mock_settings = MagicMock()
    mock_settings.headless = True
    mock_settings.resolved_db_path.return_value = tmp_path / "gflow.db"
    mock_settings.history_prompts = "hash"

    mock_recorder = MagicMock()
    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(_GET_SETTINGS, return_value=mock_settings),
        patch(_RESOLVE_PROFILE, return_value="default"),
        patch(_MAKE_PROVIDER_DIR, return_value=tmp_path / "profiles" / "default"),
        patch(_CLIENT, return_value=mock_client_cm),
        patch(_RECORDER_OPEN, return_value=mock_recorder),
        patch(_SAGA, new=AsyncMock(return_value=result)) as mock_saga,
    ):
        runner = CliRunner()
        cli_result = runner.invoke(
            character,
            [
                "create",
                "--project",
                "proj-1",
                "--name",
                "Knight",
                "--face-prompt",
                "knight",
                "--model",
                model_arg,
            ],
            catch_exceptions=False,
        )
    return cli_result.exit_code, mock_saga.call_args.kwargs["face"]


def test_create_model_nano2_reaches_saga(tmp_path: Path) -> None:
    code, face = _invoke_with_model(tmp_path, "nano2")
    assert code == 0
    assert face.model == "nano2"


def test_create_model_nanopro_reaches_saga(tmp_path: Path) -> None:
    code, face = _invoke_with_model(tmp_path, "nanopro")
    assert code == 0
    assert face.model == "nanopro"
