from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from gflow_cli.cli import main
from gflow_cli.data.models import AssetRecord, LocalFileRecord, ProjectRecord
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore


def _seed_db(db_path: Path, *, media_id: str, profile: str = "default") -> None:
    with DataStore.open(db_path) as store:
        repo = DataRepository(store)
        repo.upsert_profile(profile, db_path.parent / "profile_default")
        repo.upsert_project(
            ProjectRecord(
                id="project-local",
                profile_name=profile,
                flow_project_id="flow-project-1",
                title="title",
                source="generated",
            )
        )
        asset = repo.upsert_asset(
            AssetRecord.minimal_image(
                id="asset-local",
                profile_name=profile,
                flow_project_id="flow-project-1",
                flow_media_id=media_id,
            )
        )
        local_path = db_path.parent / "image.png"
        local_path.write_bytes(b"x")
        repo.upsert_local_file(
            LocalFileRecord(
                id="file-local",
                profile_name=profile,
                asset_id=asset.id,
                path=local_path,
                media_type="image/png",
                bytes=1,
                sha256=None,
            )
        )


def test_data_media_prints_media_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "gflow.db"
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    monkeypatch.setenv("GFLOW_CLI_PROFILE", "default")
    _seed_db(db, media_id="media-image-1")

    from gflow_cli.config import reset_settings

    reset_settings()
    runner = CliRunner()
    result = runner.invoke(main, ["data", "media", "media-image-1", "--profile", "default"])
    assert result.exit_code == 0, result.output
    assert "media-image-1" in result.output
    assert "flow-project-1" in result.output
    assert "image" in result.output  # asset kind


def test_data_media_missing_exits_non_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "gflow.db"
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    monkeypatch.setenv("GFLOW_CLI_PROFILE", "default")
    _seed_db(db, media_id="media-image-1")  # seed one record so the DB exists

    from gflow_cli.config import reset_settings

    reset_settings()
    runner = CliRunner()
    result = runner.invoke(main, ["data", "media", "media-missing", "--profile", "default"])
    assert result.exit_code != 0
    assert "media-missing" in result.output or "not" in result.output.lower()
