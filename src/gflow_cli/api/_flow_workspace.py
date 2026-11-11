"""Flow workspace surface: projects, agent info, uploads, workflows, scenes, project listing.

Split out of ``client.py`` (#1249); ``FlowApiClient`` composes it. Method
bodies are unchanged.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

import structlog

from gflow_cli.api import routes
from gflow_cli.api.dto import (
    AssetInfo,
    ProjectInfo,
)
from gflow_cli.api.scene import ConcatInput, Scene, SceneWorkflow
from gflow_cli.api.video import (
    is_media_uuid,
)
from gflow_cli.errors import (
    AuthExpiredError,
    SceneConcatError,
    TransportTimeoutError,
    WireFormatError,
)
from gflow_cli.storage import AnyPath, write_asset_async

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from gflow_cli.api.image import AgentInstruction, ImageRef, ProjectBrief
from gflow_cli.api._flow_session import (
    _APPLICATION_JSON,
    FlowSession,
    JsonObject,
    _build_wire_format_discovery,
    _make_instance,
    _raise_for_non_retryable,
)

logger = structlog.get_logger(__name__)

# Cap matches Flow's UI upload limit (~20 MB observed in captured traffic). Used
# by `upload_image` to reject oversize files BEFORE reading them into memory —
# protects this process from OOM and the remote endpoint from DoS-shaped traffic.
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB

# Server-side concat returns the combined MP4 inline as base64 in `encodedVideo`
# (~1.27 MB base64 per second of video). Cap before b64decode to avoid OOM on a
# pathologically long scene; ~350 MB base64 ≈ 260 MB MP4 ≈ a ~4.5-min scene.
MAX_CONCAT_B64_LEN = 350 * 1024 * 1024
_MIGRATED_FLOW_HOST = "flow.google.com"


def _is_supported_image_header(header: bytes) -> bool:
    """Return True if ``header`` (first 12 bytes of a file) matches a known image
    container's magic bytes.

    Allowed formats — every one is accepted by Flow's web UI:

    * **PNG** — ``\\x89PNG\\r\\n\\x1a\\n``
    * **JPEG** — bytes 0..2 are ``\\xff\\xd8\\xff``
    * **WebP** — bytes 0..3 ``RIFF`` + bytes 8..11 ``WEBP``
    * **GIF** — ``GIF87a`` or ``GIF89a``

    Rejecting anything else is a defense-in-depth measure: combined with
    ``resolve_path=True`` on the CLI argument it stops a symlink-laundering
    attack (``./photo.png -> ~/.ssh/id_rsa``) at the bytes layer.
    """
    if len(header) < 12:
        return False
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if header[:3] == b"\xff\xd8\xff":
        return True
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return True
    return header[:6] in (b"GIF87a", b"GIF89a")


async def validate_image_file(image_path: Path) -> None:
    """Reject a file that must not be uploaded to Flow, before any bytes leave.

    Exists, within :data:`MAX_IMAGE_BYTES`, and a real image by its first 12
    bytes — the header check is what stops a resolved path (a symlink, a typo)
    from laundering ``~/.ssh/id_rsa`` into a Flow project. Raises the same
    ``FileNotFoundError`` / ``ValueError`` the REST upload has always raised.
    """
    if not image_path.exists():
        raise FileNotFoundError(image_path)
    size = image_path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        msg = (
            f"Image too large: {size / 1_048_576:.1f} MB exceeds "
            f"{MAX_IMAGE_BYTES // 1_048_576} MB limit"
        )
        raise ValueError(msg)

    # Staged read: validate magic bytes first (12 B) before loading the full
    # file. Run both reads in a worker thread to keep the event loop free.
    def _read_header(p: Path) -> bytes:
        with p.open("rb") as fh:
            return fh.read(12)

    if not _is_supported_image_header(await asyncio.to_thread(_read_header, image_path)):
        msg = f"Not a supported image format: {image_path.name}"
        raise ValueError(msg)


def _unwrap_trpc(data: Any) -> JsonObject:
    """Unwrap the tRPC envelope ``result.data.json`` and return the inner dict.

    Only the standard tRPC v10 shape is accepted:
    ``{"result": {"data": {"json": {...}}}}``.

    A no-``"json"``-key shape was considered but has no observed evidence in
    any captured HAR for createEntity or projectInitialData (see
    ``docs/CHARACTER_RECON.md`` and ``scripts/dev/character_create_spike.py``).
    Responses without ``"json"`` therefore surface as
    :class:`~gflow_cli.errors.WireFormatError` rather than being silently
    accepted.

    Raises :class:`~gflow_cli.errors.WireFormatError` when the envelope is
    malformed or when the extracted payload is not a dict.
    """
    if not isinstance(data, dict):
        raise WireFormatError(
            detail=f"tRPC response is not a dict; got {type(data).__name__}",
            route="tRPC",
        )
    data_dict = cast("JsonObject", data)
    result = data_dict.get("result")
    if not isinstance(result, dict):
        raise WireFormatError(
            detail="tRPC reply missing 'result' dict",
            route="tRPC",
        )
    result_dict = cast("JsonObject", result)
    data_obj = result_dict.get("data")
    if not isinstance(data_obj, dict):
        raise WireFormatError(
            detail="tRPC reply missing result.data dict",
            route="tRPC",
        )
    data_obj_dict = cast("JsonObject", data_obj)
    inner: Any = data_obj_dict.get("json")
    if not isinstance(inner, dict):
        raise WireFormatError(
            detail=f"tRPC reply missing result.data.json dict; got {type(inner).__name__}",
            route="tRPC",
        )
    return cast("JsonObject", inner)


def _default_project_title() -> str:
    return datetime.now().strftime("gflow-cli %b %d, %I:%M %p")


class WorkspaceSurface(FlowSession):
    """Flow workspace surface: projects, agent info, uploads, workflows, scenes, project listing."""

    # --- public API -------------------------------------------------------

    async def create_project(self, title: str | None = None) -> ProjectInfo:
        """Bootstrap a fresh Flow project. Title defaults to a timestamp.

        Maps to `POST .../trpc/project.createProject`, and to flow.google.com's projects
        page when that route refuses (#864, see :meth:`_labs_project_route_refused`).
        """
        title = title or _default_project_title()
        if self.settings.flow_host != _MIGRATED_FLOW_HOST:
            body = {"json": {"projectTitle": title, "toolName": "PINHOLE"}}
            try:
                data = await self._post_json(
                    routes.CREATE_PROJECT, body, content_type=_APPLICATION_JSON
                )
                return ProjectInfo.from_create_response(data)
            except (AuthExpiredError, WireFormatError) as exc:
                if not self._labs_project_route_refused(exc):
                    raise
        from gflow_cli.api.transports import migrated_composer  # noqa: PLC0415

        page = await self._checkout_page()
        try:
            return await migrated_composer.create_project(page, title)
        finally:
            self._checkin_page(page)

    def _labs_project_route_refused(self, exc: AuthExpiredError | WireFormatError) -> bool:
        """Is this the labs project route's measured refusal, worth trying flow.google.com?

        Measured 2026-09-17 on four profiles, 12/12: 404 "Flow RPCs have been deprecated
        and disabled" for a session holding a labs token, 401 for one without. Keyed on
        that observed answer, never on which host an account is served — and only under
        ``flow_host=auto``, so an operator who pinned labs.google keeps the labs error.
        A 401 from a genuinely signed-out profile goes on to fail on flow.google.com too,
        where the landing diagnosis names what the browser actually saw.
        """
        refused = self.settings.flow_host == "auto" and exc.status in (401, 404)
        if refused:
            logger.info("project.labs_route_refused", status=exc.status, issue_ref="#864")
        return refused

    async def rename_project(self, project_id: str, new_title: str) -> JsonObject:
        """Rename an existing Flow project.

        Maps to `POST .../trpc/project.renameProject`, and to the project's header title
        on flow.google.com when that route refuses (#864). Returns ``{}`` there.
        """
        if self.settings.flow_host != _MIGRATED_FLOW_HOST:
            body = {"json": {"projectId": project_id, "projectTitle": new_title}}
            try:
                return await self._post_json(
                    routes.RENAME_PROJECT, body, content_type=_APPLICATION_JSON
                )
            except (AuthExpiredError, WireFormatError) as exc:
                if not self._labs_project_route_refused(exc):
                    raise
        from gflow_cli.api.transports import migrated_composer  # noqa: PLC0415

        page = await self._checkout_page()
        try:
            await migrated_composer.rename_project(page, project_id, new_title)
        finally:
            self._checkin_page(page)
        return {}

    async def patch_agent_info(
        self,
        project_id: str,
        *,
        enabled: bool | None = None,
        cards: tuple[AgentInstruction, ...] | None = None,
    ) -> JsonObject:
        """Patch the agentic settings / instructions for a project.

        Maps to ``PATCH /v1/projects/{project_id}/agentInfo``. The endpoint
        accepts the default ``text/plain`` content-type used by ``_patch_json``
        (``application/json`` also works; ``application/json+protobuf`` is
        rejected 400 — see docs/AGENT_UI_RECON / the instructions spike).

        Returns the ``projectBrief`` echoed back in the PATCH response (Flow
        replies with the full updated ``agentInfo``). There is **no**
        ``GET /agentInfo`` route — this echo is the authoritative read-back, so
        callers (``gflow instructions list`` / enable / disable) confirm state
        from the return value rather than a follow-up GET. Returns ``{}`` when
        nothing was patched or the echo was empty.
        """
        from gflow_cli.api.image import build_agent_brief_cards

        body: dict[str, Any] = {"projectBrief": {}}
        masks: list[str] = []
        if enabled is not None:
            body["projectBrief"]["enabled"] = enabled
            masks.append("project_brief.enabled")

        if cards is not None:
            body["projectBrief"]["cards"] = build_agent_brief_cards(cards, project_id=project_id)
            masks.append("project_brief.cards")

        if not masks:
            return {}

        mask_str = ",".join(masks)
        url = f"https://aisandbox-pa.googleapis.com/v1/projects/{project_id}/agentInfo?updateMask={mask_str}"
        data = await self._patch_json(url, body)
        if isinstance(data, dict):
            agent_info = cast("JsonObject", data).get("agentInfo")
            if isinstance(agent_info, dict):
                brief = cast("JsonObject", agent_info).get("projectBrief")
                if isinstance(brief, dict):
                    return cast("JsonObject", brief)
        return {}

    async def get_agent_info(self, project_id: str) -> ProjectBrief:
        """Read a project's Agent brief (instruction cards + master switch).

        There is NO ``GET /v1/projects/{id}/agentInfo`` (it 404s). The brief is
        read from the ``agentInfo`` block of ``flow.projectInitialData`` — the
        same tRPC query ``list_characters`` uses. Session-cookie auth; FREE — no
        reCAPTCHA, no credit. This is the read half of the ``gflow instructions``
        read-modify-write cycle (the server is the source of truth).
        """
        from gflow_cli.api.image import ProjectBrief

        trpc_input = json.dumps({"json": {"projectId": project_id}}, separators=(",", ":"))
        url = f"{routes.PROJECT_INITIAL_DATA_URL}?input={quote(trpc_input, safe='')}"
        data = await self._get_json(url, route_name="projectInitialData")
        inner = _unwrap_trpc(data)
        agent_info = inner.get("agentInfo")
        typed = cast("JsonObject", agent_info) if isinstance(agent_info, dict) else None
        return ProjectBrief.from_agent_info(typed)

    async def upload_image(self, project_id: str, image_path: Path) -> AssetInfo:
        """Upload an image into a Flow project's library.

        Maps to `POST /v1/flow/uploadImage`. Image bytes go in base64.
        Returns the asset UUID + dimensions Flow inferred.

        Validates BEFORE reading the full file:

        * **Size cap** — files larger than ``MAX_IMAGE_BYTES`` (20 MB, matching
          Flow's UI limit) are rejected. Prevents OOM on accidental uploads of
          huge files and protects the remote endpoint from DoS-shaped traffic.
        * **Magic-byte check** — the first 12 bytes must match a PNG / JPEG /
          WebP / GIF signature. Stops users from silently exfiltrating
          arbitrary local files (e.g. ``~/.bashrc``, ``~/.ssh/id_rsa``) just
          because the path resolved cleanly.

        Both validations run before ``image_path.read_bytes()`` so a hostile
        path is never fully loaded into memory.
        """
        await validate_image_file(image_path)
        full_bytes = await asyncio.to_thread(image_path.read_bytes)
        b64 = base64.b64encode(full_bytes).decode()
        body = {
            "clientContext": {"projectId": project_id, "tool": "PINHOLE"},
            "imageBytes": b64,
        }
        data = await self._post_json(routes.UPLOAD_IMAGE, body)
        return AssetInfo.from_upload_response(data)

    async def upload_reference(self, project_id: str, path: Path) -> ImageRef:
        """Upload a local image into ``project_id`` once, as a reference to use in place.

        Returns an ``ImageRef`` for an image now in the project (``in_project=True``),
        so every later use is a mention, never another upload (#913). The transport
        uploads it when it drives the host Flow served (flow.google.com: the composer
        toolbar, which names the media id and a run-unique caption); otherwise the REST
        upload. The file is checked first either way (size, image magic bytes).
        """
        from gflow_cli.api.image import ImageRef  # noqa: PLC0415 - typing-only at module level

        ref = await self.upload_reference_in_place(project_id, path)
        if ref is not None:
            return ref
        asset = await self.upload_image(project_id, path)
        return ImageRef(name=asset.name, display_name=asset.display_name, in_project=True)

    async def upload_reference_in_place(self, project_id: str, path: Path) -> ImageRef | None:
        """:meth:`upload_reference` without the REST fallback: ``None`` where the host Flow
        served has no in-place upload (labs), so the caller keeps its own path (#772)."""
        await validate_image_file(path)
        uploader = cast(
            "Callable[..., Awaitable[ImageRef | None]] | None",
            getattr(self.transport, "upload_reference", None),
        )
        return None if uploader is None else await uploader(project_id=project_id, path=path)

    async def archive_workflow(self, workflow_id: str, project_id: str) -> None:
        """Soft-delete (archive) a workflow — used by clear-library tooling.

        Maps to `PATCH /v1/flowWorkflows/{id}` with `metadata.archived=true`.
        """
        url = f"{routes.ARCHIVE_WORKFLOW_BASE}/{workflow_id}"
        body = {
            "workflow": {
                "name": workflow_id,
                "projectId": project_id,
                "metadata": {"archived": True},
            },
            "updateMask": "metadata.archived",
        }
        await self._patch_json(url, body)

    async def commit_workflow(
        self, workflow_id: str, *, project_id: str, primary_media_id: str
    ) -> None:
        """Commit a workflow's primaryMediaId so it can be placed in a scene.

        PATCH /v1/flowWorkflows/{id}, updateMask metadata.primaryMediaId.
        Auth handled by the _patch_json Bearer path.
        """
        body = {
            "workflow": {
                "name": workflow_id,
                "projectId": project_id,
                "metadata": {"primaryMediaId": primary_media_id},
            },
            "updateMask": "metadata.primaryMediaId",
        }
        await self._patch_json(
            routes.flow_workflow_url(workflow_id), body, route_name="commitWorkflow"
        )

    async def create_scene(self, *, project_id: str, workflow_ids: list[str]) -> Scene:
        """Compose a scene from an ordered list of source workflowIds.

        POST /v1/flow/projects/{pid}/scenes. Repeat an id to clone a clip.
        """
        data = await self._post_json(
            routes.scenes_url(project_id),
            {"workflowIds": list(workflow_ids)},
            route_name="createScene",
        )
        return Scene.from_create_response(data, project_id=project_id)

    async def update_scene_workflows(
        self, *, scene_id: str, project_id: str, workflows: list[SceneWorkflow]
    ) -> None:
        """Set per-clip order + trim. POST /v1/flow/scene/sceneWorkflows:update."""
        body = {
            "sceneId": scene_id,
            "projectId": project_id,
            "sceneWorkflows": [w.to_wire(scene_id=scene_id) for w in workflows],
        }
        await self._post_json(
            routes.SCENE_WORKFLOWS_UPDATE, body, route_name="updateSceneWorkflows"
        )

    async def get_scene_workflows(self, scene_id: str, *, project_id: str) -> Scene:
        """Read back a scene's clips (order + trims). GET via _get_json."""
        data = await self._get_json(
            routes.scene_workflows_url(scene_id, project_id), route_name="getSceneWorkflows"
        )
        return Scene.from_get_response(data, scene_id=scene_id, project_id=project_id)

    async def _poll_concat_until_done(
        self,
        operation: Any,
        *,
        poll_interval: float,
        deadline: float,
        timeout_s: float,
    ) -> str:
        """Poll ``runVideoFxCheckConcatenationStatus`` until successful.

        Returns the raw base64 ``encodedVideo`` string on success.
        Raises :class:`SceneConcatError` on job failure and
        :class:`TransportTimeoutError` on deadline breach.
        """
        while True:
            status_resp = await self._post_json(
                routes.RUN_VIDEO_FX_CHECK_CONCATENATION_STATUS,
                {"operation": operation},
                route_name="runVideoFxCheckConcatenationStatus",
            )
            status = str(status_resp.get("status", ""))
            if status == "MEDIA_GENERATION_STATUS_SUCCESSFUL":
                return str(status_resp.get("encodedVideo") or "")
            if status and status != "MEDIA_GENERATION_STATUS_ACTIVE":
                # FAILED / unspecified — detail from status ONLY, never encodedVideo.
                logger.warning("scene.concat_failed", status=status)
                raise SceneConcatError(
                    detail=f"concatenation job status: {status}",
                    route="runVideoFxCheckConcatenationStatus",
                )
            if time.monotonic() >= deadline:
                raise TransportTimeoutError(
                    f"scene concatenation did not finish within {timeout_s:.0f}s",
                    charged="no",  # a scene render spends no credits
                )
            await asyncio.sleep(poll_interval)

    @staticmethod
    def _decode_concat_video(encoded: str) -> bytes:
        """Validate size, base64-decode, and magic-byte-check a concat payload.

        Raises :class:`SceneConcatError` when the payload is absent, oversized,
        undecodable, or not a valid MP4.
        """
        if not encoded:
            raise SceneConcatError(
                detail="concatenation succeeded but returned no encodedVideo",
                route="runVideoFxCheckConcatenationStatus",
            )
        if len(encoded) > MAX_CONCAT_B64_LEN:
            # Reject before decode — never log the body (mitigation: no 20MB+ in logs).
            raise SceneConcatError(
                detail=(
                    f"concatenated video exceeds the {MAX_CONCAT_B64_LEN // (1024 * 1024)} MB "
                    "size cap; compose fewer/shorter clips"
                ),
                route="runVideoFxConcatenation",
            )
        try:
            video_bytes = base64.b64decode(encoded)
        except ValueError as e:  # binascii.Error subclasses ValueError
            # Don't include the (undecodable) body in the message.
            raise SceneConcatError(
                detail="concatenation returned undecodable video data",
                route="runVideoFxCheckConcatenationStatus",
            ) from e
        if video_bytes[4:8] != b"ftyp":
            raise SceneConcatError(detail="concatenation output is not a valid MP4")
        return video_bytes

    async def concatenate_scene(
        self,
        inputs: list[ConcatInput],
        *,
        out_path: Path,
        poll_interval: float = 3.0,
        timeout_s: float = 180.0,
    ) -> AnyPath:
        """Render a scene's clips into ONE extended MP4 via Flow's server-side
        concatenation. Credit-free, no reCAPTCHA, no ffmpeg.

        Pipeline: ``POST runVideoFxConcatenation`` → poll
        ``runVideoFxCheckConcatenationStatus`` (each poll is its own
        ``_post_json``, so the Page pool is free during the ``asyncio.sleep`` —
        no nested checkout, no deadlock) until ``MEDIA_GENERATION_STATUS_SUCCESSFUL``.
        The combined MP4 is returned inline as base64 in ``encodedVideo``.

        Writes to ``out_path`` (or the configured cloud ``storage_uri``) and
        returns the write target. Raises ``SceneConcatError`` if the job fails
        or returns no/invalid video, ``TransportTimeoutError`` on poll timeout.
        """
        if not inputs:
            msg = "concatenate_scene requires at least one clip"
            raise ValueError(msg)
        op = await self._post_json(
            routes.RUN_VIDEO_FX_CONCATENATION,
            {"inputVideos": [i.to_wire() for i in inputs]},
            route_name="runVideoFxConcatenation",
        )
        operation = op.get("operation")
        logger.info("scene.concat_started", clips=len(inputs))

        deadline = time.monotonic() + timeout_s
        encoded = await self._poll_concat_until_done(
            operation,
            poll_interval=poll_interval,
            deadline=deadline,
            timeout_s=timeout_s,
        )
        video_bytes = self._decode_concat_video(encoded)
        del encoded  # drop the ~20MB+ payload promptly
        logger.info("scene.concat_completed", bytes=len(video_bytes))

        # Write via the same storage_uri-aware path as download_image.
        target = self._write_target(out_path)
        # #1201: a scene render spends no credits, so a failed write is just a path to fix.
        await write_asset_async(target, video_bytes, charged="no", next_action="fix_input")
        return target

    async def fetch_project_listing(self, project_id: str) -> JsonObject:
        """Fetch the raw ``flow.projectInitialData`` listing for *project_id*.

        Maps to ``GET .../trpc/flow.projectInitialData?input=…`` with
        ``toolName="PINHOLE"`` — the Flow editor's own initial-data call.
        Live-verified 2026-08-16: ~0.5s, session-cookie auth, no page
        navigation, FREE (no reCAPTCHA, no credit). Returns the tRPC envelope
        verbatim; parsing lives in :mod:`gflow_cli.services.catalog_sync`
        (#543).

        Issued through the context's APIRequestContext (not a checked-out
        Page) so multi-project sync sweeps never contend on the page pool.

        Raises:
            ValueError: *project_id* is not a canonical UUID — harvested ids
                must never reach URL construction unvalidated.
            RuntimeError: client not entered (no browser context).
        """
        if not is_media_uuid(project_id):
            msg = f"Invalid project_id: {project_id!r}"
            raise ValueError(msg)
        ctx = self._context
        if ctx is None:
            msg = "FlowApiClient not entered — use `async with`"
            raise RuntimeError(msg)
        trpc_input = json.dumps(
            {"json": {"projectId": project_id, "toolName": "PINHOLE"}},
            separators=(",", ":"),
        )
        url = f"{routes.PROJECT_INITIAL_DATA_URL}?input={quote(trpc_input, safe='')}"
        route = "projectInitialData"

        async def attempt() -> Any:
            return await ctx.request.get(url, timeout=30_000)

        resp = await self._run_with_retry(attempt, route=route)
        text = await resp.text()
        _raise_for_non_retryable(resp, text, route=route)
        # Single-channel rule (docs/SECURITY.md): id + size only — never
        # display names / captions.
        logger.info(
            "client.project_listing_fetched",
            project_id=project_id,
            byte_count=len(text.encode("utf-8")),
        )
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            raise WireFormatError(
                detail=f"non-JSON projectInitialData response: {text[:200]}",
                status=resp.status,
                instance=_make_instance(),
                route=route,
                discovery=_build_wire_format_discovery(resp, text, route),
            ) from e
        if not isinstance(parsed, dict):
            raise WireFormatError(
                detail=(f"projectInitialData returned non-object JSON ({type(parsed).__name__})"),
                status=resp.status,
                instance=_make_instance(),
                route=route,
            )
        return cast("JsonObject", parsed)
