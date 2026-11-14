"""Media fetch and download.

A part of `MigratedComposer`, moved byte-identical from
`migrated_composer.py` (#1249 wave 2).
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import structlog
from playwright.async_api import Error as PlaywrightError

from gflow_cli.api.transports._common import (
    expired_link_hint,
    get_signed_media,
    recoverable_clip_hint,
)
from gflow_cli.api.transports.batchexecute import GenerationRecord
from gflow_cli.errors import GFlowError, MediaDownloadError, WireFormatError

if TYPE_CHECKING:
    from playwright.async_api import Page

    from gflow_cli.api.transports.migrated_composer import ClipSaver, MigratedComposer

log = structlog.get_logger(__name__)


class MediaMixin:
    """Media fetch and download."""

    @staticmethod
    async def fetch_mp4(page: Page, record: GenerationRecord) -> bytes:
        """GET the signed URL and prove it is an MP4 (``ftyp`` at offset 4); if it is
        not — the record carries a poster JPEG next to the clip, and a 2026-09-05
        run downloaded that one — try the other URL before giving up."""
        from gflow_cli.api.transports.ui_automation import (  # noqa: PLC0415 - cycle
            _is_allowed_download_host,  # pyright: ignore[reportPrivateUsage]
        )

        seen: list[str] = []
        for url in (record.video_url, record.poster_url):
            if not url:
                continue
            if not _is_allowed_download_host(url):
                raise WireFormatError(
                    detail=(
                        "migrated host: refusing to download from "
                        f"{urlsplit(url).hostname!r} (not an allowed Google host)"
                    ),
                    route="batchexecute:as29s",
                )
            # No redirects: an open redirect on the CDN must not rebound the
            # request elsewhere (same posture as the labs image download).
            resp = await get_signed_media(
                page,
                url,
                media_id=record.media_id,
                route="flow-content.google",
                max_redirects=0,
                remediation=recoverable_clip_hint(record.media_id),
                retryable=False,  # billed: a re-run bills again (#941)
            )
            if resp.status >= 300:
                # #941: the clip is done and billed; this is a failed DOWNLOAD, recoverable.
                raise MediaDownloadError(
                    media_id=record.media_id,
                    detail=f"migrated host: signed media URL returned HTTP {resp.status}",
                    status=resp.status,
                    route="flow-content.google",
                    remediation_hint=expired_link_hint(record.media_id),
                    retryable=False,
                )
            body = await resp.body()
            if body[4:8] == b"ftyp":
                if record.size_bytes and len(body) != record.size_bytes:
                    log.warning(
                        "migrated.download_size_mismatch",
                        expected=record.size_bytes,
                        actual=len(body),
                    )
                return body
            seen.append(
                f"{urlsplit(url).path.rsplit('/', 1)[-1]}: {body[:4].hex()} ({len(body)} B)"
            )
        raise WireFormatError(
            detail=(
                "migrated host: no signed URL on the record returned an MP4 "
                f"(ftyp magic); saw {'; '.join(seen) or 'no URLs'}"
            ),
            route="batchexecute:as29s",
        )

    @staticmethod
    async def clip_bytes(page: Page, record: GenerationRecord) -> bytes:
        """The bytes of a finished, billed clip, from whichever source it has.

        A failed route read ends in the free recovery: this clip is billed, so advice to
        re-run would bill it again (#941). The clip's route replaces the page, so only a
        caller that has finished with the page may reach this."""
        if record.video_url:
            return await MigratedComposer.fetch_mp4(page, record)
        from gflow_cli.api.transports import migrated_recover  # noqa: PLC0415 - cycle

        try:
            _, body = await migrated_recover.read_clip(
                page, project_id=record.project_id, media_id=record.media_id
            )
        except (GFlowError, PlaywrightError, TimeoutError) as exc:
            raise MediaDownloadError(
                media_id=record.media_id,
                detail=(
                    "migrated host: the clip finished but could not be read from its own "
                    f"route ({type(exc).__name__})"
                ),
                route="flow-content.google",
                remediation_hint=recoverable_clip_hint(record.media_id),
                retryable=False,
            ) from exc
        return body

    async def download(
        self,
        page: Page,
        record: GenerationRecord,
        save: ClipSaver,
    ) -> Path | None:
        """The clip from its signed URL, stored by ``save``.

        With no clip URL on the record, its own route supplies one (#1153): since
        2026-11-05 no status reply in the run was seen to name the clip. The labs
        ``media.getMediaUrlRedirect`` route answers 404 for a migrated media id."""
        body = await self.clip_bytes(page, record)
        path = await save(record.media_id, body)
        log.info("migrated.download", path=str(path), bytes=len(body))
        return path
