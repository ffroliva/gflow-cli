"""Generation surface: images and videos, the reCAPTCHA mint, status polling, downloads, credits.

Split out of ``client.py`` (#1249); ``FlowApiClient`` composes it. Method
bodies are unchanged.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import time
from dataclasses import replace as _dataclass_replace
from typing import TYPE_CHECKING, Any, TypeVar, cast
from urllib.parse import urlsplit, urlunsplit

import structlog

from gflow_cli.api import routes
from gflow_cli.api._engine import (
    mint_evaluate_kwargs,
)
from gflow_cli.api._retry import MAX_ATTEMPTS, post_with_retry
from gflow_cli.api.dto import (
    CreditsInfo,
    GeneratedImage,
    GenerationCheckpoint,
    GenerationCheckpointObserver,
)
from gflow_cli.api.recaptcha import TokenMinter
from gflow_cli.api.transports._common import (
    raise_if_migrated,
)
from gflow_cli.api.transports.base import (
    VideoCapableTransport,
)
from gflow_cli.api.video import (
    VideoStatus,
    parse_video_status,
)
from gflow_cli.errors import (
    CONTENT_SAFETY_REASONS,
    ContentPolicyError,
    FlowApiError,  # re-exported via gflow_cli.api.__init__
    FlowHostMigratedError,
    NetworkError,
    TransportTimeoutError,
    WireFormatError,
)
from gflow_cli.paths import adjust_key_extension, looks_like_video
from gflow_cli.storage import AnyPath, storage_path, write_asset_async

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from _typeshed import DataclassInstance

    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.video import (
        GenerateVideoRequest,
        VideoResult,
        VideoStarted,
        VideoStartedCallback,
    )
from gflow_cli.api._flow_session import (
    JsonObject,
    _make_instance,
    _raise_for_non_retryable,
    _storage_key_from_path,
)
from gflow_cli.api._flow_workspace import _MIGRATED_FLOW_HOST, WorkspaceSurface

logger = structlog.get_logger(__name__)

# Floor for the outbound video status poll. The cheapest extend model takes ~110s,
# so a short interval buys nothing and spends requests against a WAF-scored host.
_MIN_VIDEO_POLL_INTERVAL_S = 5.0

_DataclassT = TypeVar("_DataclassT", bound="DataclassInstance")


def _dc_replace(obj: _DataclassT, /, **changes: Any) -> _DataclassT:
    """Typed wrapper over ``dataclasses.replace`` that preserves the input type.

    The stdlib annotation for ``replace`` is opaque to some analyzers (they infer
    a generic ``DataclassInstance``), which produced spurious argument-type and
    declared-type findings at call sites. Re-declaring it with a ``TypeVar`` makes
    the return type flow through as the concrete request dataclass.
    """
    return _dataclass_replace(obj, **changes)


# #692: how long the post-failure migration re-check keeps watching `page.url`
# for a hop that was still committing when the mint died. Error path only, so a
# successful mint never waits. Small enough that a genuine labs-side reCAPTCHA
# failure is not meaningfully delayed on its way to the user.
_MIGRATION_RECHECK_BUDGET_S = 1.5
_MIGRATION_RECHECK_POLL_S = 0.25


def _strip_query(url: str) -> str:
    """Return ``url`` with its query string and fragment removed.

    Signed CDN URLs from ``flow-content.google`` carry a time-limited
    ``Signature=...&Expires=...`` query — that's a bearer-style credential
    for the resource. We strip it before passing the URL to ``FlowApiError``
    so it cannot leak via ``str(exc)`` or any log line that formats the
    exception. See docs/SECURITY.md ("No cookies, no tokens, no API keys").
    """
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _validate_fife_url(url: str) -> None:
    """Reject non-HTTPS URLs and hosts outside Google's CDN namespace.

    SSRF guard: ``GeneratedImage.fife_url`` is parsed verbatim from the
    server response. If that response is tampered with (or the DTO is
    constructed from untrusted input), the URL could point to internal
    services (``http://169.254.169.254/``, ``http://localhost:6006/``,
    ``http://127.0.0.1/``, ...). Playwright's ``max_redirects`` does not
    constrain the redirect target host, so this up-front check is paired with
    :func:`_validate_fife_final_url` on the host the response ended on.

    Allowlist: scheme must be ``https`` AND host must be ``flow-content.google``
    or any subdomain of ``.google``. The captured samples (see
    ``samples/captured/06_batchGenerateImages.json``) only ever serve from
    ``flow-content.google``; the ``.google`` allowance is a small concession
    for any future CDN swap that stays inside Google's TLD.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        msg = f"Refusing non-HTTPS download URL: scheme={parts.scheme!r}"
        raise ValueError(msg)
    host = parts.hostname or ""
    # flow.google.com: agent-only image tiles serve from an opaque /asb/ URL there (2026-10-04).
    if not (
        host == "flow-content.google" or host.endswith(".google") or host == _MIGRATED_FLOW_HOST
    ):
        msg = f"Refusing download from unexpected host: {host!r}"
        raise ValueError(msg)


def _validate_fife_final_url(url: str) -> None:
    """Refuse a download whose redirects ended off Google's hosts (SSRF guard, part 2).

    ``flow.google.com/asb/...`` answers with a redirect to ``lh3.google.com``, so the
    final host is checked against the same allowlist the agent-only clip download uses.
    """
    from gflow_cli.api.transports.ui_automation import (  # noqa: PLC0415 - cycle
        _is_allowed_download_host,  # pyright: ignore[reportPrivateUsage]
    )

    if _is_allowed_download_host(url):
        return
    try:
        _validate_fife_url(url)
    except ValueError:
        parts = urlsplit(url)
        msg = f"Refusing image download redirected to {parts.scheme}://{parts.hostname!r}"
        raise ValueError(msg) from None


class GenerationSurface(WorkspaceSurface):
    """Generation surface: images and videos, the reCAPTCHA mint, status polling, downloads,
    credits.
    """

    async def get_credits(self) -> CreditsInfo:
        """Return the profile's current Flow credit balance.

        Read from flow.google.com's own ``nzlxg`` reply (#795): the labs session that
        issued the token for ``GET /v1/credits`` answers 410 to every account since
        2026-10-22, and the app on flow.google.com sends no Bearer to aisandbox-pa.
        """
        from gflow_cli.api.transports import migrated_composer  # noqa: PLC0415

        page = await self._checkout_page()
        try:
            return await migrated_composer.read_credits(page)
        finally:
            self._checkin_page(page)

    async def download(self, name_or_url: str, out_path: Path) -> Path:
        """Download an asset (image or video) to `out_path`. Returns out_path.

        Retries 5xx (transient CDN hiccups) via the tenacity layer. 429 from
        Google's CDN on signed URLs is rare-to-impossible in practice but the
        retry predicate handles it uniformly if it ever happens.
        """
        url = (
            name_or_url
            if name_or_url.startswith("http")
            else routes.media_download_url(name_or_url)
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        route = "mediaDownload"

        async def attempt() -> Any:
            page = await self._checkout_page()
            try:
                return await page.request.get(url, max_redirects=5, timeout=120_000)
            finally:
                self._checkin_page(page)

        resp = await self._run_with_retry(attempt, route=route)
        # Strip query string before logging — signed CDN URLs carry
        # bearer-style tokens (Signature=, Expires=) that must not
        # leak via str(exc) or log lines. See docs/SECURITY.md.
        if resp.status >= 400:
            _raise_for_non_retryable(resp, await resp.text(), route=_strip_query(url))
        out_path.write_bytes(await resp.body())
        return out_path

    async def download_image(self, image: GeneratedImage, out_path: Path) -> AnyPath:
        """Download a generated image's signed ``fifeUrl`` to local disk or cloud storage.

        Distinct from :meth:`download`: ``fifeUrl`` is already a fully
        qualified signed CDN URL on ``flow-content.google`` (carrying
        ``Expires=...&Signature=...``), so we MUST NOT route it through
        ``routes.media_download_url`` — that helper builds the
        labs.google redirect path which doesn't apply here.

        When ``GFLOW_CLI_STORAGE_URI`` is set the file is uploaded to the
        configured cloud backend instead of local disk.
        The actual write target is derived from ``out_path`` relative to the
        configured output directory.

        Returns the final write location (local ``Path`` or cloud ``UPath``).

        Raises:
            FlowApiError: when the CDN responds with a 4xx/5xx status.
            ValueError: when ``image.fife_url`` is not an HTTPS URL on a
                trusted Google host (SSRF guard).
        """
        _validate_fife_url(image.fife_url)
        # Strip query string before logging — signed CDN URLs carry
        # bearer-style tokens (Signature=, Expires=) that must not
        # leak via str(exc) or log lines. See docs/SECURITY.md.
        route = _strip_query(image.fife_url)

        async def attempt() -> Any:
            page = await self._checkout_page()
            try:
                return await page.request.get(image.fife_url, max_redirects=2, timeout=120_000)
            finally:
                self._checkin_page(page)

        try:
            resp = await self._run_with_retry(attempt, route=route)
        except NetworkError as exc:
            # The image exists and spent quota: a re-run spends it again (#941).
            exc.retryable = False
            raise
        _validate_fife_final_url(str(resp.url))
        if resp.status >= 400:
            _raise_for_non_retryable(resp, await resp.text(), route=route)
        body = await resp.body()

        # Fail loud if an image request downloaded video content. The agentic
        # gflow_generate_image path has no explicit image-mode toggle — Flow's
        # conversational agent infers image-vs-video from the prompt and can
        # produce a video, whose tile await_images then scrapes as if it were an
        # image. Saving those bytes with an image suffix is a silent corruption
        # that only surfaces far downstream (e.g. Flow 400-rejects the file as an
        # i2v frame -> #125 text-only fallback). Catch it at the download.
        if looks_like_video(body):
            raise WireFormatError(
                detail=(
                    "image download returned video content (ISO-BMFF/WebM magic "
                    "bytes) — the agentic conversational agent likely produced a "
                    "video instead of an image. The agentic arm is only bound "
                    "when asked for by name or by -i instructions, so drop "
                    "--ui-mode agentic / GFLOW_CLI_UI_MODE=agentic / -i "
                    "(auto resolves to classic), or rephrase the prompt."
                ),
                route=route,
            )

        # Resolve the write target: local Path or cloud UPath.
        # Compute a relative key from out_path so cloud keys mirror the local
        # directory structure (images/YYYY-MM-DD/media_id_N.ext).
        storage_uri = self.settings.storage_uri
        if storage_uri:
            key = _storage_key_from_path(out_path, self.settings.output_dir)
            target: AnyPath = storage_path(storage_uri, self.settings.output_dir, key)
        else:
            target = out_path

        # Issue #96: detect actual format from in-memory bytes and correct the
        # suffix before writing — avoids post-write rename (unsupported on cloud).
        target = adjust_key_extension(target, body)
        await write_asset_async(target, body)
        return target

    async def download_video(self, media_id: str, out_path: Path) -> Path:
        """Download a generated video by media ID to disk.

        Wraps :meth:`download` — ``media.getMediaUrlRedirect`` is followed
        transparently; the response body (mp4) is written to ``out_path``.

        Args:
            media_id: The UUID returned in :attr:`VideoStatus.media_id`.
            out_path: Destination file path. Parent directories are created
                if missing.

        Returns:
            ``out_path`` for ergonomic chaining.
        """
        return await self.download(media_id, out_path)

    async def poll_video_status(
        self,
        media_id: str,
        *,
        project_id: str,
        initial_delay_s: float = 90.0,
        poll_interval: float = 10.0,
        timeout_s: float = 900.0,
    ) -> VideoStatus:
        """Poll ``batchCheckAsyncVideoGenerationStatus`` until *media_id* is terminal.

        The **only** outbound video poller in the codebase. Production T2V/I2V
        does not poll: ``ui_automation_video`` passively scans Flow's own captured
        status traffic, which works because the SPA is on-screen polling for its
        own generation. A direct-wire submit (the extend route) gives Flow's UI no
        reason to poll our media id, so that mechanism sees nothing and would sit
        until its deadline. Hence this.

        Shaped after :meth:`_poll_concat_until_done`: every poll is its own
        ``_post_json``, so the Page is checked back in before each sleep. Holding
        a checked-out Page across a sleep self-deadlocks at the default
        ``concurrency=1``.

        ``initial_delay_s`` exists because the cheapest extend model takes ~110s;
        polling immediately spends requests against a WAF-scored host on a job
        that cannot possibly be done. ``poll_interval`` is floored at 5s for the
        same reason — at 2s a 15-segment run would fire ~825 status requests
        instead of ~75.

        Returns the terminal :class:`VideoStatus` on success. Raises
        :class:`ContentPolicyError` when the failure is a safety rejection,
        :class:`FlowApiError` on any other terminal failure, and
        :class:`TransportTimeoutError` on deadline breach. A failed segment has
        still been billed, so it is never returned as a success-shaped object.
        """
        interval = max(poll_interval, _MIN_VIDEO_POLL_INTERVAL_S)
        deadline = time.monotonic() + timeout_s
        if initial_delay_s > 0:
            await asyncio.sleep(initial_delay_s)

        while True:
            resp = await self._post_json(
                routes.CHECK_VIDEO_STATUS,
                {"media": [{"name": media_id, "projectId": project_id}]},
                route_name="batchCheckAsyncVideoGenerationStatus",
            )
            status = parse_video_status(
                cast("JsonObject", resp) if isinstance(resp, dict) else {}, media_id=media_id
            )
            if status.succeeded:
                return status
            if status.is_terminal:
                reasons = ", ".join(status.failure_reasons) or "no reason given"
                detail = f"video generation failed: {reasons}"
                if status.error_message:
                    detail = f"{detail} ({status.error_message})"
                logger.warning(
                    "video.generation_failed",
                    media_id=media_id,
                    failure_reasons=list(status.failure_reasons),
                )
                if any(r in CONTENT_SAFETY_REASONS for r in status.failure_reasons):
                    raise ContentPolicyError(detail, route="batchCheckAsyncVideoGenerationStatus")
                raise FlowApiError(detail, route="batchCheckAsyncVideoGenerationStatus")
            if time.monotonic() >= deadline:
                raise TransportTimeoutError(
                    f"video {media_id} did not finish within {timeout_s:.0f}s "
                    f"(last status: {status.status})",
                    # #1201: submitted, so it may still finish and bill; find it first.
                    next_action="reconcile",
                    operation_id=media_id,
                )
            await asyncio.sleep(interval)

    async def _mint_recaptcha_token(self, action: str) -> str:
        """Mint a single-use reCAPTCHA Enterprise token via the client's Page.

        Flow's `batchGenerateImages` (and `batchAsyncGenerateVideoText`) endpoints
        reject requests with empty / stale tokens with HTTP 403 "reCAPTCHA
        evaluation failed". Tokens are single-use, ~2 min TTL — mint fresh per call.

        Extracted from `_drive_image_generation` so unit tests can monkeypatch
        the mint without standing up a real Playwright Page + reCAPTCHA
        Enterprise script (which requires loading `enterprise.js` from Google).
        Production code path is unchanged.
        """
        page = await self._checkout_page()
        try:
            # #673: this runs BEFORE the UI transport, so none of its migration
            # guards can fire first. On a moved account the pool page is the
            # flow.google.com ROOT GRID, which carries no recaptcha/enterprise.js —
            # bail to exit 36 here instead of a bare RecaptchaError.
            #
            # Be precise about WHY, because the imprecise version misdirects whoever
            # ports this next: flow.google.com is NOT unable to mint. Measured
            # 2026-09-06 (scripts/dev/spike_migrated_recaptcha_mint.py) on the same
            # page pool — `/project/<id>` there carries the enterprise script and
            # site key 6LdsFiUsAAAA…, and a mint returns a 2404-char token. Only the
            # root grid, which is where the client-side handoff leaves the pooled
            # bootstrap page, has no script at all.
            #
            # Image generation no longer reaches this method on the UI transport
            # (#891: it never read the token). The guard stays for the callers that
            # genuinely send one -- the experimental HTTP image transports, upscale
            # and extend -- where a labs page can hand off mid-mint.
            raise_if_migrated(page, at="mint_recaptcha_token")
            # Patchright evaluates in an isolated world by default, where the
            # page's main-world ``grecaptcha`` global is undefined; the resolver
            # supplies ``isolated_context=False`` for patchright ({} for playwright).
            minter = TokenMinter(page, mint_evaluate_kwargs=mint_evaluate_kwargs())
            try:
                return await minter.mint(action)
            # Deliberately broad. Both of `TokenMinter`'s evaluates now raise
            # `RecaptchaError` (#915; the site-key read was unguarded until then and
            # surfaced a RAW Playwright error), but the minter is not the only code in
            # this block, and the re-classification below must see any failure a hop
            # mid-mint can cause. Nothing is swallowed: the original propagates
            # untouched unless the page turns out to be migrated.
            except Exception:
                # #692: the guard above is a point-in-time read, and the handoff
                # to flow.google.com is a CLIENT-SIDE navigation that can land
                # while we are minting. The bootstrap's own `await_url_settled`
                # does not close the window either — it is skipped entirely for a
                # profile latched at NOT_REDIRECTED (`settle = cached !=
                # NOT_REDIRECTED`), which is the population #643 was written for.
                # So a moved account can read as labs at the guard, hop, and then
                # fail here for want of `recaptcha/enterprise.js`.
                #
                # Re-classify on the FAILURE path only: free when the mint
                # succeeds, and correct whenever the hop lands, rather than
                # depending on it landing before a particular line. A genuine
                # labs-side reCAPTCHA failure still propagates as RecaptchaError.
                # One instantaneous read still loses the race. Playwright updates
                # `page.url` on `framenavigated`, so a mint that dies WHILE the
                # navigation is committing reads the old host on an account that
                # is in fact migrated. Poll for a bounded moment instead: this is
                # already the error path, so the wait costs a successful run
                # nothing, and it turns "who won this instant" into "did the hop
                # land at all".
                try:
                    deadline = time.monotonic() + _MIGRATION_RECHECK_BUDGET_S
                    while True:
                        raise_if_migrated(page, at="mint_recaptcha_token_after_failure")
                        probed_url = getattr(page, "url", None)
                        if time.monotonic() >= deadline:
                            break
                        await asyncio.sleep(_MIGRATION_RECHECK_POLL_S)
                except FlowHostMigratedError:
                    raise
                except Exception:
                    # The re-check is a PROBE, and `page.url` can itself raise on a
                    # dead page. `_common.flow_host_kind` is total by construction
                    # for exactly this reason — "a probe error must never displace
                    # the real failure" — but reaching it requires the URL read.
                    # Swallowed so the caller still sees what actually went wrong;
                    # widening the outer handler is what made this reachable.
                    logger.debug("recaptcha_mint_migration_probe_failed", exc_info=True)
                else:
                    # Still reads as labs. That is the discriminating observation
                    # for #692 — the reporter's failure could NOT be reproduced
                    # here (the hop wins the race on this machine and the guard
                    # classifies correctly), so record where the page actually was
                    # rather than spending a round trip asking. The URL is the same
                    # value ``raise_if_migrated`` already logs on the bail path.
                    logger.warning(
                        "recaptcha_mint_failed_off_migrated_host",
                        url=probed_url,
                    )
                raise
        finally:
            self._checkin_page(page)

    async def _drive_images_generation(
        self,
        *,
        project_id: str,
        req: GenerateImageRequest,
        recaptcha_action: str,
        on_checkpoint: GenerationCheckpointObserver | None = None,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> list[GeneratedImage]:
        """Call the transport once and return all images, minting first only for a
        transport that sends the token itself.

        ``req.count`` controls how many images Flow generates (1–4). The UI
        transport clicks the matching x{N} tab so one submission produces N
        images; other transports may fan-out internally, but that is their
        concern. The UI transport never reads ``recaptcha_token`` (#891), so it
        gets no client mint; the HTTP image transports do.

        ``on_checkpoint`` (Task C1) receives a ``submit_attempted`` observation
        immediately before the credit-spending transport call, then a
        ``remote_started`` observation carrying the generated media/workflow
        UUIDs — the first point the image handle is observable.
        """
        if self.transport is None:
            msg = "FlowApiClient.transport is None — call generate_image inside 'async with client'"
            raise RuntimeError(
                msg,
            )
        page_owned_fn = getattr(self.transport, "uses_page_owned_image_recaptcha", None)
        page_owned = callable(page_owned_fn) and bool(page_owned_fn())
        if page_owned:
            # #891: a transport that drives Flow's own page never reads
            # `recaptcha_token` -- the page mints its own on click -- so a client
            # mint here is dead weight at best. At worst it runs on whatever page the
            # pool hands out, which after a successful migrated run is about:blank,
            # and turns the transport's precise exit 36 into a misleading
            # RecaptchaError. Unported forms are refused by the transport's router.
            req_with_token = req
        else:
            token = await self._mint_recaptcha_token(recaptcha_action)
            req_with_token = _dc_replace(req, recaptcha_token=token)
        if on_checkpoint is not None:
            on_checkpoint(GenerationCheckpoint(phase="submit_attempted"))
        # Kwarg passed only when set: keeps duck-typed fakes/transports that
        # predate #546 working, while the Protocol documents the seam.
        resolver_kw: dict[str, Any] = (
            {} if name_resolver is None else {"name_resolver": name_resolver}
        )
        images: list[GeneratedImage] = []
        # #1010: a page-owned call IS the billed click, and an error after it does not
        # mean nothing was made, so retrying it re-submits (one run left 2 images).
        async for retrying in post_with_retry(max_attempts=1 if page_owned else MAX_ATTEMPTS):
            with retrying:
                images = await self.transport.generate_images(
                    project_id=project_id,
                    request=req_with_token,
                    **resolver_kw,
                )
        if not images:
            raise ContentPolicyError(
                detail="empty media[]",
                instance=_make_instance(),
                route=routes.batch_generate_images_url(project_id),
            )
        if on_checkpoint is not None:
            on_checkpoint(
                GenerationCheckpoint(
                    phase="remote_started",
                    media_ids=tuple(img.media_name for img in images),
                    workflow_ids=tuple(img.workflow_id for img in images),
                ),
            )
        return images

    async def _drive_image_generation(
        self,
        *,
        project_id: str,
        req: GenerateImageRequest,
        recaptcha_action: str,
        on_checkpoint: GenerationCheckpointObserver | None = None,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> GeneratedImage:
        """Single-image shortcut — delegates to ``_drive_images_generation`` with count=1.

        Forces ``count=1`` on the request before delegating so that callers
        constructing a :class:`GenerateImageRequest` with ``count>1`` and then
        invoking the single-image API still receive exactly one image (no
        silent discard).

        When the transport returns more images than the requested ``count=1``
        (typically because the generation-settings panel was not found and Flow
        used its own default count, billing the account for the extra
        generations), a ``client.generate_image_extra_returned`` warning is
        logged so the caller can investigate.
        """
        req_one: GenerateImageRequest = _dc_replace(req, count=1)
        images = await self._drive_images_generation(
            project_id=project_id,
            req=req_one,
            recaptcha_action=recaptcha_action,
            on_checkpoint=on_checkpoint,
            name_resolver=name_resolver,
        )
        if len(images) > 1:
            logger.warning(
                "client.generate_image_extra_returned",
                requested=1,
                returned=len(images),
                extra_media_ids=[img.media_name for img in images[1:]],
                hint=(
                    "Flow generated more images than requested — the "
                    "generation-settings panel selector may have missed and Flow "
                    "used its own default count. The extra image(s) were billed "
                    "to your account but not saved. Use `gflow image t2i -n 2` to "
                    "request and save multiple images explicitly."
                ),
            )
        return images[0]

    async def generate_image(
        self,
        *,
        project_id: str | None = None,
        req: GenerateImageRequest,
        recaptcha_action: str = "imageGeneration",
        on_checkpoint: GenerationCheckpointObserver | None = None,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> GeneratedImage:
        """Single-shot Imagen/Narwhal image generation.

        Spec C2: retry+mint live in the per-method closure inside
        :meth:`_drive_image_generation` — fresh token on each attempt.
        Multi-image fan-out is the caller's responsibility
        (see ``generate_images_batch``); this method always returns the FIRST
        media item.

        When ``project_id`` is ``None``, a new Flow project is created
        automatically via :meth:`create_project`.  Existing callers that supply
        an explicit ``project_id`` are unaffected.

        """
        try:
            resolved_project_id: str
            if project_id is None:
                project = await self.create_project()
                resolved_project_id = project.project_id
            else:
                resolved_project_id = project_id
            return await self._drive_image_generation(
                project_id=resolved_project_id,
                req=req,
                recaptcha_action=recaptcha_action,
                on_checkpoint=on_checkpoint,
                name_resolver=name_resolver,
            )
        except Exception as e:
            await self._raise_with_incident(e, phase="image_generation")

    async def generate_images_batch(
        self,
        *,
        project_id: str | None = None,
        req: GenerateImageRequest,
        count: int = 1,
        recaptcha_action: str = "imageGeneration",
        on_checkpoint: GenerationCheckpointObserver | None = None,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> list[GeneratedImage]:
        """Generate ``count`` images using Flow's native count selector (1–4).

        One transport call, one submission round-trip — the UI transport clicks
        the x{count} tab so Flow produces N images in one shot.

        Args:
            project_id: Flow project ID.  When ``None``, a new project is
                created automatically via :meth:`create_project`.
            req: Shared request (prompt, aspect, reference image, ...).
            count: How many images to generate (1–4, Flow's UI cap).

        Raises:
            ValueError: if ``count`` is outside ``[1, 4]``.
        """
        if not 1 <= count <= 4:
            msg = f"count must be between 1 and 4, got {count}"
            raise ValueError(msg)

        try:
            resolved_project_id: str
            if project_id is None:
                project = await self.create_project()
                resolved_project_id = project.project_id
            else:
                resolved_project_id = project_id

            req_with_count: GenerateImageRequest = _dc_replace(req, count=count)
            return await self._drive_images_generation(
                project_id=resolved_project_id,
                req=req_with_count,
                recaptcha_action=recaptcha_action,
                on_checkpoint=on_checkpoint,
                name_resolver=name_resolver,
            )
        except Exception as e:
            await self._raise_with_incident(e, phase="image_batch")

    async def generate_video(
        self,
        *,
        req: GenerateVideoRequest,
        project_id: str | None = None,
        out_dir: Path | None = None,
        poll_timeout_s: float = 600.0,
        download: bool = True,
        on_started: VideoStartedCallback | None = None,
        on_checkpoint: GenerationCheckpointObserver | None = None,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> VideoResult:
        """Generate a video via the transport's ``generate_video`` method.

        Routes all video generation through a single client boundary so the
        data-layer recorder (Task 8) can hook in at one place.

        ``on_checkpoint`` (Task C1) receives a ``submit_attempted`` observation
        immediately before the credit-spending transport call, then a
        ``remote_started`` observation (via the transport's ``on_started`` hook
        — the first point the video handle is observable). ``operation_id`` is
        best-effort/optional: it is captured when the generate response
        includes ``operations[0].operation.name``, and is ``None`` when that
        field is absent (observed on veo-lite too, not only omni-flash, live
        2026-07-21). ``media_id`` is the canonical handle used for polling and
        download.

        Raises:
            RuntimeError: transport is None (client not entered) or the transport
                doesn't implement :class:`VideoCapableTransport`.
            BrowserSessionClosedError: Playwright target was closed mid-call.
        """
        if self.transport is None:
            msg = "FlowApiClient.transport is None - call generate_video inside 'async with client'"
            raise RuntimeError(
                msg,
            )
        if not isinstance(self.transport, VideoCapableTransport):
            msg = f"transport {type(self.transport).__name__} does not support video generation"
            raise RuntimeError(
                msg,
            )

        if project_id is None:
            # #864: as generate_image does. Leaving it to the transport meant the labs
            # gallery's "new project" click, which cannot reach flow.google.com's
            # projects page; create_project reaches both hosts.
            try:
                project_id = (await self.create_project()).project_id
            except Exception as e:
                await self._raise_with_incident(e, phase="video_generation")

        wrapped_on_started = on_started
        if on_checkpoint is not None:
            observer: GenerationCheckpointObserver = on_checkpoint
            observer(GenerationCheckpoint(phase="submit_attempted"))

            def _relay(started: VideoStarted) -> Awaitable[None] | None:
                observer(
                    GenerationCheckpoint(
                        phase="remote_started",
                        operation_id=started.flow_operation_id,
                        media_ids=(started.media_id,),
                    ),
                )
                return on_started(started) if on_started is not None else None

            wrapped_on_started = _relay

        try:
            # Kwarg passed only when set — same #546 compat rule as
            # _drive_images_generation.
            resolver_kw: dict[str, Any] = (
                {} if name_resolver is None else {"name_resolver": name_resolver}
            )
            return await self.transport.generate_video(
                request=req,
                project_id=project_id,
                out_dir=out_dir,
                poll_timeout_s=poll_timeout_s,
                download=download,
                on_started=wrapped_on_started,
                **resolver_kw,
            )

        except Exception as e:
            await self._raise_with_incident(e, phase="video_generation")
