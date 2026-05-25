import subprocess
import tarfile
import zipfile
from importlib import resources
from pathlib import Path

import pytest


def test_migration_resource_is_discoverable() -> None:
    migration = resources.files("gflow_cli.data.migrations").joinpath("0001_initial.sql")
    assert migration.is_file()


@pytest.mark.integration
def test_built_distributions_contain_sql_migrations(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "--sdist", "--out-dir", str(dist_dir)],
        check=True,
    )
    wheel = next(dist_dir.glob("gflow_cli-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert "gflow_cli/data/migrations/0001_initial.sql" in archive.namelist()
    sdist = next(dist_dir.glob("gflow_cli-*.tar.gz"))
    with tarfile.open(sdist) as archive:
        assert any(
            name.endswith("src/gflow_cli/data/migrations/0001_initial.sql")
            for name in archive.getnames()
        )
