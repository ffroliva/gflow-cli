"""Media edit surface: upscale and extend an existing image or clip.

Split out of ``client.py`` (#1249); ``FlowApiClient`` composes it. Method
bodies are unchanged.
"""

from __future__ import annotations

import base64
import functools
import time
import uuid
from typing import TYPE_CHECKING, cast

import structlog

from gflow_cli.api import routes, video_extend
from gflow_cli.api.image_upscale import (
    TargetResolution,
    UpsampleImageRequest,
    build_upsample_image_body,
)
from gflow_cli.api.transports._common import (
    flow_host_kind,
    migrated_route,
    raise_if_migrated,
)
from gflow_cli.api.video import (
    media_name_from_generate_response,
)
from gflow_cli.api.video_extend import ExtendStarted
from gflow_cli.errors import (
    FlowHostMigratedError,
    UpscaleUnavailableError,
    WafRejectionError,
    WireFormatError,
)
from gflow_cli.paths import adjust_key_extension
from gflow_cli.storage import AnyPath, write_asset_async

if TYPE_CHECKING:
    from pathlib import Path

    from gflow_cli.api.transports.migrated_extend import ExtendOutcome, ExtendStartedCallback
from gflow_cli.api._flow_generation import GenerationSurface, _dc_replace  # pyright: ignore[reportPrivateUsage]
from gflow_cli.api._flow_session import JsonObject, _make_instance  # pyright: ignore[reportPrivateUsage]

logger = structlog.get_logger(__name__)

# upsampleImage returns the upscaled image inline as base64 in `encodedImage`
# (~3.8–5.1 MB observed for 2K). Cap before b64decode to avoid OOM on a
# pathological 4K PNG; 50 MB base64 ≈ 37 MB decoded gives generous headroom.
MAX_UPSAMPLE_B64_LEN = 50 * 1024 * 1024


def _decode_upsample_response(resp: object) -> bytes:
    """The PNG/JPEG bytes in an ``upsampleImage`` reply; the base64 is never logged."""
    resp_obj: JsonObject = cast("JsonObject", resp) if isinstance(resp, dict) else {}
    encoded = str(resp_obj.get("encodedImage") or "")
    if not encoded:
        raise WireFormatError(
            detail="upsampleImage response missing encodedImage",
            instance=_make_instance(),
            route="upsampleImage",
            discovery={"keys": sorted(resp_obj)},
        )
    if len(encoded) > MAX_UPSAMPLE_B64_LEN:
        # Reject before decode — never log the body (mitigation: no MBs in logs).
        raise WireFormatError(
            detail=(
                f"upscaled image exceeds the {MAX_UPSAMPLE_B64_LEN // (1024 * 1024)} MB size cap"
            ),
            route="upsampleImage",
        )
    try:
        image_bytes = base64.b64decode(encoded)
    except ValueError as exc:  # binascii.Error subclasses ValueError
        raise WireFormatError(
            detail="upsampleImage returned undecodable image data",
            route="upsampleImage",
        ) from exc
    del encoded  # drop the multi-MB payload promptly
    if not _is_png_or_jpeg(image_bytes):
        raise WireFormatError(
            detail="upscaled output is not a valid PNG/JPEG",
            route="upsampleImage",
        )
    return image_bytes


def _is_png_or_jpeg(data: bytes) -> bool:
    """True if *data* begins with a PNG or JPEG magic-byte signature.

    Guards against writing a non-image payload (e.g. an error blob that decoded
    as base64) to an image file. PNG: ``89 50 4E 47 0D 0A 1A 0A``; JPEG/JFIF:
    ``FF D8 FF``.
    """
    return data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff"


class MediaEditSurface(GenerationSurface):
    """Media edit surface: upscale and extend an existing image or clip."""

    async def capability_listing(self, project_id: str) -> JsonObject:
        """`projectInitialData` for *project_id*, fetched once per client session.

        The model catalogue and the account's tier cannot change mid-run, so a
        chained extend must not re-fetch per segment: at N=15 that is 15 extra
        requests to a WAF-scored host for a constant.
        """
        cached = self._extend_listing_cache.get(project_id)
        if cached is None:
            cached = await self.fetch_project_listing(project_id)
            self._extend_listing_cache[project_id] = cached
        return cached

    async def extend_video(
        self,
        *,
        media_id: str,
        project_id: str,
        scene_id: str,
        position: int,
        prompt: str,
        aspect: str = "16:9",
        seed: int | None = None,
        recaptcha_action: str = "VIDEO_GENERATION",
    ) -> ExtendStarted:
        """Continue an existing clip by another 8 seconds. Costs credits.

        Direct-wire ``POST /v1/video:batchAsyncGenerateVideoExtendVideo``, verified
        live 2026-08-31. Unlike T2V/I2V — which ride ``ui_automation_video`` and
        passively capture Flow's own request — this composes the body itself, so
        it also owns its polling (see :meth:`poll_video_status`).

        Returns as soon as Flow schedules the job. Poll the returned ``media_id``
        for the result.
        """
        listing = await self.capability_listing(project_id)
        service_tier = video_extend.account_service_tier(listing)
        model_key, unit_cost = video_extend.resolve_extend_model(
            listing, service_tier=service_tier, aspect=aspect
        )
        # When Flow moves the extend family again — it has moved once already —
        # this single line is the diagnosis. The raw creditMapping table is NOT
        # logged; only the decision and the inputs that produced it.
        logger.info(
            "extend_model_resolved",
            model_key=model_key,
            service_tier=service_tier,
            unit_cost=unit_cost,
            candidate_count=len(video_extend.extract_video_models(listing)),
        )
        req = video_extend.ExtendVideoRequest(
            media_id=media_id,
            project_id=project_id,
            scene_id=scene_id,
            position=position,
            prompt=prompt,
            model_key=model_key,
            aspect=aspect,
            seed=seed,
        )
        token = await self._mint_recaptcha_token(recaptcha_action)
        body = req.to_wire(
            session_id=f";{int(time.time() * 1000)}",
            token=token,
            batch_id=str(uuid.uuid4()),
        )
        resp = await self._post_json(
            routes.EXTEND_VIDEO, body, route_name="batchAsyncGenerateVideoExtendVideo"
        )
        data = cast("JsonObject", resp) if isinstance(resp, dict) else {}
        workflows = data.get("workflows")
        workflow_id = ""
        if isinstance(workflows, list) and workflows and isinstance(workflows[0], dict):
            workflow_id = str(cast("JsonObject", workflows[0]).get("name") or "")
        return ExtendStarted(
            media_id=media_name_from_generate_response(data),
            workflow_id=workflow_id,
            model_key=model_key,
            unit_cost=unit_cost,
        )

    def served_route(self, *, prefer_migrated: bool) -> str:
        """``migrated_route`` for the host this client's page was actually served."""
        url = getattr(self._page, "url", None)
        if flow_host_kind(url) is None and getattr(self.transport, "parked_after_migrated", False):
            # Parked on about:blank after a migrated run (image, video or upload): the
            # URL no longer names the host, and reading it as labs mints reCAPTCHA on
            # about:blank (#914).
            url = "https://flow.google.com/"
        return migrated_route(url, self.settings.flow_host, prefer_migrated=prefer_migrated)

    async def upsample_image(
        self,
        *,
        media_id: str,
        project_id: str,
        target_resolution: TargetResolution,
        out_path: Path,
        recaptcha_action: str = "IMAGE_GENERATION",
    ) -> AnyPath:
        """Incident/typed-error boundary for the upscale path (WireFormat/WAF
        failures here are exactly the discovery evidence the recorder exists
        to keep — e.g. the open unexplained HTTP 400 class)."""
        try:
            return await self._upsample_image_impl(
                media_id=media_id,
                project_id=project_id,
                target_resolution=target_resolution,
                out_path=out_path,
                recaptcha_action=recaptcha_action,
            )
        except Exception as e:
            await self._raise_with_incident(e, phase="image_upscale")

    async def _upsample_image_impl(
        self,
        *,
        media_id: str,
        project_id: str,
        target_resolution: TargetResolution,
        out_path: Path,
        recaptcha_action: str = "IMAGE_GENERATION",
    ) -> AnyPath:
        """Upscale a platform-generated image to 2K/4K via Flow's ``upsampleImage``.

        reCAPTCHA-gated (a Bearer-only call is 403-walled — REST is not viable),
        so this mints a fresh single-use token, POSTs, and decodes the synchronous
        ``{"encodedImage": <base64>}`` response (no async poll loop). Writes to
        ``out_path`` (or the configured cloud ``storage_uri``) and returns the
        target.

        ``project_id`` is the project that owns ``media_id`` — the live wire
        requires it inside ``clientContext`` (a minimal body 403s even with a
        valid token; confirmed by live smoke). The request is validated BEFORE the
        reCAPTCHA mint so malformed ids fail fast without spending a token.

        4K is Ultra-tier-gated: a 403 on a 4K request surfaces as
        :class:`UpscaleUnavailableError` (exit 22, never auto-retried) rather than
        :class:`WafRejectionError` — both are HTTP 403 on the wire, disambiguated
        by the requested resolution. A 403 on a 2K request stays a WAF rejection.

        The ~5 MB ``encodedImage`` base64 is NEVER logged; it is capped before
        decode, validated by magic bytes, and dropped promptly after decode.
        """
        # Construct (and validate media_id/project_id) BEFORE minting — fail fast.
        base_req = UpsampleImageRequest(
            media_id=media_id,
            project_id=project_id,
            target_resolution=target_resolution,
        )
        logger.info(
            "image.upscale_started",
            media_id=media_id,
            resolution=target_resolution.name,
        )

        migrated = functools.partial(
            self._drive_migrated_image_upscale,
            project_id=project_id,
            media_id=media_id,
            target_resolution=target_resolution,
            out_path=out_path,
        )
        route = self.served_route(prefer_migrated=False)
        if route == "blocked":
            raise_if_migrated(self._page, at="image_upscale_flow_host_kill_switch")
        if route == "migrated":
            return await migrated()

        try:
            token = await self._mint_recaptcha_token(recaptcha_action)
        except FlowHostMigratedError:
            if self.served_route(prefer_migrated=True) != "migrated":
                raise
            return await migrated()
        req: UpsampleImageRequest = _dc_replace(base_req, recaptcha_token=token)
        session_id = f";{int(time.time() * 1000)}"
        try:
            resp = await self._post_json(
                routes.UPSAMPLE_IMAGE,
                build_upsample_image_body(req, session_id=session_id),
                route_name="upsampleImage",
            )
        except WafRejectionError as exc:
            # A 403 on a 4K request is almost certainly the Ultra-tier gate, not a
            # WAF/fingerprint block. Surface the distinct error (exit 22) so callers
            # can branch on "upgrade your plan" — and crucially, NEVER auto-retry it
            # (a retry only inflates per-profile WAF heat and never succeeds).
            if target_resolution is TargetResolution.RES_4K:
                raise UpscaleUnavailableError(
                    detail=("4K upscale rejected (HTTP 403) — requires a Flow Ultra subscription"),
                    status=403,
                    instance=_make_instance(),
                    route="upsampleImage",
                ) from exc
            raise

        image_bytes = _decode_upsample_response(resp)
        del resp  # drop the multi-MB payload promptly
        logger.info(
            "image.upscale_completed",
            media_id=media_id,
            resolution=target_resolution.name,
            bytes=len(image_bytes),
        )

        target = self._write_target(out_path)
        target = adjust_key_extension(target, image_bytes)
        await write_asset_async(target, image_bytes)
        return target

    async def _drive_migrated_image_upscale(
        self,
        *,
        project_id: str,
        media_id: str,
        target_resolution: TargetResolution,
        out_path: Path,
    ) -> AnyPath:
        from gflow_cli.api.transports.migrated_upscale import upscale_image_migrated

        page = await self._checkout_page()
        try:
            image_bytes = await upscale_image_migrated(
                page,
                project_id=project_id,
                media_id=media_id,
                target_resolution=target_resolution,
            )
            target = self._write_target(out_path)
            target = adjust_key_extension(target, image_bytes)
            await write_asset_async(target, image_bytes)
            logger.info(
                "image.upscale_completed",
                media_id=media_id,
                resolution=target_resolution.name,
                bytes=len(image_bytes),
            )
            return target
        finally:
            self._checkin_page(page)

    async def upsample_video(
        self,
        *,
        media_id: str,
        project_id: str,
        scale: str = "1080p",
        out_path: Path,
    ) -> AnyPath:
        """Upscale or export a platform-generated video to 1080p, 720p, or 270p GIF.

        Drives the migrated Flow editor to export an upsampled 1080p Full HD video or
        animated GIF. There is no labs.google route for this, so
        ``GFLOW_CLI_FLOW_HOST=labs.google`` (the kill switch) refuses it with exit 36.
        """
        from gflow_cli.api.transports.migrated_video_upscale import upscale_video_migrated

        if self.served_route(prefer_migrated=True) != "migrated":
            raise FlowHostMigratedError(
                detail=(
                    "video upscale is driven only on flow.google.com (there is no "
                    "labs.google route for it), and GFLOW_CLI_FLOW_HOST=labs.google "
                    "switched that host off"
                ),
                remediation_hint=(
                    "Unset GFLOW_CLI_FLOW_HOST (or set it to auto / flow.google.com) "
                    "to export the video through flow.google.com."
                ),
            )

        logger.info(
            "video.upscale_started",
            media_id=media_id,
            scale=scale,
        )
        try:
            page = await self._checkout_page()
            try:
                video_bytes = await upscale_video_migrated(
                    page,
                    project_id=project_id,
                    media_id=media_id,
                    scale=scale,
                )
                ext = ".gif" if scale.strip().lower() == "270p" else ".mp4"
                if out_path.suffix.lower() != ext:
                    out_path = out_path.with_suffix(ext)

                target = self._write_target(out_path)
                await write_asset_async(target, video_bytes)
                logger.info(
                    "video.upscale_completed",
                    media_id=media_id,
                    scale=scale,
                    bytes=len(video_bytes),
                )
                return target
            finally:
                self._checkin_page(page)
        except Exception as exc:
            await self._raise_with_incident(exc, phase="video_upscale")

    async def extend_video_migrated(  # noqa: PLR0913
        self,
        *,
        media_id: str,
        project_id: str,
        prompt: str,
        aspect: str | None,
        out_path: Path,
        on_started: ExtendStartedCallback | None = None,
        poll_timeout_s: float = 900.0,
    ) -> tuple[ExtendOutcome, AnyPath | None]:
        """One extend segment through Flow's own UI on flow.google.com. Costs credits.

        The page mints and sends ``fZytfe`` itself; see
        :mod:`gflow_cli.api.transports.migrated_extend`. Returns the outcome and the
        written mp4 (``None`` when the generation did not finish).
        """
        from gflow_cli.api.transports.migrated_extend import run_extend

        try:
            page = await self._checkout_page()
            try:
                outcome = await run_extend(
                    page,
                    project_id=project_id,
                    media_id=media_id,
                    prompt=prompt,
                    aspect=aspect,
                    poll_timeout_s=poll_timeout_s,
                    on_started=on_started,
                )
            finally:
                self._checkin_page(page)
        except Exception as exc:
            await self._raise_with_incident(exc, phase="video_extend")
        if outcome.video is None:
            return outcome, None
        target = self._write_target(out_path)
        await write_asset_async(target, outcome.video)
        return outcome, target
