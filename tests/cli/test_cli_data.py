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


# ---------------------------------------------------------------------------
# #87 — data media cross-profile lookup default
# ---------------------------------------------------------------------------


def _seed_extra_profile(db_path: Path, *, profile: str, media_id: str, asset_id: str) -> None:
    """Append a second profile + asset to an already-initialised DB."""
    with DataStore.open(db_path) as store:
        repo = DataRepository(store)
        repo.upsert_profile(profile, db_path.parent / f"profile_{profile}")
        repo.upsert_project(
            ProjectRecord(
                id=f"project-{profile}",
                profile_name=profile,
                flow_project_id=f"flow-project-{profile}",
                title=f"title-{profile}",
                source="generated",
            )
        )
        repo.upsert_asset(
            AssetRecord.minimal_image(
                id=asset_id,
                profile_name=profile,
                flow_project_id=f"flow-project-{profile}",
                flow_media_id=media_id,
            )
        )


def test_data_media_finds_match_cross_profile_when_profile_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closes #87 — a media_id present in `data list` must be findable by
    `data media <id>` without passing `--profile`, even when the row's
    profile is not the active default."""
    db = tmp_path / "gflow.db"
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    # Seed under profile "default" but make the active profile something else
    # to force the cross-profile path — this is the original #87 repro shape.
    monkeypatch.setenv("GFLOW_CLI_PROFILE", "other")
    _seed_db(db, media_id="cross-profile-media", profile="default")

    from gflow_cli.config import reset_settings

    reset_settings()
    runner = CliRunner()
    result = runner.invoke(main, ["data", "media", "cross-profile-media"])
    assert result.exit_code == 0, result.output
    assert "cross-profile-media" in result.output
    assert "default" in result.output  # the row's profile is printed


def test_data_media_disambiguates_when_multiple_profiles_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two profiles each owning the same flow_media_id → require --profile."""
    db = tmp_path / "gflow.db"
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    monkeypatch.setenv("GFLOW_CLI_PROFILE", "default")
    _seed_db(db, media_id="shared-media", profile="default")
    _seed_extra_profile(db, profile="alice", media_id="shared-media", asset_id="asset-alice")

    from gflow_cli.config import reset_settings

    reset_settings()
    runner = CliRunner()
    result = runner.invoke(main, ["data", "media", "shared-media"])
    assert result.exit_code != 0, result.output
    out = result.output.lower()
    assert "multiple profiles" in out
    assert "--profile" in result.output  # exact case for the flag hint


def test_data_media_profile_flag_still_scopes_strictly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With --profile passed, lookup must scope to that profile and report
    not-found if the row lives under a different profile."""
    db = tmp_path / "gflow.db"
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    monkeypatch.setenv("GFLOW_CLI_PROFILE", "default")
    _seed_db(db, media_id="row-under-default", profile="default")

    from gflow_cli.config import reset_settings

    reset_settings()
    runner = CliRunner()
    result = runner.invoke(main, ["data", "media", "row-under-default", "--profile", "alice"])
    assert result.exit_code != 0
    assert "row-under-default" in result.output
    assert "alice" in result.output  # the scoped profile name appears in the error
