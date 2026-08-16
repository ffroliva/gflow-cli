"""Catalog name/presence sync from Flow project listings (#543).

Source endpoint: ``GET .../trpc/flow.projectInitialData?input=…`` with
``toolName="PINHOLE"`` — the Flow editor's own initial-data call, live-verified
2026-08-16 (~0.5s, session-cookie auth, no page navigation, credit-free).
:meth:`FlowApiClient.fetch_project_listing` returns its tRPC envelope
verbatim; this module turns it into catalog writes.

Identity model: the media UUID is the identity; display names are SEARCH KEYS
only (captions the Flow UI shows). Names come exclusively from
``workflows[].metadata`` (``primaryMediaId`` -> ``displayName``); presence
comes exclusively from ``media[].name``. ``externalReferenceMedia`` (preset
voices etc.) contributes neither — keying a ghost-mark sweep on it would
falsely retain or resurrect rows.

Safety rails (PLAN risk register):

- Harvested ids are remote bytes later interpolated into selectors — anything
  failing strict UUID validation is dropped and counted, never stored.
- Absence proves nothing on a partial listing: any pagination marker anywhere
  in the payload flips ``complete`` to False, and an incomplete listing must
  never ghost-mark.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.api.video import is_media_uuid

if TYPE_CHECKING:
    from collections.abc import Callable

log = structlog.get_logger(__name__)

# Keys whose presence (with a non-empty / non-null value) anywhere in the
# payload signals the listing is paginated — i.e. NOT the complete project.
PAGINATION_MARKER_KEYS = frozenset(
    {
        "nextPageToken",
        "pageToken",
        "cursor",
        "totalCount",
        "hasMore",
        "pageInfo",
        "continuationToken",
    }
)

# ponytail: bounded recursion — real payloads nest ~6 deep; 16 is headroom,
# and anything deeper is not a shape we should trust anyway.
_MARKER_WALK_MAX_DEPTH = 16


@dataclass(frozen=True)
class ListingParse:
    """Parsed view of one project listing.

    ``names`` maps media UUID -> displayName; ``present`` is the set of media
    UUIDs the listing contains; ``complete`` is False when any pagination
    marker was seen; ``dropped`` counts harvested ids rejected by strict UUID
    validation.
    """

    names: dict[str, str]
    present: frozenset[str]
    complete: bool
    dropped: int


@dataclass(frozen=True)
class SyncSummary:
    """Outcome of one ``gflow data sync --names`` run."""

    projects_visited: int
    names_written: int
    ghosts_marked: int
    rows_still_nameless: int
    # (project_id, error) records for per-project fetch failures — exact
    # element shape is pinned by S3's orchestration implementation.
    failures: tuple[Any, ...]


def _has_pagination_marker(node: Any, depth: int = 0) -> bool:
    """True if any pagination-marker key holds a truthy value, recursively."""
    if depth > _MARKER_WALK_MAX_DEPTH:
        return False
    if isinstance(node, dict):
        mapping = cast("dict[str, Any]", node)
        return any(
            (key in PAGINATION_MARKER_KEYS and bool(value))
            or _has_pagination_marker(value, depth + 1)
            for key, value in mapping.items()
        )
    if isinstance(node, list):
        items = cast("list[Any]", node)
        return any(_has_pagination_marker(item, depth + 1) for item in items)
    return False


def parse_project_listing(payload: dict[str, Any]) -> ListingParse:
    """Extract names + presence from a ``flow.projectInitialData`` envelope.

    Raises ``ValueError`` when ``result.data.json.projectContents`` is absent
    — an unrecognized envelope must fail loudly, not sync an empty project.
    """
    try:
        raw_contents: Any = payload["result"]["data"]["json"]["projectContents"]
    except (KeyError, TypeError) as exc:
        msg = "payload has no result.data.json.projectContents"
        raise ValueError(msg) from exc
    if not isinstance(raw_contents, dict):
        msg = "projectContents is not an object"
        raise ValueError(msg)
    contents = cast("dict[str, Any]", raw_contents)

    dropped = 0

    present: set[str] = set()
    for item in cast("list[Any]", contents.get("media") or []):
        entry = cast("dict[str, Any]", item) if isinstance(item, dict) else None
        name: Any = entry.get("name") if entry is not None else None
        if isinstance(name, str) and is_media_uuid(name):
            present.add(name)
        elif name is not None:
            dropped += 1

    names: dict[str, str] = {}
    for wf in cast("list[Any]", contents.get("workflows") or []):
        wf_entry = cast("dict[str, Any]", wf) if isinstance(wf, dict) else None
        raw_meta: Any = wf_entry.get("metadata") if wf_entry is not None else None
        if not isinstance(raw_meta, dict):
            continue
        meta = cast("dict[str, Any]", raw_meta)
        media_id: Any = meta.get("primaryMediaId")
        display_name: Any = meta.get("displayName")
        if media_id is None or not isinstance(display_name, str):
            continue  # degenerate workflow (no join key / no caption): skip, uncounted
        if isinstance(media_id, str) and is_media_uuid(media_id):
            names[media_id] = display_name
        else:
            dropped += 1

    return ListingParse(
        names=names,
        present=frozenset(present),
        complete=not _has_pagination_marker(payload),
        dropped=dropped,
    )


def run_sync(
    client: Any,
    repo: Any,
    settings: Any,
    *,
    profile_name: str,
    dry_run: bool = False,
    max_projects: int | None = None,
    on_progress: Callable[[Any], None] | None = None,
) -> SyncSummary:
    """Sweep nameless catalog rows' projects and write names / ghost marks.

    Seams: ``client.fetch_project_listing(project_id)``;
    ``repo.list_nameless_asset_projects`` / ``set_asset_display_name`` /
    ``mark_asset_missing_remote``. Contract: tests/services/test_catalog_sync.py.
    """
    raise NotImplementedError("S3")
