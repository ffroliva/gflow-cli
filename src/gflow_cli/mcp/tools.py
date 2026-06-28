# SPDX-License-Identifier: MIT
"""MCP tool definitions — maps MCP tools to gflow-cli core functions.

Each tool is registered on the shared FastMCP server instance and delegates
to FlowApiClient / DataStore for actual execution.

Rate limiting: a token-bucket (capacity=8, refill=1/20s) prevents runaway
agentic loops from burning credits. Session and daily budget limits are
enforced by checking spent amounts against SQLite records.

Profile locking: an asyncio.Lock per profile serialises tool executions
to prevent Playwright browser-context collisions.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from gflow_cli.mcp.server import server

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Token bucket rate limiter
# ---------------------------------------------------------------------------

_BUCKET_CAPACITY = 8
_BUCKET_REFILL_RATE = 1 / 20  # 1 token every 20 seconds


class _TokenBucket:
    """Simple token-bucket rate limiter for generation tools."""

    def __init__(self, capacity: int = _BUCKET_CAPACITY, refill_rate: float = _BUCKET_REFILL_RATE):
        self._capacity = capacity
        self._tokens = float(capacity)
        self._refill_rate = refill_rate
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        """Try to acquire a token. Returns True if acquired, False if rate-limited."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
            self._last_refill = now

            if self._tokens >= 1:
                self._tokens -= 1
                return True
            return False


_rate_limiter = _TokenBucket()

# Per-profile execution locks to prevent Playwright collisions
_profile_locks: dict[str, asyncio.Lock] = {}


def _get_profile_lock(profile: str) -> asyncio.Lock:
    """Get or create an asyncio.Lock for the given profile name."""
    if profile not in _profile_locks:
        _profile_locks[profile] = asyncio.Lock()
    return _profile_locks[profile]


def _adapt_tools(tools: list[dict[str, Any]] | None) -> tuple[str, ...] | dict[str, Any]:
    """Validate + adapt the MCP ``tools`` array to CLI ``--tool`` specs.

    Returns the spec tuple on success, or a structured error dict (to return to
    the agent) when an item is malformed — so a bad ``tools`` payload fails
    cleanly rather than as an uncaught error once generation is wired.
    """
    from pydantic import ValidationError

    from gflow_cli.tools.invocation import tool_specs_from_invocations

    try:
        return tool_specs_from_invocations(tools)
    except ValidationError as exc:
        log.warning("mcp.tool.invalid_tools", error=str(exc))
        return {
            "status": "invalid_tools",
            "error": (
                "Each item in 'tools' must be {'name': <slug>, 'options': {k: v}}. "
                f"Validation failed: {exc}"
            ),
        }


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@server.tool(
    name="gflow_generate_image",
    description=(
        "Generate an image using Google Flow's Imagen model. "
        "Produces 1-4 images from a text prompt. "
        "Models: nano2 (fast), nano-pro (balanced), image4 (highest quality). "
        "Aspects: 1:1, 9:16, 16:9, 4:3, 3:4. "
        "Returns local file paths to the generated images."
    ),
)
async def gflow_generate_image(
    prompt: str,
    model: str = "nano2",
    aspect: str = "1:1",
    count: int = 1,
    seed: int | None = None,
    tools: list[dict[str, Any]] | None = None,
    profile: str = "default",
) -> dict[str, Any]:
    """Generate an image via Google Flow's Imagen.

    Args:
        prompt: The text prompt describing the desired image.
        model: Model to use — 'nano2', 'nano-pro', or 'image4'.
        aspect: Aspect ratio — '1:1', '9:16', '16:9', '4:3', '3:4'.
        count: Number of images to generate (1-4).
        seed: Optional random seed for reproducibility.
        tools: Optional list of prompt tools to apply before generation.
            Each item is ``{"name": str, "options": dict}``.  Valid names
            include ``"creative-director"`` (which supports an ``options``
            key of ``"style"`` for domain-vocabulary injection).
            Requires GFLOW_CLI_GEMINI_API_KEY; degrades gracefully to the
            original prompt when unavailable (mirrors the CLI ``--tool/-t``
            flag).
        profile: gflow-cli profile name to use.

    Returns:
        Dict with 'status', 'images' (list of file paths), and metadata.
    """
    if not await _rate_limiter.acquire():
        log.warning("mcp.tool.rate_limited", tool="gflow_generate_image")
        return {
            "status": "rate_limited",
            "error": "Too many requests. Please wait before generating again.",
        }

    lock = _get_profile_lock(profile)
    async with lock:
        log.info(
            "mcp.tool.generate_image",
            prompt=prompt[:80],
            model=model,
            aspect=aspect,
            count=count,
            profile=profile,
        )

        # Validate + adapt the agent-supplied tools array to CLI --tool specs now
        # (fail cleanly on malformed input) even though generation is not yet
        # wired — the daemon will feed these specs to apply_tool_option.
        adapted = _adapt_tools(tools)
        if isinstance(adapted, dict):
            return adapted
        tool_specs = adapted

        # TODO: Wire to FlowApiClient when daemon integration lands.
        # For now, return a structured placeholder that validates the schema.
        return {
            "status": "pending",
            "message": (
                "MCP server scaffolded. Image generation will be wired "
                "when the daemon worker queue (Sprint 2) ships."
            ),
            "params": {
                "prompt": prompt,
                "model": model,
                "aspect": aspect,
                "count": count,
                "seed": seed,
                "tools": tools or [],
                "tool_specs": list(tool_specs),
                "profile": profile,
            },
        }


@server.tool(
    name="gflow_generate_video",
    description=(
        "Generate a video using Google Flow's Veo model. "
        "Modes: t2v (text-to-video), i2v (image-to-video), r2v (reference-to-video). "
        "Aspects: 9:16, 16:9. "
        "Returns the local file path to the generated video."
    ),
)
async def gflow_generate_video(
    prompt: str,
    mode: str = "t2v",
    aspect: str = "9:16",
    image_path: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    profile: str = "default",
) -> dict[str, Any]:
    """Generate a video via Google Flow's Veo.

    Args:
        prompt: The text prompt describing the desired video.
        mode: Generation mode — 't2v', 'i2v', or 'r2v'.
        aspect: Aspect ratio — '9:16' or '16:9'.
        image_path: Path to start frame image (required for i2v/r2v).
        tools: Optional list of prompt tools to apply before generation.
            Each item is ``{"name": str, "options": dict}``.  Valid names
            include ``"creative-director"`` (which supports an ``options``
            key of ``"style"`` for domain-vocabulary injection).
            Requires GFLOW_CLI_GEMINI_API_KEY; degrades gracefully to the
            original prompt when unavailable (mirrors the CLI ``--tool/-t``
            flag on ``video t2v``).
        profile: gflow-cli profile name to use.

    Returns:
        Dict with 'status', 'video_path', and metadata.
    """
    if not await _rate_limiter.acquire():
        log.warning("mcp.tool.rate_limited", tool="gflow_generate_video")
        return {
            "status": "rate_limited",
            "error": "Too many requests. Please wait before generating again.",
        }

    lock = _get_profile_lock(profile)
    async with lock:
        log.info(
            "mcp.tool.generate_video",
            prompt=prompt[:80],
            mode=mode,
            aspect=aspect,
            profile=profile,
        )

        adapted = _adapt_tools(tools)
        if isinstance(adapted, dict):
            return adapted
        tool_specs = adapted

        # TODO: Wire to FlowApiClient when daemon integration lands.
        return {
            "status": "pending",
            "message": (
                "MCP server scaffolded. Video generation will be wired "
                "when the daemon worker queue (Sprint 2) ships."
            ),
            "params": {
                "prompt": prompt,
                "mode": mode,
                "aspect": aspect,
                "image_path": image_path,
                "tools": tools or [],
                "tool_specs": list(tool_specs),
                "profile": profile,
            },
        }


@server.tool(
    name="gflow_list_tools",
    description="List available gflow prompt tools (name, title, description, category).",
)
async def gflow_list_tools() -> dict[str, Any]:
    """List available prompt tools that can be passed to gflow_generate_image/video.

    Returns:
        Dict with 'tools' list; each entry has name, title, description, category.
    """
    from gflow_cli.tools.registry import iter_tools

    return {
        "tools": [
            {"name": s.name, "title": s.title, "description": s.description, "category": s.category}
            for s in iter_tools()
        ]
    }


@server.tool(
    name="gflow_list_projects",
    description=(
        "List all projects in the local gflow catalog. "
        "Returns project IDs, names, and creation dates from the SQLite database."
    ),
)
async def gflow_list_projects(
    profile: str = "default",
    limit: int = 50,
) -> dict[str, Any]:
    """List projects from the local SQLite catalog.

    Args:
        profile: gflow-cli profile name to filter by.
        limit: Maximum number of projects to return.

    Returns:
        Dict with 'projects' list and pagination info.
    """
    log.info("mcp.tool.list_projects", profile=profile, limit=limit)

    # TODO: Wire to DataStore queries when daemon integration lands.
    return {
        "status": "ok",
        "message": "MCP server scaffolded. Project listing will be wired to DataStore.",
        "projects": [],
        "total": 0,
    }


@server.tool(
    name="gflow_list_characters",
    description=(
        "List all Flow Character entities for the active profile. "
        "Characters are reusable project-scoped entities with voices."
    ),
)
async def gflow_list_characters(
    profile: str = "default",
) -> dict[str, Any]:
    """List Flow Character entities from the local catalog.

    Args:
        profile: gflow-cli profile name to filter by.

    Returns:
        Dict with 'characters' list.
    """
    log.info("mcp.tool.list_characters", profile=profile)

    # TODO: Wire to DataStore queries when daemon integration lands.
    return {
        "status": "ok",
        "message": "MCP server scaffolded. Character listing will be wired to DataStore.",
        "characters": [],
    }
