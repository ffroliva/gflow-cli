"""FlowProvider — drives the unofficial aisandbox-pa.googleapis.com REST API.

Auth strategy: piggyback on Playwright's persistent context. The user runs
`gflow auth login` once to capture Google session cookies; subsequent commands
launch a HEADLESS Playwright context using the same profile and call REST
endpoints via `page.request` (Playwright's HTTP client auto-attaches cookies).

This is hybrid: Playwright for auth + transport, no UI automation. Eliminates
the brittle DOM scraping the legacy worker used.

Routes captured (2026-05-09):
  POST   https://aisandbox-pa.googleapis.com/v1/flow/uploadImage
  POST   https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText
  POST   https://aisandbox-pa.googleapis.com/v1/video:batchCheckAsyncVideoGenerationStatus
  PATCH  https://aisandbox-pa.googleapis.com/v1/flowWorkflows/{id}
  POST   https://labs.google/fx/api/trpc/project.createProject
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import BrowserContext, async_playwright

from flow_cli.models import Asset, GenerationJob, GenerationRequest, JobStatus

logger = logging.getLogger(__name__)

FLOW_BASE = "https://aisandbox-pa.googleapis.com"
LABS_BASE = "https://labs.google"
GEMINI_URL = "https://labs.google/fx/tools/flow?hl=en"


class FlowProvider:
    """Calls Flow's private REST API via an authenticated Playwright context.

    NOT YET IMPLEMENTED — request body shapes and the auth-token extraction
    pattern are pending. Captured request bodies live in
    `samples/captured_requests.json` (sanitised) for reference.
    """

    name = "flow"

    def __init__(self, profile_dir: Path):
        self.profile_dir = profile_dir
        self._context: Optional[BrowserContext] = None
        self._project_id: Optional[str] = None

    async def __aenter__(self) -> "FlowProvider":
        pw = await async_playwright().start()
        self._pw = pw
        self._context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=True,
            viewport={"width": 1280, "height": 720},
        )
        return self

    async def __aexit__(self, *exc):
        if self._context:
            await self._context.close()
        if self._pw:
            await self._pw.stop()

    async def _ensure_project(self) -> str:
        """Create a fresh Flow project so uploads have a projectId. Cached per session."""
        if self._project_id:
            return self._project_id
        # TODO: POST /fx/api/trpc/project.createProject
        # Response includes projectId. Cache it.
        raise NotImplementedError("project.createProject not yet wired")

    async def upload_image(self, path: Path) -> Asset:
        """POST /v1/flow/uploadImage with base64-encoded image bytes."""
        project_id = await self._ensure_project()
        image_b64 = base64.b64encode(path.read_bytes()).decode()
        # TODO: POST /v1/flow/uploadImage
        # body: {"clientContext": {"projectId": project_id, "tool": "PINHOLE"},
        #        "imageBytes": image_b64}
        # response: {"media": {"name": "<uuid>"}, "workflow": {...}}
        del image_b64  # silence unused-var until implemented
        raise NotImplementedError("uploadImage not yet wired")

    async def start_generation(self, req: GenerationRequest) -> GenerationJob:
        """POST /v1/video:batchAsyncGenerateVideoText with start frame + prompt."""
        # TODO: derive start asset UUID from already-uploaded asset
        # TODO: POST body shape — see samples/captured_requests.json
        raise NotImplementedError("batchAsyncGenerateVideoText not yet wired")

    async def get_job(self, job_id: str) -> GenerationJob:
        """POST /v1/video:batchCheckAsyncVideoGenerationStatus — poll status."""
        raise NotImplementedError("batchCheckAsyncVideoGenerationStatus not yet wired")

    async def download(self, asset_or_url: Asset | str, out_path: Path) -> Path:
        """Fetch the rendered mp4 (or image) bytes via Playwright's request API."""
        # The download URL pattern is ".../trpc/media.getMediaUrlRedirect?name=<uuid>"
        # which 302s to a signed Cloud Storage URL.
        raise NotImplementedError("download not yet wired")
