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

import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.api.video import is_media_uuid
from gflow_cli.config import parse_jitter_range
from gflow_cli.errors import ConfigurationError, SyncPartialError, WafRejectionError

if TYPE_CHECKING:
    from collections.abc import Callable

log = structlog.get_logger(__name__)

# Seam for tests to patch — jitter sleeps between project fetches route here.
_sleep = time.sleep

# Minimal-jitter default (seconds) between consecutive project fetches when
# settings carry no jitter_range — enough to break the burst signature without
# wasting wall-clock; widen via GFLOW_CLI_JITTER_RANGE on observed WAF 403s.
_DEFAULT_JITTER_RANGE = (0.5, 1.5)

# Keys whose presence (with a truthy value — `totalCount: 0` / `hasMore: false`
# deliberately do not flag) anywhere in the payload signals the listing is
# paginated — i.e. NOT the complete project.
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
    #: (project_id, error) records for per-project fetch/parse failures.
    failures: tuple[tuple[str, Exception], ...]


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

    Raises:
        ConfigurationError: ``history_prompts == "redacted"`` — refused before
            any repo/client call (sync stores remote display names).
        WafRejectionError: re-raised RAW mid-sweep; the whole run aborts
            (continuing escalates the WAF score), earlier writes stay committed.
        SyncPartialError: some projects failed, at least one succeeded —
            ``.summary`` carries the SyncSummary including failure records.
        Exception: all visited projects failed — the FIRST failure re-raised.
            Repo write errors also propagate raw: local failures are systemic,
            earlier writes stay committed, and an idempotent re-run resumes.
    """
    if getattr(settings, "history_prompts", None) == "redacted":
        msg = (
            "history_prompts is 'redacted' — sync stores remote display names, "
            "which are prompt-derived captions. Set GFLOW_CLI_HISTORY_PROMPTS=store "
            "to enable `gflow data sync --names`."
        )
        raise ConfigurationError(msg)

    work = list(repo.list_nameless_asset_projects(profile_name))
    if max_projects is not None:
        work = work[:max_projects]
    jitter_spec: Any = getattr(settings, "jitter_range", None)
    jitter_low, jitter_high = (
        parse_jitter_range(jitter_spec)
        if isinstance(jitter_spec, str) and jitter_spec.strip()
        else _DEFAULT_JITTER_RANGE
    )

    names_written = 0
    ghosts_marked = 0
    rows_still_nameless = 0
    failures: list[tuple[str, Exception]] = []

    for index, row in enumerate(work):
        project_id = str(row.flow_project_id)
        if index > 0 and jitter_high > 0:
            _sleep(random.uniform(jitter_low, jitter_high))  # noqa: S311  # cadence, not crypto
        if on_progress is not None:
            on_progress(f"[{index + 1}/{len(work)}] project {project_id}")
        log.info("sync.project_started", project_id=project_id, index=index, total=len(work))
        try:
            parsed = parse_project_listing(client.fetch_project_listing(project_id))
        except WafRejectionError:
            # WAF 403 mid-sweep: continuing escalates the WAF score — abort the
            # whole run, propagate raw. Earlier projects' writes stay committed.
            raise
        except Exception as exc:  # noqa: BLE001 — per-project isolation is the contract
            failures.append((project_id, exc))
            log.info("sync.project_failed", project_id=project_id, error=type(exc).__name__)
            continue

        project_names = 0
        project_ghosts = 0
        project_nameless = 0
        for media_id in row.media_ids:
            named = media_id in parsed.names
            if named and (
                True
                if dry_run
                else repo.set_asset_display_name(
                    profile_name, media_id, parsed.names[media_id], source="sync"
                )
            ):
                project_names += 1
            # Absence proves nothing on a partial listing — ghost-mark ONLY
            # when the listing is complete.
            ghost = parsed.complete and media_id not in parsed.present
            if ghost and (
                True if dry_run else repo.mark_asset_missing_remote(profile_name, media_id)
            ):
                project_ghosts += 1
            if not named and not ghost:
                project_nameless += 1
        names_written += project_names
        ghosts_marked += project_ghosts
        rows_still_nameless += project_nameless
        log.info(
            "sync.project_done",
            project_id=project_id,
            names=project_names,
            ghosts=project_ghosts,
            still_nameless=project_nameless,
            dropped=parsed.dropped,
            complete=parsed.complete,
        )

    summary = SyncSummary(
        projects_visited=len(work),
        names_written=names_written,
        ghosts_marked=ghosts_marked,
        rows_still_nameless=rows_still_nameless,
        failures=tuple(failures),
    )
    log.info(
        "sync.summary",
        projects_visited=summary.projects_visited,
        names_written=summary.names_written,
        ghosts_marked=summary.ghosts_marked,
        rows_still_nameless=summary.rows_still_nameless,
        failures=len(summary.failures),
        dry_run=dry_run,
    )
    if failures:
        if len(failures) == len(work):
            raise failures[0][1]  # every project failed — surface the first typed error raw
        msg = (
            f"{len(failures)} of {len(work)} projects failed; "
            f"{names_written} names written, {ghosts_marked} ghosts marked"
        )
        raise SyncPartialError(msg, summary=summary)
    return summary
