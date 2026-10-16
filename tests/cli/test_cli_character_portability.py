"""`gflow character export` / `import` wiring (#639). Services are mocked; their own
behaviour is pinned in tests/services/test_character_portability.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from gflow_cli.api.character import CharacterCreateResult
from gflow_cli.character_bundle import BundleImage, CharacterBundle
from gflow_cli.cli_character import character

_M = "gflow_cli.cli_character"


def _env(tmp_path: Path):  # type: ignore[no-untyped-def]
    client_cm = MagicMock()
    client_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    client_cm.__aexit__ = AsyncMock(return_value=False)
    return (
        patch(f"{_M}.get_settings", return_value=MagicMock(headless=True)),
        patch(f"{_M}._resolve_profile", return_value="vraisykamel"),
        patch(f"{_M}._make_provider_dir", return_value=tmp_path / "profile"),
        patch(f"{_M}.FlowApiClient", return_value=client_cm),
        patch(f"{_M}.OperationRecorder.open", return_value=MagicMock()),
    )


def test_export_writes_and_reports_the_bundle(tmp_path: Path) -> None:
    out = tmp_path / "nora"
    bundle = CharacterBundle(
        root=out,
        name="Nora",
        voice="Aoede",
        personality="calm",
        images=(BundleImage("portrait", out / "portrait.jpg", "ab" * 32, "image/jpeg"),),
    )
    a, b, c, d, e = _env(tmp_path)
    with a, b, c, d, e, patch(f"{_M}.export_character", new=AsyncMock(return_value=bundle)) as ex:
        res = CliRunner().invoke(
            character,
            ["export", "--project", "p1", "--name", "Nora", "--out", str(out), "--json"],
        )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["character"] == {"name": "Nora", "voice": "Aoede", "personality": "calm"}
    assert payload["images"][0]["file"] == "portrait.jpg"
    assert ex.await_args.kwargs == {
        "project_id": "p1",
        "out": out,
        "entity_id": None,
        "name": "Nora",
    }


def test_export_needs_exactly_one_of_id_or_name(tmp_path: Path) -> None:
    for args in ([], ["--id", "e1", "--name", "Nora"]):
        res = CliRunner().invoke(
            character, ["export", "--project", "p1", "--out", str(tmp_path / "x"), *args]
        )
        assert res.exit_code == 2, args


def test_import_rebuilds_into_the_named_profile_and_project(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "nora"
    bundle_dir.mkdir()
    result = CharacterCreateResult(
        entity_id="new-1",
        project_id="p2",
        workflow_ids=("w0", "w1"),
        primary_media_ids=("m0",),
        name="Nora",
        voice="Aoede",
        image_paths=(),
    )
    a, b, c, d, e = _env(tmp_path)
    with a, b, c, d, e, patch(f"{_M}.import_character", new=AsyncMock(return_value=result)) as im:
        res = CliRunner().invoke(
            character,
            [
                "import",
                str(bundle_dir),
                "--project",
                "p2",
                "--profile",
                "vraisykamel",
                "--voice",
                "charon",
                "--json",
            ],
        )
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["character"]["entity_id"] == "new-1"
    kwargs = im.await_args.kwargs
    assert (kwargs["bundle_dir"], kwargs["project_id"], kwargs["voice"]) == (
        bundle_dir,
        "p2",
        "charon",
    )
    assert kwargs["profile_name"] == "vraisykamel"


def test_import_of_a_missing_folder_is_a_usage_error(tmp_path: Path) -> None:
    res = CliRunner().invoke(character, ["import", str(tmp_path / "nope"), "--project", "p2"])
    assert res.exit_code == 2
