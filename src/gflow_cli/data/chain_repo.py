"""Concrete :class:`~gflow_cli.chain.ChainRecorder` backed by the SQLite catalog.

The orchestrator (:func:`gflow_cli.chain.run_chain`) calls ``record_chain_link``
once per link, BEFORE last-frame extraction, so a crash in the download->extract
gap can be resumed at extraction rather than regenerating a paid clip. This
module persists each link into the ``chain_links`` table (migration 0005) and
exposes a resume query the CLI consumes for ``--resume-from`` (Task 8).

Persistence runs AFTER a paid generation. Per the project's post-success rule a
recorder failure must surface clearly (exit 16 / ``DataStoreError``) but never
implies the clip was lost — the orchestrator already holds ``local_path`` on
disk. We therefore re-raise as ``DataStoreError`` and let the caller decide; we
never swallow the failure silently.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from gflow_cli.data.models import ChainLinkRecord
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import DataStoreError

if TYPE_CHECKING:
    from pathlib import Path

    from gflow_cli.chain import ChainLinkResult
    from gflow_cli.config import Settings

_ROUTE = "data.record_chain_link"


def _new_id() -> str:
    return str(uuid.uuid4())


def _now_utc_iso() -> str:
    """UTC timestamp matching the format used elsewhere in the data layer."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ChainLinkRecorder:
    """Persist each completed chain link into the catalog.

    Implements the structural :class:`gflow_cli.chain.ChainRecorder` protocol
    (``record_chain_link(result: ChainLinkResult) -> None``). The orchestrator's
    :class:`~gflow_cli.chain.ChainLinkResult` carries no ``chain_id`` (it is a
    per-link value); the chain identity + owning profile are bound here at
    construction, so one recorder instance serves exactly one chain run.
    """

    repository: DataRepository
    profile_name: str
    profile_dir: Path
    chain_id: str

    def __init__(
        self,
        repository: DataRepository,
        *,
        profile_name: str,
        profile_dir: Path,
        chain_id: str,
    ) -> None:
        self.repository = repository
        self.profile_name = profile_name
        self.profile_dir = profile_dir
        self.chain_id = chain_id

    @classmethod
    def open(
        cls,
        settings: Settings,
        *,
        profile_name: str,
        profile_dir: Path,
        chain_id: str,
    ) -> ChainLinkRecorder:
        store = DataStore.open(settings.resolved_db_path())
        return cls(
            DataRepository(store),
            profile_name=profile_name,
            profile_dir=profile_dir,
            chain_id=chain_id,
        )

    def close(self) -> None:
        self.repository.store.close()

    def record_chain_link(self, result: ChainLinkResult) -> None:
        """Persist one link's downloaded clip (record-before-extract).

        Idempotent on ``(profile, chain_id, link_index)``: a resumed run that
        re-records an already-completed link overwrites in place. Wrapping any
        low-level SQLite failure as :class:`DataStoreError` (exit 16) keeps the
        recorder's failure mode consistent with the rest of the data layer.
        """
        try:
            self.repository.upsert_profile(self.profile_name, self.profile_dir)
            self.repository.upsert_chain_link(
                ChainLinkRecord(
                    id=_new_id(),
                    profile_name=self.profile_name,
                    chain_id=self.chain_id,
                    link_index=result.index,
                    flow_project_id=result.project_id,
                    flow_media_id=result.media_id,
                    flow_operation_id=result.flow_operation_id,
                    prompt=result.prompt,
                    local_path=str(result.local_path),
                    seed_frame_path=(
                        str(result.frame_path) if result.frame_path is not None else None
                    ),
                    created_at=_now_utc_iso(),
                ),
            )
        except DataStoreError:
            raise
        except sqlite3.Error as exc:
            raise DataStoreError(detail=str(exc), route=_ROUTE) from exc

    def completed_links(self) -> list[ChainLinkRecord]:
        """Return this chain's recorded links (ordered) for ``--resume-from``."""
        return self.repository.completed_chain_links(self.profile_name, self.chain_id)
