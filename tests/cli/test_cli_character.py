"""Tests for `gflow character` CLI group (list / show / voices)."""

from __future__ import annotations

import json

from click.testing import CliRunner

from gflow_cli import cli_character
from gflow_cli.api.character import VOICE_NAMES, VOICES, Character
from gflow_cli.cli import main
from gflow_cli.errors import ConfigurationError

# ---------------------------------------------------------------------------
# Fake helpers
# ---------------------------------------------------------------------------

_CHAR_A = Character(
    entity_id="eid-alpha",
    display_name="Alpha",
    project_id="proj-1",
    workflow_ids=("wf-1", "wf-2"),
    voice="gacrux",
    personality="bold",
    thumbnail_media_id="thumb-1",
)

_CHAR_B = Character(
    entity_id="eid-beta",
    display_name="Beta",
    project_id="proj-1",
    workflow_ids=("wf-3",),
    voice=None,
    personality=None,
    thumbnail_media_id=None,
)


class _FakeClient:
    """Minimal async-CM stub for FlowApiClient covering character methods."""

    def __init__(self, *, list_result=None, get_result=None, get_exc=None, deleted=None, **_kw):
        self._list_result = list_result if list_result is not None else []
        self._get_result = get_result
        self._get_exc = get_exc
        self._deleted = deleted if deleted is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def list_characters(self, project_id: str) -> list[Character]:
        return list(self._list_result)

    async def get_character(
        self,
        project_id: str,
        *,
        entity_id: str | None = None,
        name: str | None = None,
    ) -> Character:
        if self._get_exc is not None:
            raise self._get_exc
        assert self._get_result is not None
        return self._get_result

    async def delete_characters(self, project_id: str, entity_ids: list[str]) -> None:
        self._deleted.append((project_id, list(entity_ids)))


def _patch(monkeypatch, *, list_result=None, get_result=None, get_exc=None, deleted=None):
    """Patch FlowApiClient + profile/settings helpers inside cli_character."""
    monkeypatch.setattr(
        cli_character,
        "FlowApiClient",
        lambda **kw: _FakeClient(
            list_result=list_result,
            get_result=get_result,
            get_exc=get_exc,
            deleted=deleted,
            **kw,
        ),
    )
    monkeypatch.setattr(cli_character, "get_settings", lambda: type("S", (), {"headless": True})())
    # Bypass profile resolution and provider-dir existence check so tests
    # don't need a real on-disk profile (mirrors how test_cli_scene.py works).
    monkeypatch.setattr(cli_character, "_resolve_profile", lambda p: p or "default")
    monkeypatch.setattr(
        cli_character, "_make_provider_dir", lambda name: __import__("pathlib").Path("/fake")
    )


# ---------------------------------------------------------------------------
# character list
# ---------------------------------------------------------------------------


def test_list_prints_character_names(monkeypatch):
    _patch(monkeypatch, list_result=[_CHAR_A, _CHAR_B])
    res = CliRunner().invoke(
        main,
        ["character", "list", "--project", "proj-1"],
        catch_exceptions=False,
    )
    assert res.exit_code == 0
    assert "Alpha" in res.output
    assert "Beta" in res.output


def test_list_json_output(monkeypatch):
    _patch(monkeypatch, list_result=[_CHAR_A])
    res = CliRunner().invoke(
        main,
        ["character", "list", "--project", "proj-1", "--json"],
        catch_exceptions=False,
    )
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["status"] == "ok"
    assert isinstance(data["characters"], list)
    assert len(data["characters"]) == 1
    assert data["characters"][0]["entity_id"] == "eid-alpha"
    assert data["characters"][0]["display_name"] == "Alpha"


def test_list_empty_project(monkeypatch):
    _patch(monkeypatch, list_result=[])
    res = CliRunner().invoke(
        main,
        ["character", "list", "--project", "proj-1"],
        catch_exceptions=False,
    )
    assert res.exit_code == 0
    assert "No characters found" in res.output


def test_list_requires_project():
    res = CliRunner().invoke(main, ["character", "list"])
    assert res.exit_code != 0


# ---------------------------------------------------------------------------
# character show
# ---------------------------------------------------------------------------


def test_show_by_id_happy_path(monkeypatch):
    _patch(monkeypatch, get_result=_CHAR_A)
    res = CliRunner().invoke(
        main,
        ["character", "show", "--project", "proj-1", "--id", "eid-alpha"],
        catch_exceptions=False,
    )
    assert res.exit_code == 0
    assert "eid-alpha" in res.output or "Alpha" in res.output


def test_show_by_name_happy_path(monkeypatch):
    _patch(monkeypatch, get_result=_CHAR_A)
    res = CliRunner().invoke(
        main,
        ["character", "show", "--project", "proj-1", "--name", "Alpha"],
        catch_exceptions=False,
    )
    assert res.exit_code == 0
    assert "Alpha" in res.output


def test_show_json_output(monkeypatch):
    _patch(monkeypatch, get_result=_CHAR_A)
    res = CliRunner().invoke(
        main,
        ["character", "show", "--project", "proj-1", "--id", "eid-alpha", "--json"],
        catch_exceptions=False,
    )
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["status"] == "ok"
    assert data["character"]["entity_id"] == "eid-alpha"
    assert data["character"]["voice"] == "gacrux"


def test_show_ambiguous_name_exits_11(monkeypatch):
    """ConfigurationError (ambiguous name) must map to exit code 11."""
    exc = ConfigurationError(
        detail="ambiguous character name 'Dup' matches multiple entities: eid-1, eid-2",
        route="projectInitialData",
    )
    _patch(monkeypatch, get_exc=exc)
    res = CliRunner().invoke(
        main,
        ["character", "show", "--project", "proj-1", "--name", "Dup"],
    )
    assert res.exit_code == 11


def test_show_not_found_exits_11(monkeypatch):
    """ConfigurationError (not found) must map to exit code 11."""
    exc = ConfigurationError(
        detail="character not found: eid-missing",
        route="projectInitialData",
    )
    _patch(monkeypatch, get_exc=exc)
    res = CliRunner().invoke(
        main,
        ["character", "show", "--project", "proj-1", "--id", "eid-missing"],
    )
    assert res.exit_code == 11


def test_show_requires_id_or_name():
    """--id and --name are both absent → usage error (exit 2) with message."""
    res = CliRunner().invoke(main, ["character", "show", "--project", "proj-1"])
    assert res.exit_code == 2
    assert "--id" in res.output or "--name" in res.output


def test_show_id_and_name_mutually_exclusive():
    """Passing both --id and --name must be rejected with mutual-exclusion message."""
    res = CliRunner().invoke(
        main,
        ["character", "show", "--project", "proj-1", "--id", "x", "--name", "y"],
    )
    assert res.exit_code == 2
    assert "mutually exclusive" in res.output


# ---------------------------------------------------------------------------
# character rm
# ---------------------------------------------------------------------------


def test_rm_by_id_deletes(monkeypatch):
    """--id --yes → resolves the character, deletes it, prints confirmation."""
    deleted: list = []
    _patch(monkeypatch, get_result=_CHAR_A, deleted=deleted)
    res = CliRunner().invoke(
        main,
        ["character", "rm", "--project", "proj-1", "--id", "eid-alpha", "--yes"],
        catch_exceptions=False,
    )
    assert res.exit_code == 0
    assert "Deleted" in res.output
    assert "Alpha" in res.output
    assert deleted == [("proj-1", ["eid-alpha"])]


def test_rm_json_output(monkeypatch):
    """--json emits the deleted entity and skips the interactive confirm."""
    deleted: list = []
    _patch(monkeypatch, get_result=_CHAR_A, deleted=deleted)
    res = CliRunner().invoke(
        main,
        ["character", "rm", "--project", "proj-1", "--id", "eid-alpha", "--json"],
        catch_exceptions=False,
    )
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["status"] == "ok"
    assert data["deleted"]["entity_id"] == "eid-alpha"
    assert deleted == [("proj-1", ["eid-alpha"])]


def test_rm_requires_id_or_name():
    """Neither --id nor --name → usage error (exit 2)."""
    res = CliRunner().invoke(main, ["character", "rm", "--project", "proj-1"])
    assert res.exit_code == 2
    assert "--id" in res.output or "--name" in res.output


def test_rm_id_and_name_mutually_exclusive():
    """Both --id and --name → mutual-exclusion usage error (exit 2)."""
    res = CliRunner().invoke(
        main,
        ["character", "rm", "--project", "proj-1", "--id", "x", "--name", "y"],
    )
    assert res.exit_code == 2
    assert "mutually exclusive" in res.output


def test_rm_by_name_deletes(monkeypatch):
    """--name --yes resolves the character by display name and deletes it."""
    deleted: list = []
    _patch(monkeypatch, get_result=_CHAR_A, deleted=deleted)
    res = CliRunner().invoke(
        main,
        ["character", "rm", "--project", "proj-1", "--name", "Alpha", "--yes"],
        catch_exceptions=False,
    )
    assert res.exit_code == 0
    assert "Deleted" in res.output
    assert "Alpha" in res.output
    assert deleted == [("proj-1", ["eid-alpha"])]


def test_rm_confirm_abort_does_not_delete(monkeypatch):
    """Answering 'n' at the confirm prompt aborts (click.Abort → exit 130) and deletes nothing."""
    deleted: list = []
    _patch(monkeypatch, get_result=_CHAR_A, deleted=deleted)
    res = CliRunner().invoke(
        main,
        ["character", "rm", "--project", "proj-1", "--id", "eid-alpha"],
        input="n\n",
    )
    # run_with_handlers maps click.Abort to 130 (the conventional SIGINT code).
    assert res.exit_code == 130
    assert deleted == []


def test_rm_ambiguous_name_exits_11(monkeypatch):
    """ConfigurationError (ambiguous name) during resolution must map to exit 11."""
    exc = ConfigurationError(
        detail="ambiguous character name 'Dup' matches multiple entities: eid-1, eid-2",
        route="projectInitialData",
    )
    deleted: list = []
    _patch(monkeypatch, get_exc=exc, deleted=deleted)
    res = CliRunner().invoke(
        main,
        ["character", "rm", "--project", "proj-1", "--name", "Dup"],
    )
    assert res.exit_code == 11
    assert deleted == []


# ---------------------------------------------------------------------------
# character voices
# ---------------------------------------------------------------------------


def test_voices_lists_known_presets():
    res = CliRunner().invoke(main, ["character", "voices"])
    assert res.exit_code == 0
    # Check a few known canonical (Capitalized) voice names
    assert "Gacrux" in res.output
    assert "Aoede" in res.output
    # Human output includes the descriptor and sample URL
    assert "sample:" in res.output
    assert "https://gstatic.com/aitestkitchen/voices/samples/" in res.output


def test_voices_json_parses_and_contains_known_presets():
    res = CliRunner().invoke(main, ["character", "voices", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["status"] == "ok"
    assert isinstance(data["voices"], list)
    assert len(data["voices"]) == 29
    names = {v["name"] for v in data["voices"]}
    assert "Gacrux" in names
    assert "Zephyr" in names
    # Each entry carries name / description / sample_url
    first = data["voices"][0]
    assert set(first.keys()) == {"name", "description", "sample_url"}
    assert first["sample_url"].endswith(f"/{first['name']}.wav")


def test_voices_json_matches_voices_constant():
    res = CliRunner().invoke(main, ["character", "voices", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert tuple(v["name"] for v in data["voices"]) == VOICE_NAMES
    assert len(data["voices"]) == len(VOICES)


# ---------------------------------------------------------------------------
# Group registration
# ---------------------------------------------------------------------------


def test_character_group_registered():
    res = CliRunner().invoke(main, ["character", "--help"])
    assert res.exit_code == 0
    assert "list" in res.output
    assert "show" in res.output
    assert "voices" in res.output
