from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from gflow_cli.api.client import FlowApiClient
from gflow_cli.auth.verification import FlowSessionOutcome, verify_flow_profile
from gflow_cli.config import get_settings
from gflow_cli.data.queries import list_projects
from gflow_cli.data.store import DataStore
from gflow_cli.errors import AuthMissingError, GFlowError, RateLimitError
from gflow_cli.mcp.server import mcp
from gflow_cli.worker.daemon import get_profile_lock

logger = structlog.get_logger()


class TokenBucket:
    def __init__(self, capacity: int, refill_rate: float):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = float(capacity)
        self.last_update = time.monotonic()

    def consume(self, tokens: int = 1) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_update
        self.last_update = now
        self.tokens = min(float(self.capacity), self.tokens + elapsed * self.refill_rate)
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False


# Global token-bucket rate limiter: capacity=8,
# refill rate of 1 token every 20 seconds (0.05 tokens/sec)
_limiter = TokenBucket(capacity=8, refill_rate=0.05)

# Session credit tracking
_session_credits_spent = 0


def get_credits_spent_today(store: DataStore, profile_name: str) -> int:
    today_start = datetime.now(UTC).date().isoformat() + "T00:00:00Z"

    # Count images generated today
    cursor = store.conn.execute(
        """
        SELECT COUNT(*)
        FROM operation_assets oa
        JOIN operations o ON oa.operation_id = o.id
        JOIN assets a ON oa.asset_id = a.id
        WHERE o.profile_name = ?
          AND o.status = 'completed'
          AND o.started_at >= ?
          AND a.kind = 'image'
        """,
        (profile_name, today_start),
    )
    image_count = cursor.fetchone()[0]

    # Count videos generated today
    cursor = store.conn.execute(
        """
        SELECT COUNT(*)
        FROM operation_assets oa
        JOIN operations o ON oa.operation_id = o.id
        JOIN assets a ON oa.asset_id = a.id
        WHERE o.profile_name = ?
          AND o.status = 'completed'
          AND o.started_at >= ?
          AND a.kind = 'video'
        """,
        (profile_name, today_start),
    )
    video_count = cursor.fetchone()[0]

    return image_count * 1 + video_count * 20


async def _check_limits_and_auth(profile_name: str, cost: int) -> Path:
    # 1. Verify Rate Limiting
    if not _limiter.consume(1):
        raise RateLimitError("Rate limit exceeded. Please wait before running again.")

    # 2. Resolve Profile and Check Authentication
    settings = get_settings()
    profile_dir = settings.profile_subdir(profile_name)

    auth_status = await verify_flow_profile(profile_dir)
    if auth_status.outcome != FlowSessionOutcome.AUTHENTICATED:
        raise AuthMissingError(
            f"Profile '{profile_name}' is not authenticated with Flow. "
            "Please run 'gflow auth login' in the terminal first."
        )

    # 3. Check Session and Daily Budgets
    global _session_credits_spent
    session_limit = os.environ.get("GFLOW_CLI_SESSION_CREDIT_LIMIT") or os.environ.get(
        "GFLOW_SESSION_CREDIT_LIMIT"
    )
    if session_limit is not None:
        try:
            limit_val = int(session_limit)
            if _session_credits_spent + cost > limit_val:
                raise RateLimitError(
                    f"Session credit limit exceeded. Remaining session budget: "
                    f"{limit_val - _session_credits_spent} credits, requested: {cost} credits."
                )
        except ValueError:
            pass

    daily_limit = os.environ.get("GFLOW_CLI_DAILY_BUDGET") or os.environ.get("GFLOW_DAILY_BUDGET")
    if daily_limit is not None:
        try:
            limit_val = int(daily_limit)
            db_path = settings.resolved_db_path()
            with DataStore.open(db_path) as store:
                spent_today = get_credits_spent_today(store, profile_name)
            if spent_today + cost > limit_val:
                raise RateLimitError(
                    f"Daily budget limit exceeded. Spent today: {spent_today} credits, "
                    f"limit: {limit_val} credits, requested: {cost} credits."
                )
        except ValueError:
            pass

    return profile_dir


async def _wait_for_task(db_path: Path, task_id: str, cost: int) -> dict[str, Any]:
    while True:
        await asyncio.sleep(1.0)
        with DataStore.open(db_path) as store:
            row = store.conn.execute(
                "SELECT status, flow_media_id, error_json FROM generation_queue WHERE task_id = ?",
                (task_id,),
            ).fetchone()

        if row is None:
            raise GFlowError(f"Task '{task_id}' was removed from the queue.")

        status = row["status"]
        if status == "completed":
            global _session_credits_spent
            _session_credits_spent += cost

            # Try to resolve local path for the media
            local_path = None
            if row["flow_media_id"]:
                with DataStore.open(db_path) as store:
                    file_row = store.conn.execute(
                        "SELECT path FROM local_files "
                        "WHERE asset_id = (SELECT id FROM assets WHERE flow_media_id = ? LIMIT 1) "
                        "LIMIT 1",
                        (row["flow_media_id"],),
                    ).fetchone()
                    if file_row:
                        local_path = file_row["path"]

            return {
                "status": "completed",
                "task_id": task_id,
                "flow_media_id": row["flow_media_id"],
                "local_path": local_path,
                "local_uri": Path(local_path).as_uri() if local_path else None,
            }
        elif status == "failed":
            error_data: dict[str, Any] = json.loads(row["error_json"]) if row["error_json"] else {}
            title = error_data.get("title", "Task Failed")
            detail = error_data.get("detail", "An unexpected error occurred during task execution.")
            raise GFlowError(f"{title}: {detail}")


@mcp.tool()
async def gflow_generate_image(
    prompt: str,
    aspect: str = "1:1",
    model: str = "nano-pro",
    count: int = 1,
    profile: str = "default",
    project_id: str | None = None,
    reference_entities: list[str] | None = None,
    reference_entity_names: list[str] | None = None,
) -> dict[str, Any]:
    """Generate 1-4 images from a text prompt via Google Flow Imagen.

    This tool enqueues the image generation task and blocks until it is processed.
    """
    cost = count
    await _check_limits_and_auth(profile, cost)

    settings = get_settings()
    db_path = settings.resolved_db_path()

    task_id = f"mcp-img-{uuid.uuid4().hex[:8]}"
    payload = {
        "prompt": prompt,
        "aspect": aspect,
        "model": model,
        "count": count,
        "project_id": project_id,
        "reference_entities": reference_entities or [],
        "reference_entity_names": reference_entity_names or [],
    }

    # Queue the task
    with DataStore.open(db_path) as store:
        now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        store.conn.execute(
            """
            INSERT INTO generation_queue(
                task_id, profile_name, task_type, payload_json, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, profile, "t2i", json.dumps(payload), "pending", now, now),
        )

    logger.info("Enqueued image generation task via MCP", task_id=task_id, profile=profile)
    return await _wait_for_task(db_path, task_id, cost)


@mcp.tool()
async def gflow_generate_video(
    prompt: str,
    mode: str = "t2v",
    aspect: str = "16:9",
    tier: str = "fast",
    model: str | None = None,
    duration: int | None = None,
    count: int = 1,
    seed: int | None = None,
    start_image: str | None = None,
    end_image: str | None = None,
    reference_images: list[str] | None = None,
    reference_entities: list[str] | None = None,
    reference_entity_names: list[str] | None = None,
    reference_audio: str | None = None,
    profile: str = "default",
) -> dict[str, Any]:
    """Generate a video from a text prompt or input references via Google Flow Veo.

    This tool enqueues the video generation task and blocks until it is processed.
    """
    cost = count * 20
    await _check_limits_and_auth(profile, cost)

    settings = get_settings()
    db_path = settings.resolved_db_path()

    task_id = f"mcp-vid-{uuid.uuid4().hex[:8]}"
    payload = {
        "prompt": prompt,
        "mode": mode,
        "aspect": aspect,
        "tier": tier,
        "model": model,
        "duration": duration,
        "count": count,
        "seed": seed,
        "start_image": start_image,
        "end_image": end_image,
        "reference_images": reference_images or [],
        "reference_entities": reference_entities or [],
        "reference_entity_names": reference_entity_names or [],
        "reference_audio": reference_audio,
    }

    # Queue the task
    with DataStore.open(db_path) as store:
        now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        store.conn.execute(
            """
            INSERT INTO generation_queue(
                task_id, profile_name, task_type, payload_json, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, profile, mode, json.dumps(payload), "pending", now, now),
        )

    logger.info("Enqueued video generation task via MCP", task_id=task_id, profile=profile)
    return await _wait_for_task(db_path, task_id, cost)


@mcp.tool()
async def gflow_list_projects(
    profile: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List historical projects generated using gflow.

    Reads the database operations record catalog directly without requiring a browser context.
    """
    settings = get_settings()
    db_path = settings.resolved_db_path()
    rows = list_projects(db_path=db_path, profile=profile, limit=limit, offset=offset)
    return [dataclasses_asdict_custom(r) for r in rows]


@mcp.tool()
async def gflow_list_characters(
    project_id: str,
    profile: str = "default",
) -> list[dict[str, Any]]:
    """List all character entities within a Flow project.

    This tool launches a browser context to fetch characters directly from the
    live Google Flow workspace.
    """
    settings = get_settings()
    profile_dir = settings.profile_subdir(profile)

    # Resolve active profile lock to prevent browser collisions
    lock = get_profile_lock(profile)
    async with lock:
        async with FlowApiClient(
            profile_dir=profile_dir,
            headless=settings.headless,
            transport=settings.transport,
            out_dir=settings.output_dir,
        ) as client:
            characters = await client.list_characters(project_id)

    res: list[dict[str, Any]] = []
    for c in characters:
        res.append(
            {
                "entity_id": c.entity_id,
                "name": c.display_name,
                "voice": c.voice,
                "personality": c.personality,
                "thumbnail_media_id": c.thumbnail_media_id,
            }
        )
    return res


def dataclasses_asdict_custom(obj: Any) -> dict[str, Any]:
    # Custom helper to serialize ProjectRow and other dataclasses containing Path objects
    import dataclasses

    if dataclasses.is_dataclass(obj):
        res: dict[str, Any] = {}
        for f in dataclasses.fields(obj):
            val = getattr(obj, f.name)
            if isinstance(val, Path):
                res[f.name] = str(val)
            else:
                res[f.name] = val
        return res
    return {}
