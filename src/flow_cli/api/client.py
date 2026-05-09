"""FlowApiClient — typed wrapper around Flow's private REST surface.

Architecture: the client manages its own Playwright persistent-context
lifecycle (async context manager). All HTTP goes through `page.request`
so Google's session cookies attach automatically — no manual bearer-token
extraction.

The video-generation route requires a fresh reCAPTCHA token per call;
that piece lives in `flow_cli.api.recaptcha` and `generate_video()` (added
in a later commit). For now this client implements the four routes that
DON'T need reCAPTCHA: createProject, uploadImage, checkStatus, download.

Usage:
    async with FlowApiClient(profile_dir) as client:
        project = await client.create_project()
        asset = await client.upload_image(project.project_id, Path("hero.png"))
        ...
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from flow_cli.api import routes
from flow_cli.api.dto import AssetInfo, ProjectInfo, VideoOperation, VideoStatus
from flow_cli.api.recaptcha import TokenMinter
from flow_cli.api.video import GenerateVideoRequest, build_generate_body

logger = logging.getLogger(__name__)


class FlowApiError(RuntimeError):
    """Raised when a Flow API call returns a non-2xx response."""

    def __init__(self, status: int, body: str, *, route: str):
        self.status = status
        self.body = body
        self.route = route
        super().__init__(f"Flow API {route} -> HTTP {status}: {body[:200]}")


class FlowApiClient:
    """Async context-managed client for Flow's REST surface.

    Holds a Playwright persistent context and a single page (used as the HTTP
    transport via `page.request`). Auth = whatever cookies the profile dir
    has from a prior `gflow auth login`.
    """

    def __init__(self, profile_dir: Path, *, headless: bool = True):
        self.profile_dir = profile_dir
        self.headless = headless
        self._pw: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    # --- lifecycle --------------------------------------------------------

    async def __aenter__(self) -> FlowApiClient:
        self._pw = await async_playwright().start()
        self._context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            viewport={"width": 1280, "height": 720},
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        self._page = (
            self._context.pages[0] if self._context.pages else await self._context.new_page()
        )
        # Bootstrap navigation so cookies + JS context are loaded before any
        # API call. Many endpoints 401 if you POST cold without an active page.
        await self._page.goto(
            routes.EDITOR_BOOTSTRAP_URL, wait_until="domcontentloaded", timeout=60_000
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("FlowApiClient not entered — use `async with`")
        return self._page

    # --- private HTTP helpers --------------------------------------------

    async def _post_json(
        self, url: str, body: dict[str, Any], *, content_type: str = "text/plain;charset=UTF-8"
    ) -> Any:
        """POST a JSON body. aisandbox-pa requires text/plain content-type
        (not application/json) — see samples/captured/*.json. The tRPC
        host on labs.google accepts standard application/json."""
        body_str = json.dumps(body)
        logger.debug("POST %s body=%s", url, body_str[:300])
        resp = await self.page.request.post(
            url,
            data=body_str,
            headers={"content-type": content_type},
        )
        text = await resp.text()
        if resp.status >= 400:
            raise FlowApiError(resp.status, text, route=url)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise FlowApiError(resp.status, f"non-JSON response: {text[:200]}", route=url) from e

    async def _patch_json(self, url: str, body: dict[str, Any]) -> Any:
        body_str = json.dumps(body)
        logger.debug("PATCH %s body=%s", url, body_str[:300])
        resp = await self.page.request.patch(
            url,
            data=body_str,
            headers={"content-type": "text/plain;charset=UTF-8"},
        )
        text = await resp.text()
        if resp.status >= 400:
            raise FlowApiError(resp.status, text, route=url)
        try:
            return json.loads(text) if text else {}
        except json.JSONDecodeError:
            return {}

    # --- public API -------------------------------------------------------

    async def create_project(self, title: str | None = None) -> ProjectInfo:
        """Bootstrap a fresh Flow project. Title defaults to a timestamp.

        Maps to `POST .../trpc/project.createProject`.
        """
        title = title or _default_project_title()
        body = {"json": {"projectTitle": title, "toolName": "PINHOLE"}}
        data = await self._post_json(routes.CREATE_PROJECT, body, content_type="application/json")
        return ProjectInfo.from_create_response(data)

    async def upload_image(self, project_id: str, image_path: Path) -> AssetInfo:
        """Upload an image into a Flow project's library.

        Maps to `POST /v1/flow/uploadImage`. Image bytes go in base64.
        Returns the asset UUID + dimensions Flow inferred.
        """
        if not image_path.exists():
            raise FileNotFoundError(image_path)
        b64 = base64.b64encode(image_path.read_bytes()).decode()
        body = {
            "clientContext": {"projectId": project_id, "tool": "PINHOLE"},
            "imageBytes": b64,
        }
        data = await self._post_json(routes.UPLOAD_IMAGE, body)
        return AssetInfo.from_upload_response(data)

    async def get_video_status(self, project_id: str, media_names: list[str]) -> list[VideoStatus]:
        """Poll the status of one or more in-flight video generations.

        Maps to `POST /v1/video:batchCheckAsyncVideoGenerationStatus`.
        """
        body = {"media": [{"name": n, "projectId": project_id} for n in media_names]}
        data = await self._post_json(routes.CHECK_VIDEO_STATUS, body)
        return [VideoStatus.from_check_status_item(it) for it in data.get("media", [])]

    async def download(self, name_or_url: str, out_path: Path) -> Path:
        """Download an asset (image or video) to `out_path`. Returns out_path."""
        url = (
            name_or_url
            if name_or_url.startswith("http")
            else routes.media_download_url(name_or_url)
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        resp = await self.page.request.get(url, max_redirects=5, timeout=120_000)
        if resp.status >= 400:
            raise FlowApiError(resp.status, await resp.text(), route=url)
        out_path.write_bytes(await resp.body())
        return out_path

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

    async def generate_video(
        self,
        *,
        project_id: str,
        req: GenerateVideoRequest,
        seed: int | None = None,
        recaptcha_action: str = "videoGeneration",
        batch_id: str | None = None,
    ) -> VideoOperation:
        """Enqueue a Veo video generation. Returns the operation reference.

        Mints a fresh reCAPTCHA token via the live page session before
        submitting. Caller polls completion via `get_video_status`.
        """
        minter = TokenMinter(self.page)
        token = await minter.mint(recaptcha_action)
        body = build_generate_body(
            req,
            project_id=project_id,
            recaptcha_token=token,
            batch_id=batch_id or _new_batch_id(),
            seed=seed if seed is not None else secrets.randbelow(2**31),
            session_id=f";{int(time.time() * 1000)}",
        )
        data = await self._post_json(routes.GENERATE_VIDEO, body)
        return VideoOperation.from_generate_response(data)


def _default_project_title() -> str:
    return datetime.now().strftime("flow-cli %b %d, %I:%M %p")


def _new_batch_id() -> str:
    """Generate a fresh batch ID for the mediaGenerationContext."""
    return str(uuid.uuid4())
