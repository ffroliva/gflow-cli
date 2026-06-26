from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from gflow_cli.errors import DataIntegrityError

if TYPE_CHECKING:
    from gflow_cli.data.store import DataStore


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class QueueTask:
    task_id: str
    profile_name: str
    task_type: str
    payload: dict[str, Any]
    status: str
    flow_media_id: str | None = None
    error: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None


class QueueRepository:
    def __init__(self, store: DataStore) -> None:
        self._store = store

    @property
    def store(self) -> DataStore:
        return self._store

    def enqueue_task(
        self,
        task_id: str,
        profile_name: str,
        task_type: str,
        payload: dict[str, Any],
    ) -> QueueTask:
        now = _utc_now()
        payload_str = json.dumps(payload)
        try:
            with self._store.transaction(immediate=True):
                self._store.conn.execute(
                    """
                    INSERT INTO generation_queue(
                        task_id, profile_name, task_type,
                        payload_json, status, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (task_id, profile_name, task_type, payload_str, "pending", now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise DataIntegrityError(detail=str(exc), route="queue.enqueue_task") from exc

        return QueueTask(
            task_id=task_id,
            profile_name=profile_name,
            task_type=task_type,
            payload=payload,
            status="pending",
            created_at=now,
            updated_at=now,
        )

    def get_task(self, task_id: str) -> QueueTask | None:
        row = self._store.conn.execute(
            """
            SELECT
                task_id, profile_name, task_type, payload_json,
                status, flow_media_id, error_json, created_at, updated_at
            FROM generation_queue
            WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()

        if row is None:
            return None

        return QueueTask(
            task_id=row["task_id"],
            profile_name=row["profile_name"],
            task_type=row["task_type"],
            payload=json.loads(row["payload_json"]),
            status=row["status"],
            flow_media_id=row["flow_media_id"],
            error=json.loads(row["error_json"]) if row["error_json"] else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_next_pending_task(self, profile_name: str) -> QueueTask | None:
        # Pulls the oldest pending task for the given profile
        row = self._store.conn.execute(
            """
            SELECT
                task_id, profile_name, task_type, payload_json,
                status, flow_media_id, error_json, created_at, updated_at
            FROM generation_queue
            WHERE profile_name = ? AND status = 'pending'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (profile_name,),
        ).fetchone()

        if row is None:
            return None

        return QueueTask(
            task_id=row["task_id"],
            profile_name=row["profile_name"],
            task_type=row["task_type"],
            payload=json.loads(row["payload_json"]),
            status=row["status"],
            flow_media_id=row["flow_media_id"],
            error=json.loads(row["error_json"]) if row["error_json"] else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def update_task_status(
        self,
        task_id: str,
        status: str,
        flow_media_id: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        now = _utc_now()
        error_str = json.dumps(error) if error is not None else None
        try:
            with self._store.transaction(immediate=True):
                self._store.conn.execute(
                    """
                    UPDATE generation_queue
                    SET status = ?,
                        flow_media_id = COALESCE(?, flow_media_id),
                        error_json = COALESCE(?, error_json),
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (status, flow_media_id, error_str, now, task_id),
                )
        except sqlite3.IntegrityError as exc:
            raise DataIntegrityError(detail=str(exc), route="queue.update_task_status") from exc

    def fail_processing_tasks(self, profile_name: str, error_message: str) -> int:
        now = _utc_now()
        error_payload = {
            "type": "https://gflow-cli.dev/errors/daemon-recovery",
            "title": "Daemon Boot Recovery",
            "status": 500,
            "detail": error_message,
        }
        error_str = json.dumps(error_payload)
        try:
            with self._store.transaction(immediate=True):
                cursor = self._store.conn.execute(
                    """
                    UPDATE generation_queue
                    SET status = 'failed',
                        error_json = ?,
                        updated_at = ?
                    WHERE profile_name = ? AND status = 'processing'
                    """,
                    (error_str, now, profile_name),
                )
                return cursor.rowcount
        except sqlite3.IntegrityError as exc:
            raise DataIntegrityError(detail=str(exc), route="queue.fail_processing_tasks") from exc
