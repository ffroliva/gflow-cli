"""The #546 refresh-on-miss resolver: a stale catalog caption healed from Flow.

Moved out of ``cli_image.py`` unchanged (#1249 ratchet) so the flow.google.com heal could
land without growing that file; ``cli_image`` re-exports the names it always had.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from gflow_cli.api.video import is_media_uuid
from gflow_cli.config import get_settings
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import DataStoreError
from gflow_cli.services import catalog_sync

if TYPE_CHECKING:
    from collections.abc import Callable

    from gflow_cli.api.client import FlowApiClient

logger = structlog.get_logger(__name__)


class _DisplayNameWriter(Protocol):
    """The one-method slice of :class:`DataRepository` the #546 resolver needs."""

    def set_asset_display_name(
        self, profile_name: str, flow_media_id: str, name: str, *, source: str
    ) -> bool: ...


def _build_name_resolver(
    fetch_listing: Callable[[str], dict[str, Any]],
    repo: _DisplayNameWriter,
    *,
    profile_name: str,
    project_id: str | None,
    prompt_mode: str,
) -> Callable[[str], str | None] | None:
    """Build the #546 refresh-on-miss resolver the transport consults.

    Pure and browser-free: ``fetch_listing`` is a SYNC seam over
    ``FlowApiClient.fetch_project_listing`` (the ~0.5s free
    ``flow.projectInitialData`` call) — the caller owns any async bridging
    (see :func:`wire_refresh_resolver` for the live topology).

    * ``project_id is None`` -> ``None``: no listing to consult, the transport
      keeps today's behavior.
    * The resolver fetches THAT project's listing, parses it with
      :func:`catalog_sync.parse_project_listing` (single source of listing
      truth), and returns the current display name for the UUID (``None``
      when unlisted — the transport then walks its existing fallback chain).
    * Write-through provenance: a fresh name is persisted via
      ``set_asset_display_name(..., source="refresh")`` ONLY when
      ``prompt_mode == "store"``; ``redacted`` still self-heals the run
      transiently but never writes a prompt-derived caption to disk.
    """
    if project_id is None:
        return None
    return RefreshResolver(fetch_listing, repo, profile_name, project_id, prompt_mode)


class RefreshResolver:
    """The #546 resolver: media UUID -> the project listing's current name.

    Called, it reads the labs listing (``flow.projectInitialData``). On flow.google.com that
    listing is retired, so the composer reads the caption on its own page instead and hands
    it to :meth:`heal`. Either way a fresh name is written with ``source="refresh"`` only
    when ``prompt_mode == "store"``; ``redacted`` heals the run but never the disk.
    """

    def __init__(
        self,
        fetch_listing: Callable[[str], dict[str, Any]],
        repo: _DisplayNameWriter,
        profile_name: str,
        project_id: str,
        prompt_mode: str,
    ) -> None:
        self._fetch_listing = fetch_listing
        self._repo = repo
        self._profile_name = profile_name
        self._project_id = project_id
        self._prompt_mode = prompt_mode

    def __call__(self, media_id: str) -> str | None:
        listing = catalog_sync.parse_project_listing(self._fetch_listing(self._project_id))
        fresh = listing.names.get(media_id)
        if not fresh:
            return None
        self.heal(media_id, fresh)
        return fresh

    def heal(self, media_id: str, name: str) -> None:
        """Persist a caption found for ``media_id`` (by the listing, or on the page)."""
        if self._prompt_mode == "store":
            self._repo.set_asset_display_name(self._profile_name, media_id, name, source="refresh")


class _RefreshWriteRepo:
    """Write-through repo opening the store per call, in the calling thread.

    The resolver runs on an ``asyncio.to_thread`` worker (see
    :func:`wire_refresh_resolver`), so the sqlite handle must live entirely
    on that thread — open, write, close per invocation (#543 S6 lesson:
    never carry a connection across threads). Misses are rare, so the extra
    open is negligible next to the listing fetch it accompanies.
    """

    def set_asset_display_name(
        self, profile_name: str, flow_media_id: str, name: str, *, source: str
    ) -> bool:
        try:
            with DataStore.open(get_settings().resolved_db_path()) as store:
                return DataRepository(store).set_asset_display_name(
                    profile_name, flow_media_id, name, source=source
                )
        except (DataStoreError, OSError) as exc:
            logger.debug(
                "image.refresh_write_skipped", media_id=flow_media_id, error=str(exc)[:120]
            )
            return False


def wire_refresh_resolver(
    client: FlowApiClient,
    *,
    profile_name: str,
    project_id: str | None,
) -> Callable[[str], str | None] | None:
    """Bridge the async ``FlowApiClient`` into the sync #546 resolver seam.

    Thread topology: the transport invokes the resolver via
    ``asyncio.to_thread`` — OFF the event loop — so the fetch here may safely
    block its worker thread on ``run_coroutine_threadsafe(...).result()``,
    scheduling ``fetch_project_listing`` back onto the main loop captured at
    build time (the loop is free: it is parked awaiting the ``to_thread``
    call). No second browser context, no second ProfileLease — the fetch
    rides the generation's own authenticated session.

    Best-effort and never fatal: any construction failure returns ``None``
    and generation proceeds exactly as before #546. Only a canonical-UUID
    project id qualifies — ``fetch_project_listing`` rejects anything else,
    so a resolver built around one could never succeed.
    """
    # Silent no-resolver path (no log): non-str covers test fakes whose mock
    # project ids would otherwise make is_media_uuid raise.
    if not isinstance(project_id, str) or not is_media_uuid(project_id):
        return None
    try:
        loop = asyncio.get_running_loop()
        settings = get_settings()

        def _fetch(pid: str) -> dict[str, Any]:
            return asyncio.run_coroutine_threadsafe(client.fetch_project_listing(pid), loop).result(
                timeout=60.0
            )

        return _build_name_resolver(
            _fetch,
            _RefreshWriteRepo(),
            profile_name=profile_name,
            project_id=project_id,
            prompt_mode=settings.history_prompts,
        )
    except Exception as exc:  # noqa: BLE001 - optional wiring, never blocks generation
        logger.debug("image.refresh_resolver_unavailable", error=str(exc)[:120])
        return None
