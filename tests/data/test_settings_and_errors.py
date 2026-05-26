from pathlib import Path

import pytest

from gflow_cli.config import Settings
from gflow_cli.errors import (
    EXIT_CODE_MAP,
    DataIntegrityError,
    DataMigrationError,
    DataStoreError,
    GFlowError,
)
from gflow_cli.paths import database_path


def test_database_path_defaults_under_home(tmp_path: Path) -> None:
    assert database_path(tmp_path) == tmp_path / "gflow.db"


def test_settings_resolves_default_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GFLOW_CLI_DB_PATH", raising=False)
    settings = Settings(home=tmp_path)
    assert settings.resolved_db_path() == tmp_path / "gflow.db"


def test_settings_respects_db_path_override(tmp_path: Path) -> None:
    override = tmp_path / "custom.db"
    settings = Settings(home=tmp_path, db_path=override)
    assert settings.resolved_db_path() == override


def test_settings_accepts_prompt_redaction_modes(tmp_path: Path) -> None:
    assert Settings(home=tmp_path, history_prompts="store").history_prompts == "store"
    assert Settings(home=tmp_path, history_prompts="redacted").history_prompts == "redacted"


def test_data_errors_extend_gflow_error() -> None:
    assert issubclass(DataStoreError, GFlowError)
    assert issubclass(DataMigrationError, DataStoreError)
    assert issubclass(DataIntegrityError, DataStoreError)


@pytest.mark.parametrize(
    "exc_type",
    [DataStoreError, DataMigrationError, DataIntegrityError],
)
def test_data_errors_use_exit_code_16(exc_type: type[DataStoreError]) -> None:
    assert next(code for cls, code in EXIT_CODE_MAP.items() if issubclass(exc_type, cls)) == 16
