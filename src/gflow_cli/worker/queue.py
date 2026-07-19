from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from gflow_cli.errors import DataIntegrityError, QueueSchemaError
from gflow_cli.worker import codec

if TYPE_CHECKING:
    from collections.abc import Sequence

    from gflow_cli.data.store import DataStore

# Version of the persisted checkpoint document (its own small versioned dict,
# deliberately NOT merged with the api.dto GenerationCheckpoint DTO).
CHECKPOINT_SCHEMA_VERSION = 1

# Columns selected to build a QueueTask — one list, one row->task mapping.
_TASK_COLUMNS = (
    "task_id, profile_name, task_type, payload_json, status, flow_media_id, "
    "error_json, claimant, claimed_at, checkpoint_json, created_at, updated_at"
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def make_checkpoint_document(
    *,
    claimant: str,
    phase: str,
    may_have_spent: bool,
    project_id: str | None,
    operation_id: str | None = None,
    media_ids: Sequence[str] = (),
    workflow_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a versioned, redacted checkpoint document for a queue task.

    Carries ONLY claimant identity, execution phase, a spend flag, the
    credit-free-recovery ``project_id`` (design-spec Appendix A / C1 spike),
    and observed Flow handles. It MUST NEVER carry prompt text, credentials,
    cookies, or signed URLs — the shape here is the allow-list.
    """
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "claimant": claimant,
        "phase": phase,
        "may_have_spent": may_have_spent,
        "project_id": project_id,
        "operation_id": operation_id,
        "media_ids": list(media_ids),
        "workflow_ids": list(workflow_ids),
    }


@dataclass(frozen=True)
class QueueTask:
    task_id: str
    profile_name: str
    task_type: str
    payload: dict[str, Any]
    status: str
    flow_media_id: str | None = None
    error: dict[str, Any] | None = None
    claimant: str | None = None
    claimed_at: str | None = None
    checkpoint: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None
    # Transient (never persisted): the validated request from the claim-time
    # decode, threaded to the executor so it does not re-derive the payload
    # mapping post-claim. Populated ONLY by the claim path; None everywhere else.
    decoded: codec.DecodedPayload | None = None


def _row_to_task(row: sqlite3.Row, decoded: codec.DecodedPayload | None = None) -> QueueTask:
    return QueueTask(
        task_id=row["task_id"],
        profile_name=row["profile_name"],
        task_type=row["task_type"],
        payload=json.loads(row["payload_json"]),
        status=row["status"],
        flow_media_id=row["flow_media_id"],
        error=json.loads(row["error_json"]) if row["error_json"] else None,
        claimant=row["claimant"],
        claimed_at=row["claimed_at"],
        checkpoint=json.loads(row["checkpoint_json"]) if row["checkpoint_json"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        decoded=decoded,
    )


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
            f"SELECT {_TASK_COLUMNS} FROM generation_queue WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return _row_to_task(row) if row is not None else None

    def get_next_pending_task(self, profile_name: str) -> QueueTask | None:
        # Pulls the oldest pending task for the given profile
        row = self._store.conn.execute(
            f"""
            SELECT {_TASK_COLUMNS} FROM generation_queue
            WHERE profile_name = ? AND status = 'pending'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (profile_name,),
        ).fetchone()
        return _row_to_task(row) if row is not None else None

    def claim_next_pending(self, profile_name: str, claimant: str) -> QueueTask | None:
        """Atomically claim the oldest pending task for a profile.

        Holds a single ``BEGIN IMMEDIATE`` write transaction across: select
        the oldest pending row, decode+validate its payload (C2 codec), fail
        an invalid row WITHOUT opening a browser, or conditionally transition
        exactly that row ``pending`` -> ``processing`` with claimant metadata.
        Returns the claimed task, or ``None`` when the queue is empty or the
        row was rejected as invalid.
        """
        with self._store.transaction(immediate=True):
            row = self._store.conn.execute(
                f"""
                SELECT {_TASK_COLUMNS} FROM generation_queue
                WHERE profile_name = ? AND status = 'pending'
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (profile_name,),
            ).fetchone()
            if row is None:
                return None
            return self._claim_row(row, claimant)

    def claim_task(self, task_id: str, claimant: str) -> QueueTask | None:
        """Atomically claim one specific task by id — same transaction and
        decode-at-claim semantics as :meth:`claim_next_pending`. Returns
        ``None`` if the task is missing, not pending, or invalid."""
        with self._store.transaction(immediate=True):
            row = self._store.conn.execute(
                f"SELECT {_TASK_COLUMNS} FROM generation_queue "
                "WHERE task_id = ? AND status = 'pending'",
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            return self._claim_row(row, claimant)

    def _claim_row(self, row: sqlite3.Row, claimant: str) -> QueueTask | None:
        """Decode-then-transition a selected pending row. Called INSIDE an open
        ``BEGIN IMMEDIATE`` transaction. An invalid payload is marked failed
        (no browser launch) and ``None`` returned; a valid payload is moved to
        ``processing``. Never raises for a bad payload — that would roll back
        the failure write."""
        task_id = row["task_id"]
        now = _utc_now()
        try:
            decoded = codec.decode_payload(row["task_type"], json.loads(row["payload_json"]))
        except QueueSchemaError as exc:
            error_payload = dict(exc.to_problem_details())
            error_payload.setdefault("status", 400)
            self._store.conn.execute(
                "UPDATE generation_queue "
                "SET status = 'failed', error_json = ?, updated_at = ? WHERE task_id = ?",
                (json.dumps(error_payload), now, task_id),
            )
            return None

        # Conditional transition — the WHERE status='pending' guard is
        # belt-and-suspenders under the IMMEDIATE write lock (a second claimant
        # blocks on the lock, then sees 'processing' and selects nothing).
        self._store.conn.execute(
            "UPDATE generation_queue "
            "SET status = 'processing', claimant = ?, claimed_at = ?, updated_at = ? "
            "WHERE task_id = ? AND status = 'pending'",
            (claimant, now, now, task_id),
        )
        claimed = self._store.conn.execute(
            f"SELECT {_TASK_COLUMNS} FROM generation_queue WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return _row_to_task(claimed, decoded=decoded)

    def write_checkpoint(self, task_id: str, checkpoint: dict[str, Any]) -> None:
        """Persist a versioned checkpoint document (see
        :func:`make_checkpoint_document`) onto a task row."""
        now = _utc_now()
        with self._store.transaction(immediate=True):
            self._store.conn.execute(
                "UPDATE generation_queue SET checkpoint_json = ?, updated_at = ? WHERE task_id = ?",
                (json.dumps(checkpoint), now, task_id),
            )

    def read_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        """Read a task's checkpoint document, or ``None`` if unset/missing."""
        row = self._store.conn.execute(
            "SELECT checkpoint_json FROM generation_queue WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None or row["checkpoint_json"] is None:
            return None
        return cast("dict[str, Any]", json.loads(row["checkpoint_json"]))

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
