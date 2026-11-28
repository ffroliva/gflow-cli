# pyright: reportPrivateUsage=false
"""``DirectHttpTransport``: measured requests sent direct; everything else ends ``NotSent``."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

import httpx
import structlog

from gflow_cli.api import flow_wire
from gflow_cli.api.generation_route import Route, is_bare_t2v, video_target
from gflow_cli.api.image import ImageRef
from gflow_cli.api.submit_policy import DirectAttemptEnded, RefusedNotBilled, not_sent
from gflow_cli.api.transports.direct_http._gateway import InFlightGate
from gflow_cli.api.transports.direct_http._images import run_direct_images, run_direct_upload
from gflow_cli.api.transports.direct_http._mint import PageTokenSource, ProjectTokens, _arm_page
from gflow_cli.api.transports.direct_http._route import (
    environment_reason,
    image_shape_direct,
    video_route,
)
from gflow_cli.api.transports.direct_http._upscale import UpscaleSource, run_direct_upscale
from gflow_cli.api.transports.direct_http._video import (
    StartFrame,
    VideoJob,
    run_direct_video,
    selected_target,
)
from gflow_cli.api.transports.frame_builder import centred_crop
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.video import Mode
from gflow_cli.errors import InsufficientCreditsError
from gflow_cli.paths import image_dimensions, jpeg_exif_orientation

if TYPE_CHECKING:
    from playwright.async_api import Page

    from gflow_cli.api.dto import GeneratedImage
    from gflow_cli.api.generation_route import VideoWireTarget
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.video import GenerateVideoRequest, VideoResult, VideoStartedCallback

log = structlog.get_logger(__name__)
_T = TypeVar("_T")


async def refuse_empty_balance(profile_dir: Path) -> None:
    """Exit 37 before any send when Flow reports no credits; a failed read skips the check.

    The read is browser-free (``nzlxg``, the ``gflow credits`` read) and runs before launch
    only: it reads the on-disk cookie jar, which the profile's Chrome holds while it runs.
    Only an empty balance is refused: no price is readable without the browser, so a partial
    balance is left to Flow (a price comparison is tracked separately).
    """
    try:
        balance = (await flow_wire.read_credits(profile_dir)).credits
    except Exception as exc:  # noqa: BLE001 - an unreadable balance never blocks a submit
        log.info("direct.balance_unread", cause=type(exc).__name__)
        return
    if balance <= 0:
        raise InsufficientCreditsError(
            detail="Flow reports a balance of 0 credits; nothing was sent.",
            route="batchexecute:nzlxg",
        )


class DirectHttpTransport(UiAutomationTransport):
    """The UI transport, with measured image requests sent as one direct ``ogiZ0b`` POST."""

    name = "direct_http"
    _profile_dir: Path | None = None

    def __init__(self, *, http_transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__()
        self._http_transport = http_transport
        self._gate = InFlightGate()
        self._token_source: PageTokenSource | None = None
        self._poll_interval_s = 5.0  # between jwpduf polls, as the spike measured

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
        """One attempt inside the in-flight gate; a refusal discards the page that minted.

        An attempt that waited at the gate while the client relaunched finds this transport
        torn down when the gate reopens: it ends ``NotSent("torn_down")`` before anything is
        minted or sent, and the retry loop runs it again on the new transport."""
        try:
            async with self._gate.slot():
                if not self._setup_done or self._page is None:
                    raise not_sent("torn_down")
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
        on_before_submit: Callable[[], None] | None = None,
    ) -> list[GeneratedImage]:
        """Generate via one direct POST when the request is measured.

        Every other outcome raises ``DirectAttemptEnded`` for the retry loop
        (``api/_flow_retry.py``): ``NotSent`` for a shape or session the direct path cannot
        serve, ``RefusedNotBilled`` and ``Indeterminate`` past the send. The UI path never
        runs here, on a page that may be headless or refused; the loop calls
        :meth:`generate_images_ui` on a fresh page instead. ``on_before_submit`` runs
        immediately before the billed POST.
        """
        del name_resolver  # the direct frame carries no mention text to resolve
        if not self._setup_done or self._page is None:
            msg = "DirectHttpTransport.setup() must be called before generate_images()"
            raise RuntimeError(msg)

        page, profile_dir = self._page, self._profile_dir
        reason = self._why_not_direct(request, project_id)
        if reason is not None or project_id is None or profile_dir is None:
            raise not_sent(reason or "no_project")
        source = self._source_for(page)
        tokens = source.for_project(project_id)
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
                on_before_submit=on_before_submit,
            ),
        )
        # The page stays armed: no about:blank park after a direct call.
        self.last_image_route = "migrated"
        return images

    async def generate_images_ui(
        self,
        *,
        project_id: str | None,
        request: GenerateImageRequest,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> list[GeneratedImage]:
        """The inherited UI path, for the retry loop's fallback when nothing was sent."""
        async with self._generate_lock:
            self._raise_if_torn_down()
            await self.park_deferred_page()
            return await self._generate_images_locked(
                request, project_id=project_id, name_resolver=name_resolver
            )

    async def upload_reference(self, *, project_id: str, path: Path) -> ImageRef | None:
        """Upload ``path`` with one direct ``maseQ`` POST.

        Where the direct upload cannot start (no session, not flow.google.com, a proxy) it
        raises ``DirectAttemptEnded(NotSent)``; the retry loop then calls
        :meth:`upload_reference_ui` on a fresh page.
        """
        if not self._setup_done or self._page is None:
            msg = "DirectHttpTransport.setup() must be called before upload_reference()"
            raise RuntimeError(msg)
        page, profile_dir = self._page, self._profile_dir
        reason = self._why_not_direct_upload()
        if reason is not None or profile_dir is None:
            raise not_sent(reason or "no_profile_dir")
        tokens = self._source_for(page).for_project(project_id)
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

    async def upload_reference_ui(self, *, project_id: str, path: Path) -> ImageRef | None:
        """The inherited UI upload, for the retry loop's fallback when nothing was sent."""
        return await super().upload_reference(project_id=project_id, path=path)

    async def generate_video(
        self,
        *,
        request: GenerateVideoRequest,
        project_id: str | None = None,
        out_dir: Path | None = None,
        poll_timeout_s: float = 600.0,
        download: bool = True,
        on_started: VideoStartedCallback | None = None,
        name_resolver: Callable[[str], str | None] | None = None,
        on_before_submit: Callable[[], None] | None = None,
        frame_uploads: dict[Path, str] | None = None,
    ) -> VideoResult:
        """Generate one clip over direct HTTP when its key tuple is measured.

        The page is armed first; a bare t2v (no model) is then keyed from the account's
        selection, read on it before any mint; an unread or unmeasured selection ends
        ``NotSent("t2v_key_unresolved")``.

        As :meth:`generate_images`: every other outcome raises ``DirectAttemptEnded`` for
        the retry loop, which runs :meth:`generate_video_ui` on a fresh page when nothing
        was sent. An i2v start frame is uploaded with ``maseQ`` once per run:
        ``frame_uploads`` (path to media id) is kept by the caller across a retry, so a
        retry after a refusal reuses the stored upload. The zero-balance check runs before
        launch (:func:`refuse_empty_balance`), not here.
        """
        del name_resolver  # the direct frame carries no mention text to resolve
        if not self._setup_done or self._page is None:
            msg = "DirectHttpTransport.setup() must be called before generate_video()"
            raise RuntimeError(msg)
        page, profile_dir = self._page, self._profile_dir
        reason = self._why_not_direct_video(request, project_id)
        if reason or project_id is None or profile_dir is None:
            raise not_sent(reason or "no_profile_dir")
        source = self._source_for(page)
        request, target = await self._armed_target(source.page, project_id, request)
        if target is None:  # unreachable: _routes_direct keyed it
            raise not_sent("unsupported_shape")
        tokens = source.for_project(project_id)
        start: StartFrame | None = None
        if request.mode is Mode.I2V:
            start = await self._start_frame(project_id, request, target.aspect_slot, frame_uploads)
        job = VideoJob(
            request=request,
            target=target,
            start=start,
            save=partial(self._save_clip, out_dir=out_dir),
            download=download,
            poll_timeout_s=poll_timeout_s,
            poll_interval_s=self._poll_interval_s,
            on_started=on_started,
        )
        return await self._run_direct(
            tokens,
            lambda: run_direct_video(
                tokens=tokens,
                page=page,
                profile_dir=profile_dir,
                project_id=project_id,
                job=job,
                http_transport=self._http_transport,
                on_before_submit=on_before_submit,
            ),
        )

    async def generate_video_ui(
        self,
        *,
        request: GenerateVideoRequest,
        project_id: str | None = None,
        out_dir: Path | None = None,
        poll_timeout_s: float = 600.0,
        download: bool = True,
        on_started: VideoStartedCallback | None = None,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> VideoResult:
        """The inherited UI path, for the retry loop's fallback when nothing was sent."""
        return await super().generate_video(
            request=request,
            project_id=project_id,
            out_dir=out_dir,
            poll_timeout_s=poll_timeout_s,
            download=download,
            on_started=on_started,
            name_resolver=name_resolver,
        )

    async def upscale_video_direct(
        self,
        *,
        project_id: str,
        source: UpscaleSource,
        on_before_submit: Callable[[], None] | None = None,
    ) -> bytes:
        """Upscale a catalogued clip to 1080p with one direct ``p0UkFb`` POST; its mp4 bytes.

        The caller routes first (:func:`upscale_source`). As :meth:`generate_video`, every
        other outcome raises ``DirectAttemptEnded`` for the retry loop, whose UI step is the
        download menu. It costs 0 credits, so no balance is read.
        """
        if not self._setup_done or self._page is None:
            msg = "DirectHttpTransport.setup() must be called before upscale_video_direct()"
            raise RuntimeError(msg)
        page, profile_dir = self._page, self._profile_dir
        reason = self._why_not_direct_upload()
        if reason or profile_dir is None:
            raise not_sent(reason or "no_profile_dir")
        tokens = self._source_for(page).for_project(project_id)
        return await self._run_direct(
            tokens,
            lambda: run_direct_upscale(
                tokens=tokens,
                page=page,
                profile_dir=profile_dir,
                project_id=project_id,
                source=source,
                poll_interval_s=self._poll_interval_s,
                http_transport=self._http_transport,
                on_before_submit=on_before_submit,
            ),
        )

    async def _start_frame(
        self,
        project_id: str,
        request: GenerateVideoRequest,
        aspect_slot: int,
        frame_uploads: dict[Path, str] | None,
    ) -> StartFrame:
        """The start frame's media id (uploaded once per run) and its centred crop box."""
        path = request.start_image
        if path is None:  # pragma: no cover - video_target routes an in-project frame to the UI
            raise not_sent("start_frame_not_local")
        data = await asyncio.to_thread(path.read_bytes)
        if jpeg_exif_orientation(data) >= 5:  # a quarter turn: the stored box is not the shown one
            raise not_sent("start_frame_exif_rotated")
        size = image_dimensions(data)
        if size is None:
            raise not_sent("start_frame_dimensions")
        uploads = {} if frame_uploads is None else frame_uploads
        if path not in uploads:
            ref = await self.upload_reference(project_id=project_id, path=path)
            if ref is None:  # pragma: no cover - the direct upload returns a ref or raises
                raise not_sent("start_frame_upload")
            uploads[path] = ref.name
        return (uploads[path], centred_crop(*size, aspect_slot=aspect_slot))

    def _why_not_direct_upload(self) -> str | None:
        return "no_profile_dir" if self._profile_dir is None else environment_reason()

    def _why_not_direct_video(
        self, request: GenerateVideoRequest, project_id: str | None
    ) -> str | None:
        reason = self._why_not_direct_upload()
        if reason:
            return reason
        if video_route(request) is not Route.DIRECT:
            return "unsupported_shape"
        return "no_project" if project_id is None else None

    async def _armed_target(
        self, page: Page, project_id: str, request: GenerateVideoRequest
    ) -> tuple[GenerateVideoRequest, VideoWireTarget | None]:
        """Arm the page, then key the request: a bare t2v from the account's selection."""
        async with self._generate_lock:  # the page lock, as the mint holds it
            await _arm_page(page, project_id)
            if is_bare_t2v(request):
                return await selected_target(page, request)
            return request, video_target(request)

    def _why_not_direct(self, request: GenerateImageRequest, project_id: str | None) -> str | None:
        if not image_shape_direct(request):
            return "unsupported_shape"
        if project_id is None:
            return "no_project"
        return self._why_not_direct_upload()
