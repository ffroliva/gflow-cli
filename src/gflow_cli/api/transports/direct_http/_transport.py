# pyright: reportPrivateUsage=false
"""``DirectHttpTransport``: the UI transport, with measured requests sent direct."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

import httpx
import structlog

from gflow_cli.api.generation_route import direct_can_serve
from gflow_cli.api.image import ImageRef
from gflow_cli.api.submit_policy import DirectAttemptEnded, NotSent, RefusedNotBilled
from gflow_cli.api.transports.direct_http._gateway import InFlightGate
from gflow_cli.api.transports.direct_http._images import run_direct_images, run_direct_upload
from gflow_cli.api.transports.direct_http._mint import PageTokenSource, ProjectTokens
from gflow_cli.api.transports.direct_http._session import _proxy_configured
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.config import UiMode, get_settings, resolve_ui_mode

if TYPE_CHECKING:
    from playwright.async_api import Page

    from gflow_cli.api.dto import GeneratedImage
    from gflow_cli.api.image import GenerateImageRequest

log = structlog.get_logger(__name__)
_T = TypeVar("_T")


def _fallback_reason(ended: DirectAttemptEnded) -> str:
    """The fallback reason when nothing reached Flow; past the send, the attempt's error."""
    if isinstance(ended.outcome, NotSent):
        return ended.outcome.reason
    if ended.error is None:
        raise ended
    raise ended.error from ended


class DirectHttpTransport(UiAutomationTransport):
    """The UI transport, with measured image requests sent as one direct ``ogiZ0b`` POST."""

    name = "direct_http"
    _profile_dir: Path | None = None

    def __init__(self, *, http_transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__()
        self._http_transport = http_transport
        self._gate = InFlightGate()
        self._token_source: PageTokenSource | None = None

    async def setup(self, profile_dir: Path, *, page: Page | None = None) -> None:
        self._profile_dir = profile_dir
        await super().setup(profile_dir, page=page)
        self._token_source = None if self._page is None else self._new_token_source(self._page)

    def quiesce(self) -> AbstractAsyncContextManager[None]:
        """Block new direct attempts and wait for the in-flight ones (``SupportsQuiesce``)."""
        # Lock order: quiesce first, then ``_generate_lock`` (a mint holds the lock inside a
        # gate slot, so taking the lock before quiescing can deadlock on that mint).
        return self._gate.quiesce()

    def _new_token_source(self, page: Page) -> PageTokenSource:
        # The page lock is the UI path's own: a mint never shares the page with a UI run.
        return PageTokenSource(page, lock=self._generate_lock)

    def _source_for(self, page: Page) -> PageTokenSource:
        if self._token_source is None:  # set up without setup() (a test, a caller's page)
            self._token_source = self._new_token_source(page)
        return self._token_source

    async def _run_direct(self, tokens: ProjectTokens, run: Callable[[], Awaitable[_T]]) -> _T:
        """One attempt inside the in-flight gate; a refusal discards the page that minted."""
        try:
            async with self._gate.slot():
                return await run()
        except DirectAttemptEnded as ended:
            if isinstance(ended.outcome, RefusedNotBilled):
                tokens.discard()
            raise

    async def generate_images(
        self,
        *,
        project_id: str | None,
        request: GenerateImageRequest,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> list[GeneratedImage]:
        """Generate via one direct POST when the request is measured; otherwise, or when
        nothing was sent, through the inherited UI path. Past the send, never the UI."""
        if not self._setup_done or self._page is None:
            msg = "DirectHttpTransport.setup() must be called before generate_images()"
            raise RuntimeError(msg)

        page, profile_dir = self._page, self._profile_dir
        reason = self._why_not_direct(request, project_id)
        if reason is None and project_id is not None and profile_dir is not None:
            source = self._source_for(page)
            tokens = source.for_project(project_id)
            try:
                images = await self._run_direct(
                    tokens,
                    lambda: run_direct_images(
                        tokens=tokens,
                        page=page,
                        profile_dir=profile_dir,
                        project_id=project_id,
                        request=request,
                        http_transport=self._http_transport,
                        page_lock=self._generate_lock,
                        armed_page=lambda: source.page,
                    ),
                )
            except DirectAttemptEnded as ended:
                reason = _fallback_reason(ended)
            else:
                # The page stays armed: no about:blank park after a direct call.
                self.last_image_route = "migrated"
                return images
        log.info("direct.fallback_to_ui", reason=reason)
        async with self._generate_lock:
            await self.park_deferred_page()
            return await self._generate_images_locked(
                request, project_id=project_id, name_resolver=name_resolver
            )

    async def upload_reference(self, *, project_id: str, path: Path) -> ImageRef | None:
        """Upload ``path`` with one direct ``maseQ`` POST; the UI upload when nothing was
        sent (no session, no token, not flow.google.com, a proxy, a refused session)."""
        if not self._setup_done or self._page is None:
            msg = "DirectHttpTransport.setup() must be called before upload_reference()"
            raise RuntimeError(msg)
        page, profile_dir = self._page, self._profile_dir
        reason = self._why_not_direct_upload()
        if reason is None and profile_dir is not None:
            tokens = self._source_for(page).for_project(project_id)
            try:
                return await self._run_direct(
                    tokens,
                    lambda: run_direct_upload(
                        tokens=tokens,
                        page=page,
                        profile_dir=profile_dir,
                        project_id=project_id,
                        path=path,
                        http_transport=self._http_transport,
                    ),
                )
            except DirectAttemptEnded as ended:
                reason = _fallback_reason(ended)
        log.info("direct.upload_fallback_to_ui", reason=reason)
        return await super().upload_reference(project_id=project_id, path=path)

    def _why_not_direct_upload(self) -> str | None:
        if self._profile_dir is None:
            return "no_profile_dir"
        if get_settings().flow_host == "labs.google":
            return "flow_host_labs"
        if _proxy_configured():
            return "proxy_configured"
        return None

    def _why_not_direct(self, request: GenerateImageRequest, project_id: str | None) -> str | None:
        # generation_route's predicate is pure, so the GFLOW_CLI_UI_MODE setting is read here.
        if not direct_can_serve(request) or resolve_ui_mode(request.ui_mode) is UiMode.AGENTIC:
            return "unsupported_shape"
        if project_id is None:
            return "no_project"
        if self._profile_dir is None:
            return "no_profile_dir"
        if get_settings().flow_host == "labs.google":
            return "flow_host_labs"
        if _proxy_configured():
            return "proxy_configured"
        return None
