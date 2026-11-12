from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gflow_cli.data.store import DataStore


from gflow_cli.data._repo_assets import AssetsRepo
from gflow_cli.data._repo_base import ProjectWork as ProjectWork
from gflow_cli.data._repo_base import verified_local_path as verified_local_path
from gflow_cli.data._repo_local_files import LocalFilesRepo
from gflow_cli.data._repo_operations import OperationsRepo
from gflow_cli.data._repo_projects import ProjectsRepo
from gflow_cli.data._repo_scenes import ScenesChainsRepo


class DataRepository(ProjectsRepo, AssetsRepo, OperationsRepo, ScenesChainsRepo, LocalFilesRepo):
    def __init__(self, store: DataStore) -> None:
        self._store = store

    @property
    def store(self) -> DataStore:
        return self._store
