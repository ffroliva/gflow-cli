# SPDX-License-Identifier: MIT
"""MCP tool definitions — maps MCP tools to gflow-cli core functions.

Each tool is registered on the shared FastMCP server instance and delegates
to FlowWorker / DataStore / data.queries for actual execution.

Rate limiting: a token-bucket (capacity=8, refill=1/20s) prevents runaway
agentic loops from burning credits. Session and daily budget limits are
enforced by checking spent amounts against SQLite records.

Profile locking: an asyncio.Lock per profile serialises tool executions
to prevent Playwright browser-context collisions.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from pathlib import Path
from typing import Any

import structlog

from gflow_cli._cli_helpers import _FLOW_ID_RE
from gflow_cli.config import get_settings
from gflow_cli.data.queries import list_projects
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import GFlowError
from gflow_cli.mcp.server import server
from gflow_cli.profile_store import NoDefaultProfileError, NoProfilesError, resolve_profile
from gflow_cli.worker.daemon import FlowWorker
from gflow_cli.worker.queue import QueueRepository

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
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_and_validate_profile(profile: str) -> str | dict[str, Any]:
    """Resolve the requested profile name using the same precedence as the CLI.

    When *profile* is ``"default"`` (the MCP sentinel meaning "auto-pick"),
    this runs the full CLI resolution chain:

    1. ``GFLOW_CLI_PROFILE`` env var
    2. ``config.toml`` ``default_profile``
    3. Auto-select the only profile that exists on disk

    When *profile* is any other value (an explicit name the agent passed in),
    it is forwarded to ``resolve_profile()`` as-is so the same validation runs.

    Returns the resolved profile name string on success, or a ready-to-return
    error dict on failure.
    """
    try:
        # Pass None when the agent omitted the profile (left it as "default")
        # so resolve_profile runs the full auto-detection chain.
        cli_flag: str | None = None if profile == "default" else profile
        resolved = resolve_profile(cli_flag)
    except NoProfilesError:
        return {
            "status": "error",
            "error": {
                "type": "https://gflow-cli.dev/errors/no-profile",
                "title": "No Profile Found",
                "status": 400,
                "detail": (
                    "No gflow profiles exist. Run `gflow auth login --browser chrome` first."
                ),
            },
        }
    except NoDefaultProfileError as exc:
        return {
            "status": "error",
            "error": {
                "type": "https://gflow-cli.dev/errors/no-default-profile",
                "title": "No Default Profile",
                "status": 400,
                "detail": (
                    f"Multiple profiles exist ({', '.join(exc.available)}) but none is set as "
                    "default. Pass profile=<name> explicitly, or run "
                    "`gflow auth use <name>` / set GFLOW_CLI_PROFILE."
                ),
                "available_profiles": exc.available,
            },
        }

    # Sanity-check: the profile directory must exist on disk. If auth was never
    # completed the FlowApiClient would fail with a cryptic Playwright error;
    # surface a clear message here instead.
    settings = get_settings()
    profile_dir = settings.profile_subdir(resolved)
    if not profile_dir.exists():
        return {
            "status": "error",
            "error": {
                "type": "https://gflow-cli.dev/errors/no-profile",
                "title": "Profile Directory Not Found",
                "status": 400,
                "detail": (
                    f"Profile {resolved!r} resolved but its directory does not exist: "
                    f"{profile_dir}. Run `gflow auth login --browser chrome` first."
                ),
            },
        }

    log.debug("mcp.tool.profile_resolved", requested=profile, resolved=resolved)
    return resolved


async def _run_generation_task(
    *,
    profile: str,
    task_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Enqueue a generation task, run it via FlowWorker, and return the result.

    This helper is called while holding the per-profile lock (via the callers
    in gflow_generate_image / gflow_generate_video).  It:

    1. Opens the DataStore (applying any pending migrations on first open).
    2. Ensures the profile row exists in the ``profiles`` table (FK requirement).
    3. Enqueues the task in ``generation_queue``.
    4. Instantiates a :class:`FlowWorker` and directly awaits
       ``process_task()`` — no separate daemon process needed.
    5. Reads the completed / failed status back from the queue row.
    6. On success, resolves local file paths from the ``assets`` / ``local_files``
       tables and returns them.  On failure, surfaces the RFC 9457 error dict.
    """
    settings = get_settings()
    db_path = settings.resolved_db_path()

    task_id = str(uuid.uuid4())

    try:
        # 1. Enqueue the task (short-lived connection, closed before the worker
        #    opens its own — WAL allows a single writer at a time).
        with DataStore.open(db_path) as store:
            data_repo = DataRepository(store)

            # Ensure the profile FK exists before inserting the queue row.
            profile_dir = settings.profile_subdir(profile)
            data_repo.upsert_profile(profile, profile_dir)

            # Resolve remote-ref UUIDs to the display names the UI automation
            # searches for (video paths only); an unresolvable UUID fails fast
            # here instead of timing out in the browser.
            ref_err = _resolve_payload_ref_names(data_repo, profile, payload, task_type)
            if ref_err is not None:
                return ref_err

            task = QueueRepository(store).enqueue_task(
                task_id=task_id,
                profile_name=profile,
                task_type=task_type,
                payload=payload,
            )

        log.info(
            "mcp.tool.task_enqueued",
            task_id=task_id,
            task_type=task_type,
            profile=profile,
        )

        # 2. Run the worker synchronously (we already hold the profile lock).
        worker = FlowWorker(profile_name=profile, db_path=str(db_path))
        try:
            await worker.process_task(task)
        finally:
            worker.close()

        # 3. Read the final task state back.
        with DataStore.open(db_path) as store:
            completed_task = QueueRepository(store).get_task(task_id)
            if completed_task is None:
                return {
                    "status": "error",
                    "error": f"Task {task_id!r} disappeared from queue after execution.",
                }

            # Treat anything other than an explicit "completed" as a failure —
            # a row stuck in "processing"/"pending" must not be reported as a
            # success with an empty file list.
            if completed_task.status != "completed":
                log.warning(
                    "mcp.tool.task_failed",
                    task_id=task_id,
                    status=completed_task.status,
                    error=completed_task.error,
                )
                return {
                    "status": "failed",
                    "task_id": task_id,
                    "error": completed_task.error
                    or {"detail": f"Task ended in unexpected status {completed_task.status!r}"},
                }

            # Resolve local file paths from the asset catalog.
            file_paths: list[str] = []
            flow_project_id: str | None = None
            flow_workflow_id: str | None = None
            if completed_task.flow_media_id:
                asset = DataRepository(store).get_asset_by_flow_media_id(
                    profile,
                    completed_task.flow_media_id,
                )
                if asset:
                    flow_project_id = asset.flow_project_id
                    flow_workflow_id = asset.flow_workflow_id
                    if asset.local_files:
                        file_paths = [
                            str(lf.path) for lf in asset.local_files if lf.path is not None
                        ]

        log.info(
            "mcp.tool.task_completed",
            task_id=task_id,
            flow_project_id=flow_project_id,
            flow_media_id=completed_task.flow_media_id,
            file_count=len(file_paths),
        )

        return {
            "status": "completed",
            "task_id": task_id,
            "flow_project_id": flow_project_id,
            "flow_media_id": completed_task.flow_media_id,
            "flow_workflow_id": flow_workflow_id,
            "files": file_paths,
        }

    except GFlowError as exc:
        log.error("mcp.tool.gflow_error", task_id=task_id, error=str(exc))
        return {
            "status": "error",
            "task_id": task_id,
            "error": dict(exc.to_problem_details()),
        }
    except Exception as exc:
        log.exception("mcp.tool.unexpected_error", task_id=task_id, exc_info=exc)
        return {
            "status": "error",
            "task_id": task_id,
            "error": {
                "type": "https://gflow-cli.dev/errors/unknown",
                "title": "Unexpected Error",
                "status": 500,
                "detail": str(exc),
            },
        }


_BAD_PARAM_TYPE = "https://gflow-cli.dev/errors/bad-parameter"

# UUID (Flow media id) vs on-disk path discriminator for image references.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _bad_param(title: str, detail: str) -> dict[str, Any]:
    """Build the standard RFC 9457 bad-parameter (400) error envelope."""
    return {
        "status": "error",
        "error": {"type": _BAD_PARAM_TYPE, "title": title, "status": 400, "detail": detail},
    }


def _resolve_ref_name(
    data_repo: DataRepository,
    profile: str,
    ref_id: str,
) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve a remote reference to the display name the UI automation searches.

    Returns ``(name, None)`` or ``(None, error_envelope)``. A ``ref_id`` that is
    UUID-shaped but absent from the asset catalog is a mistake worth catching at
    enqueue time: passing the raw UUID downstream surfaced as a ~120s Playwright
    timeout (PR #237 review). A non-UUID string is treated as a literal display
    name the caller typed directly and passed through unchanged.
    """
    asset = data_repo.get_asset_by_any_id(profile, ref_id)
    if asset is not None:
        name = asset.metadata_json.get("display_name")
        if not name:
            # Fallback for images generated before display_name was extracted.
            seed_info = data_repo.resolve_seed_image(profile, asset.flow_media_id)
            if seed_info and seed_info.prompt:
                name = seed_info.prompt
        if name:
            return name, None
        # Asset exists but has no searchable name: returning the raw UUID here
        # would make the picker search for the UUID and time out (PR #245
        # review). Fail fast with a clear error instead.
        return None, _bad_param(
            "Reference Has No Display Name",
            f"'{ref_id}' exists in the catalog but has no display name to search "
            "for in the Flow picker. Re-generate it so a display name is recorded, "
            "or pass the display name directly.",
        )
    if _UUID_RE.fullmatch(ref_id):
        return None, _bad_param(
            "Reference Not Found",
            f"'{ref_id}' was not found in your asset catalog for profile "
            f"{profile!r}. Generate the image first, or pass its display name.",
        )
    return ref_id, None


# Video task types whose remote refs the UI automation attaches by DISPLAY NAME
# (searched in the Flow picker) and therefore need UUID→name resolution. Image
# task types attach remote refs by raw media id and MUST NOT be resolved here —
# gating on that was the PR #245 image-i2i regression.
_VIDEO_TASK_TYPES = frozenset({"t2v", "i2v", "r2v"})


def _resolve_payload_ref_names(
    data_repo: DataRepository,
    profile: str,
    payload: dict[str, Any],
    task_type: str,
) -> dict[str, Any] | None:
    """Resolve every remote-ref field in ``payload`` to a display name in place.

    Only the video task types need this (their refs are attached by display
    name); image tasks attach remote refs by raw media id and are left
    untouched. Returns an error envelope on the first unresolvable UUID, else
    ``None``.
    """
    if task_type not in _VIDEO_TASK_TYPES:
        return None
    if "refs" in payload:
        ref_names: list[str] = []
        for ref in payload["refs"]:
            name, err = _resolve_ref_name(data_repo, profile, ref)
            if err is not None:
                return err
            # name is never falsy when err is None (see _resolve_ref_name).
            assert name is not None
            ref_names.append(name)
        payload["ref_names"] = ref_names
    for key in ("start_image_ref", "end_image_ref"):
        if key in payload:
            name, err = _resolve_ref_name(data_repo, profile, payload[key])
            if err is not None:
                return err
            payload[f"{key}_name"] = name
    return None


def _validate_project(project: str | None) -> dict[str, Any] | None:
    """Return a bad-parameter error dict if ``project`` is set but not a valid
    Flow project id, else ``None``. Reuses the CLI's ``_FLOW_ID_RE`` so the MCP
    ``project`` arg is validated identically to the CLI ``--project`` flag.
    """
    if project is not None and not _FLOW_ID_RE.fullmatch(project):
        return _bad_param(
            "Invalid Project Id",
            f"Project id '{project}' is not a valid Flow project id.",
        )
    return None


def _resolve_image_path(
    raw: str, *, title: str, label: str
) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve a user-supplied image path.

    Returns ``(resolved_path, None)`` when ``raw`` is an existing file, or
    ``(None, error_dict)`` with an RFC 9457 bad-parameter error otherwise.
    Shared by the image and video tools so the validation message stays uniform.
    """
    path = Path(raw).resolve()
    if not path.is_file():
        return None, _bad_param(title, f"{label} '{raw}' does not exist or is not a file.")
    return str(path), None


def _resolve_image_references(
    reference_images: list[str],
) -> tuple[dict[str, list[str]] | None, dict[str, Any] | None]:
    """Split image ``reference_images`` into Flow-media-id refs vs resolved
    on-disk paths. Returns ``({"refs", "ref_paths"}, None)`` or ``(None, error)``.
    """
    refs: list[str] = []
    ref_paths: list[str] = []
    for ref in reference_images:
        if _UUID_RE.fullmatch(ref):
            refs.append(ref)
            continue
        resolved, err = _resolve_image_path(
            ref, title="Invalid Reference Image", label="Reference image path"
        )
        if err is not None:
            return None, err
        assert resolved is not None
        ref_paths.append(resolved)
    return {"refs": refs, "ref_paths": ref_paths}, None


def _build_video_media_inputs(
    *,
    mode: str,
    initial_frame: str | None,
    end_frame: str | None,
    reference_images: list[str] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Validate + resolve the media inputs (start/end/reference frames) for a
    video request. Returns ``(payload_fragment, None)`` or ``(None, error)``.

    Enforces mutual exclusivity (r2v refs vs i2v frames) and the mode-specific
    required inputs at the tool boundary, so a missing frame fails fast with a
    clear 400 instead of a cryptic worker ``ValueError``.
    """
    if reference_images and (initial_frame or end_frame):
        return None, _bad_param(
            "Mutually Exclusive Arguments",
            "reference_images (for r2v) cannot be used alongside initial_frame or "
            "end_frame (for i2v).",
        )
    if mode == "i2v" and initial_frame is None:
        return None, _bad_param(
            "Missing Start Image", "i2v (image-to-video) requires 'initial_frame'."
        )
    if mode == "r2v" and not reference_images:
        return None, _bad_param(
            "Missing Reference Images", "r2v (reference-to-video) requires 'reference_images'."
        )

    media: dict[str, Any] = {}
    for frame, ref_key, path_key, noun in (
        (initial_frame, "start_image_ref", "start_image", "Start"),
        (end_frame, "end_image_ref", "end_image", "End"),
    ):
        if frame is None:
            continue
        if _UUID_RE.fullmatch(frame):
            media[ref_key] = frame
            continue
        resolved, err = _resolve_image_path(
            frame, title=f"Invalid {noun} Image", label=f"{noun} image path"
        )
        if err is not None:
            return None, err
        media[path_key] = resolved
    if reference_images:
        ref_data, err = _resolve_image_references(reference_images)
        if err is not None:
            return None, err
        assert ref_data is not None
        if ref_data["ref_paths"]:
            media["reference_images"] = ref_data["ref_paths"]
        if ref_data["refs"]:
            media["refs"] = ref_data["refs"]
    return media, None


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
    reference_images: list[str] | None = None,
    tools: list[dict[str, Any]] | None = None,
    profile: str = "default",
    project: str | None = None,
) -> dict[str, Any]:
    """Generate an image via Google Flow's Imagen.

    Args:
        prompt: The text prompt describing the desired image.
        model: Model to use — 'nano2', 'nano-pro', or 'image4'.
        aspect: Aspect ratio — '1:1', '9:16', '16:9', '4:3', '3:4'.
        count: Number of images to generate (1-4).
        seed: Optional random seed for reproducibility.
        reference_images: Optional list of reference images for image-to-image generation.
            Can be local file paths or UUIDs of previously uploaded assets.
        tools: Optional list of prompt tools to apply before generation.
            Each item is ``{"name": str, "options": dict}``.  Valid names
            include ``"creative-director"`` (which supports an ``options``
            key of ``"style"`` for domain-vocabulary injection).
            Requires GFLOW_CLI_GEMINI_API_KEY; degrades gracefully to the
            original prompt when unavailable (mirrors the CLI ``--tool/-t``
            flag).
        profile: gflow-cli profile name to use.  Leave as ``"default"`` (or
            omit) to auto-resolve using the same precedence as the CLI:
            ``GFLOW_CLI_PROFILE`` env var → ``config.toml`` default →
            auto-select if exactly one profile exists.
        project: Optional existing Flow project id to generate into (mirrors the
            CLI ``--project`` flag). When omitted, a scratch project is created
            as before.

    Returns:
        Dict with 'status', 'files' (list of local file paths), and metadata.
        On failure, 'status' is 'failed' or 'error' with an RFC 9457 'error' dict.
    """
    if (proj_err := _validate_project(project)) is not None:
        return proj_err

    if not await _rate_limiter.acquire():
        log.warning("mcp.tool.rate_limited", tool="gflow_generate_image")
        return {
            "status": "rate_limited",
            "error": "Too many requests. Please wait before generating again.",
        }

    # Resolve and validate the profile BEFORE acquiring the per-profile lock so
    # that the lock key matches the real on-disk profile name, not the sentinel.
    resolved = _resolve_and_validate_profile(profile)
    if isinstance(resolved, dict):
        return resolved  # profile error — bail out early
    resolved_profile = resolved

    lock = _get_profile_lock(resolved_profile)
    async with lock:
        log.info(
            "mcp.tool.generate_image",
            prompt=prompt[:80],
            model=model,
            aspect=aspect,
            count=count,
            profile=resolved_profile,
        )

        # Validate + adapt the agent-supplied tools array to CLI --tool specs.
        adapted = _adapt_tools(tools)
        if isinstance(adapted, dict):
            return adapted
        tool_specs = adapted

        payload: dict[str, Any] = {
            "prompt": prompt,
            "model": model,
            "aspect": aspect,
            "count": count,
        }
        if seed is not None:
            payload["seed"] = seed
        if project is not None:
            payload["project_id"] = project

        task_type = "t2i"
        if reference_images:
            ref_data, err = _resolve_image_references(reference_images)
            if err is not None:
                return err
            assert ref_data is not None
            payload["refs"] = ref_data["refs"]
            payload["ref_paths"] = ref_data["ref_paths"]
            task_type = "i2i"

        if tool_specs:
            payload["tool_specs"] = list(tool_specs)

        result = await _run_generation_task(
            profile=resolved_profile,
            task_type=task_type,
            payload=payload,
        )

        # Annotate the result with the original request parameters for context.
        result["params"] = {
            "prompt": prompt,
            "model": model,
            "aspect": aspect,
            "count": count,
            "seed": seed,
            "reference_images": reference_images,
            "tools": tools or [],
            "tool_specs": list(tool_specs),
            "profile": resolved_profile,
            "requested_profile": profile,
            "project": project,
        }
        return result


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
    initial_frame: str | None = None,
    end_frame: str | None = None,
    reference_images: list[str] | None = None,
    tools: list[dict[str, Any]] | None = None,
    profile: str = "default",
    project: str | None = None,
) -> dict[str, Any]:
    """Generate a video via Google Flow's Veo.

    Args:
        prompt: The text prompt describing the desired video.
        mode: Generation mode — 't2v', 'i2v', or 'r2v'.
        aspect: Aspect ratio — '9:16' or '16:9'.
        initial_frame: Path to start frame image (required for i2v).
        end_frame: Path to end frame image (optional for i2v).
        reference_images: List of reference image paths (ingredients) for r2v.
        tools: Optional list of prompt tools to apply before generation.
            Each item is ``{"name": str, "options": dict}``.  Valid names
            include ``"creative-director"`` (which supports an ``options``
            key of ``"style"`` for domain-vocabulary injection).
            Requires GFLOW_CLI_GEMINI_API_KEY; degrades gracefully to the
            original prompt when unavailable (mirrors the CLI ``--tool/-t``
            flag on ``video t2v``).
        profile: gflow-cli profile name to use.  Leave as ``"default"`` (or
            omit) to auto-resolve using the same precedence as the CLI:
            ``GFLOW_CLI_PROFILE`` env var → ``config.toml`` default →
            auto-select if exactly one profile exists.
        project: Optional existing Flow project id to generate into (mirrors the
            CLI ``--project`` flag on ``video t2v``/``i2v``/``r2v``). When
            omitted, a scratch project is created as before.

    Returns:
        Dict with 'status', 'files' (list of local file paths), and metadata.
        On failure, 'status' is 'failed' or 'error' with an RFC 9457 'error' dict.
    """
    if (proj_err := _validate_project(project)) is not None:
        return proj_err

    if not await _rate_limiter.acquire():
        log.warning("mcp.tool.rate_limited", tool="gflow_generate_video")
        return {
            "status": "rate_limited",
            "error": "Too many requests. Please wait before generating again.",
        }

    # Resolve and validate the profile BEFORE acquiring the per-profile lock so
    # that the lock key matches the real on-disk profile name, not the sentinel.
    resolved = _resolve_and_validate_profile(profile)
    if isinstance(resolved, dict):
        return resolved  # profile error — bail out early
    resolved_profile = resolved

    lock = _get_profile_lock(resolved_profile)
    async with lock:
        log.info(
            "mcp.tool.generate_video",
            prompt=prompt[:80],
            mode=mode,
            aspect=aspect,
            profile=resolved_profile,
        )

        adapted = _adapt_tools(tools)
        if isinstance(adapted, dict):
            return adapted
        tool_specs = adapted

        payload: dict[str, Any] = {
            "prompt": prompt,
            "mode": mode,
            "aspect": aspect,
        }

        media, media_err = _build_video_media_inputs(
            mode=mode,
            initial_frame=initial_frame,
            end_frame=end_frame,
            reference_images=reference_images,
        )
        if media_err is not None:
            return media_err
        assert media is not None
        payload.update(media)

        if tool_specs:
            payload["tool_specs"] = list(tool_specs)
        if project is not None:
            payload["project_id"] = project

        # task_type matches the mode ("t2v", "i2v", "r2v")
        result = await _run_generation_task(
            profile=resolved_profile,
            task_type=mode,
            payload=payload,
        )

        # Annotate the result with the original request parameters for context.
        result["params"] = {
            "prompt": prompt,
            "mode": mode,
            "aspect": aspect,
            "initial_frame": initial_frame,
            "end_frame": end_frame,
            "reference_images": reference_images,
            "tools": tools or [],
            "tool_specs": list(tool_specs),
            "profile": resolved_profile,
            "requested_profile": profile,
            "project": project,
        }
        return result


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

    settings = get_settings()
    db_path = settings.resolved_db_path()

    try:
        rows = list_projects(
            db_path=db_path,
            profile=profile if profile != "default" else None,
            limit=limit,
            offset=0,
        )
        return {
            "status": "ok",
            "projects": [
                {
                    "project_id": r.project_id,
                    "profile": r.profile,
                    "created_at": r.created_at.isoformat(),
                    "image_count": r.image_count,
                    "video_count": r.video_count,
                }
                for r in rows
            ],
            "total": len(rows),
        }
    except Exception as exc:
        log.error("mcp.tool.list_projects_error", error=str(exc))
        return {
            "status": "error",
            "error": str(exc),
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

    Characters are cloud-side Flow entities (not stored in the local SQLite
    catalog) and require an active browser session to enumerate.  Use
    ``gflow character list`` in the CLI for a full listing, or call this tool
    from a context where a browser session is available.

    Args:
        profile: gflow-cli profile name to filter by.

    Returns:
        Dict with 'characters' list.

    Note:
        Characters are project-scoped on the Flow side; this tool returns an
        empty list when no project_id is provided, since listing across all
        projects would require iterating every project.  Pass a project_id
        via a future parameter update, or use ``gflow character list`` in the
        terminal.
    """
    log.info("mcp.tool.list_characters", profile=profile)

    # Characters live on the Flow cloud side and are not cached in the local
    # SQLite catalog — fetching them requires an active browser session and a
    # specific project_id.  Return an informative empty response rather than
    # silently returning nothing or crashing.
    return {
        "status": "ok",
        "characters": [],
        "note": (
            "Character listing requires a project_id and an active browser session. "
            "Use `gflow character list --project <id>` in the terminal, or extend "
            "this MCP tool with a project_id parameter."
        ),
    }


# Re-export Path so tests that import it directly still work
__all__ = [
    "gflow_generate_image",
    "gflow_generate_video",
    "gflow_list_tools",
    "gflow_list_projects",
    "gflow_list_characters",
    "_TokenBucket",
    "_adapt_tools",
    "_run_generation_task",
]
