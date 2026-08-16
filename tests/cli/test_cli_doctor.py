"""Red spec for the `gflow doctor` CLI surface (#542, Task D1 → D3).

Contract pinned here:

- module ``gflow_cli.cli_doctor`` exposes the command, registered on
  ``gflow_cli.cli.main`` as ``gflow doctor``; it imports ``run_all`` into its
  own namespace (tests monkeypatch ``gflow_cli.cli_doctor.run_all``).
- exit codes: 33 when any warn/fail finding, 0 clean, 16 on DataStoreError.
- ``--json``: parseable envelope with ``overall_status`` + per-check
  ``checks`` entries; marked experimental in ``--help``.
- redaction in EVERY rendered output (text and JSON): row UUIDs instead of
  display-name values, paths through ``gflow_cli._cli_helpers.safe_path_text``,
  profile emails never in plaintext, C0/C1 control chars stripped.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from gflow_cli import cli_doctor
from gflow_cli.cli import main
from gflow_cli.config import reset_settings
from gflow_cli.data.store import DataStore
from gflow_cli.errors import DataStoreError
from gflow_cli.profile_store import ProfileMeta
from tests.fixtures.doctor_env import CHECK_IDS

_NOW_TS = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _seed_db(
    db: Path,
    *,
    display_name: str | None = None,
    missing_file_dir: Path | None = None,
    missing_file_name: str = "gone.png",
    profile_name: str = "default",
) -> str:
    """DB with one asset; optionally a defect (no display_name / missing file)."""
    asset_id = str(uuid.uuid4())
    metadata = json.dumps({"display_name": display_name}) if display_name else None
    with DataStore.open(db) as store:
        store.conn.execute(
            "INSERT INTO profiles(name, first_seen_at) VALUES (?, ?)",
            (profile_name, _NOW_TS),
        )
        store.conn.execute(
            "INSERT INTO assets(id, profile_name, flow_media_id, kind, status, created_at,"
            " metadata_json) VALUES (?, ?, ?, 'image', 'ready', ?, ?)",
            (asset_id, profile_name, str(uuid.uuid4()), _NOW_TS, metadata),
        )
        if missing_file_dir is not None:
            store.conn.execute(
                "INSERT INTO local_files(id, profile_name, asset_id, path, sha256, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    profile_name,
                    asset_id,
                    str(missing_file_dir / missing_file_name),
                    "ab" * 32,
                    _NOW_TS,
                ),
            )
    return asset_id


def _use_db(monkeypatch: pytest.MonkeyPatch, db: Path) -> None:
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    reset_settings()  # never let a cached Settings resolve a stale DB path


# ---------------------------------------------------------------------------
# Exit contract
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("healthy_doctor_env")
def test_doctor_clean_exits_0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "gflow.db"
    _seed_db(db, display_name="fine")
    _use_db(monkeypatch, db)
    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 0, result.output


@pytest.mark.usefixtures("healthy_doctor_env")
def test_doctor_findings_exit_33(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "gflow.db"
    _seed_db(db, display_name=None)  # catalog.display_name_missing
    _use_db(monkeypatch, db)
    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 33, result.output


@pytest.mark.usefixtures("healthy_doctor_env")
def test_doctor_datastore_error_exits_16(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "gflow.db"
    _seed_db(db, display_name="fine")
    _use_db(monkeypatch, db)

    def _boom(*args: object, **kwargs: object) -> object:
        raise DataStoreError("disk on fire")

    monkeypatch.setattr(cli_doctor, "run_all", _boom)
    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 16, result.output


# ---------------------------------------------------------------------------
# --json envelope + --help
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("healthy_doctor_env")
def test_doctor_json_envelope_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "gflow.db"
    _seed_db(db, display_name="fine")
    _use_db(monkeypatch, db)
    result = CliRunner().invoke(main, ["doctor", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["overall_status"] == "ok"
    entries = {entry["check"]: entry for entry in payload["checks"]}
    assert set(entries) == set(CHECK_IDS)
    assert all("severity" in entry for entry in payload["checks"])


@pytest.mark.usefixtures("healthy_doctor_env")
def test_doctor_json_findings_exit_33(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "gflow.db"
    _seed_db(db, display_name=None)
    _use_db(monkeypatch, db)
    result = CliRunner().invoke(main, ["doctor", "--json"])
    assert result.exit_code == 33, result.output
    payload = json.loads(result.output)
    assert payload["overall_status"] == "issues"


def test_doctor_help_marks_json_experimental() -> None:
    result = CliRunner().invoke(main, ["doctor", "--help"])
    assert result.exit_code == 0, result.output
    assert "--json" in result.output
    assert "experimental" in result.output.lower()


# ---------------------------------------------------------------------------
# Redaction in rendered output (text AND json)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("healthy_doctor_env")
@pytest.mark.parametrize("flags", [[], ["--json"]], ids=["text", "json"])
def test_doctor_output_has_uuids_never_display_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flags: list[str],
) -> None:
    db = tmp_path / "gflow.db"
    asset_id = _seed_db(db, display_name="TOP-SECRET-CLIENT-NAME", missing_file_dir=tmp_path)
    _use_db(monkeypatch, db)
    result = CliRunner().invoke(main, ["doctor", *flags])
    assert result.exit_code == 33, result.output
    assert "TOP-SECRET-CLIENT-NAME" not in result.output
    assert asset_id in result.output


@pytest.mark.usefixtures("healthy_doctor_env")
def test_doctor_paths_render_via_safe_path_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every rendered filesystem path must route through safe_path_text.

    Finding texts (paths included) are composed in the SERVICE layer; the
    module-namespace patch of ``gflow_cli._cli_helpers.safe_path_text``
    catches either layer (service or CLI) as long as the caller resolves it
    through the module. Assert the sentinel is what reaches the terminal
    instead of the raw absolute path.
    """
    db = tmp_path / "gflow.db"
    _seed_db(db, display_name="fine", missing_file_dir=tmp_path)
    _use_db(monkeypatch, db)
    monkeypatch.setattr(
        "gflow_cli._cli_helpers.safe_path_text",
        lambda path: f"<SAFE:{Path(path).name}>",
    )
    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 33, result.output
    assert "<SAFE:gone.png>" in result.output
    assert str(tmp_path / "gone.png") not in result.output


@pytest.mark.usefixtures("healthy_doctor_env")
def test_doctor_never_prints_profile_email_plaintext(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "gflow.db"
    _seed_db(db, display_name="fine")
    _use_db(monkeypatch, db)
    monkeypatch.setattr(
        "gflow_cli.profile_store.list_profiles",
        lambda: [
            ProfileMeta(
                name="default",
                profile_dir=tmp_path / "profile_default",
                cookies_present=False,  # forces an auth.files_present finding
                last_used_at=None,
                is_default=True,
                google_account="secret.user@example.com",
            )
        ],
    )
    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 33, result.output
    assert "secret.user@example.com" not in result.output


@pytest.mark.usefixtures("healthy_doctor_env")
def test_doctor_strips_control_chars_from_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "gflow.db"
    # Inject the control chars into a local_files.path filename: the missing
    # file guarantees a catalog.local_file_missing finding that RENDERS the
    # path, so this test cannot pass vacuously on a value doctor never prints.
    _seed_db(
        db,
        display_name="fine",
        missing_file_dir=tmp_path,
        missing_file_name="evil\x1b[31mred\x07name.png",
    )
    _use_db(monkeypatch, db)
    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 33, result.output
    assert "\x1b" not in result.output
    assert "\x07" not in result.output
