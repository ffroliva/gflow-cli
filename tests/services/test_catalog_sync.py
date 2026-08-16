"""Red spec for `gflow data sync --names` orchestration + parser (#543, Task S1).

These tests ARE the contract for ``gflow_cli.services.catalog_sync``
(Tasks S2/S3):

- ``parse_project_listing(payload) -> ListingParse`` — frozen dataclass
  ``(names: dict[uuid -> displayName], present: frozenset[uuid],
  complete: bool, dropped: int)``. Names come ONLY from
  ``workflows[].metadata.primaryMediaId -> displayName``; presence ONLY from
  ``media[].name``; ``externalReferenceMedia`` contributes NEITHER. Any
  pagination-marker key (nextPageToken, pageToken, cursor, totalCount,
  hasMore, pageInfo, continuationToken) with a non-empty/non-null value —
  anywhere in the payload — flips ``complete`` to False. Harvested ids that
  fail strict UUID validation are dropped and counted in ``dropped``.
  A payload without ``projectContents`` raises ``ValueError``.
- ``run_sync(client, repo, settings, *, profile_name, dry_run, max_projects,
  on_progress) -> SyncSummary`` (frozen: projects_visited, names_written,
  ghosts_marked, rows_still_nameless, failures) — client seam is
  ``client.fetch_project_listing(project_id)``; repo seam is
  ``list_nameless_asset_projects`` / ``set_asset_display_name`` /
  ``mark_asset_missing_remote`` (duck-typed here).

Red reason (by design, STRICT TDD): ``gflow_cli.services.catalog_sync`` does
not exist yet — this whole file fails collection with ModuleNotFoundError.
``SyncPartialError`` (also new) is imported inside the tests that need it so
this file's collection-time red reason stays the missing module.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest

from gflow_cli.errors import (
    ConfigurationError,
    TransportTimeoutError,
    WafRejectionError,
)
from gflow_cli.services.catalog_sync import (
    ListingParse,
    SyncSummary,
    parse_project_listing,
    run_sync,
)
from tests.fixtures.listing_payload import (
    listing_payload,
    media_item,
    named_pair,
    new_id,
    workflow_item,
)

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parse_happy_join_names_presence_complete() -> None:
    id_a, media_a, wf_a = named_pair("Sunset over water")
    id_b, media_b, wf_b = named_pair("A cozy cabin")
    parsed = parse_project_listing(
        listing_payload(media=[media_a, media_b], workflows=[wf_a, wf_b])
    )
    assert parsed.names == {id_a: "Sunset over water", id_b: "A cozy cabin"}
    assert parsed.present == frozenset({id_a, id_b})
    assert parsed.complete is True
    assert parsed.dropped == 0
    # Workflow uuids are join keys, never members of the presence set.
    assert wf_a["name"] not in parsed.present


def test_listing_parse_is_frozen() -> None:
    parsed = parse_project_listing(listing_payload())
    assert isinstance(parsed, ListingParse)
    with pytest.raises(dataclasses.FrozenInstanceError):
        parsed.complete = False  # type: ignore[misc]


def test_parse_workflow_without_display_name_or_primary_media_skipped() -> None:
    media_id = new_id()
    payload = listing_payload(
        media=[media_item(media_id)],
        workflows=[
            workflow_item(primary_media_id=media_id),  # no displayName
            workflow_item(display_name="Orphan caption"),  # no primaryMediaId
        ],
    )
    parsed = parse_project_listing(payload)
    assert parsed.names == {}
    # Presence is untouched: the media row itself is still there.
    assert parsed.present == frozenset({media_id})
    assert parsed.complete is True


def test_parse_blank_display_name_contributes_nothing() -> None:
    """A caption that is blank after .strip() must vanish entirely: writing ""
    as a name would leave the row matching the nameless query forever (eternal
    resweep) while a ``dropped`` count would misreport a validation failure —
    so it lands in neither ``names`` nor ``dropped``."""
    media_id = new_id()
    payload = listing_payload(
        media=[media_item(media_id)],
        workflows=[workflow_item(primary_media_id=media_id, display_name="  ")],
    )
    parsed = parse_project_listing(payload)
    assert parsed.names == {}
    assert parsed.dropped == 0
    assert parsed.present == frozenset({media_id})


def test_parse_malformed_uuid_dropped_and_counted() -> None:
    """Harvested ids are remote bytes later interpolated into selectors —
    strict UUID validation drops anything non-conforming (risk register S2)."""
    good_id, good_media, good_wf = named_pair("Kept")
    evil = "bad']uuid"
    payload = listing_payload(
        media=[good_media, media_item(evil)],
        workflows=[
            good_wf,
            workflow_item(primary_media_id=evil, display_name="Evil caption"),
        ],
    )
    parsed = parse_project_listing(payload)
    assert parsed.names == {good_id: "Kept"}
    assert parsed.present == frozenset({good_id})
    assert evil not in parsed.names
    assert parsed.dropped == 2  # one media name + one primaryMediaId


@pytest.mark.parametrize(
    "marker",
    [
        {"nextPageToken": "tok-1"},
        {"pageToken": "tok-2"},
        {"cursor": "abc"},
        {"totalCount": 120},
        {"hasMore": True},
        {"pageInfo": {"hasNextPage": True}},
        {"continuationToken": "tok-3"},
    ],
    ids=lambda m: next(iter(m)),
)
def test_parse_pagination_marker_sets_incomplete(marker: dict[str, Any]) -> None:
    media_id, media, wf = named_pair()
    payload = listing_payload(media=[media], workflows=[wf], project_contents_extra=marker)
    assert parse_project_listing(payload).complete is False


def test_parse_pagination_marker_nested_deep_sets_incomplete() -> None:
    """Markers count ANYWHERE in the payload, not just at known levels."""
    media_id, media, wf = named_pair()
    payload = listing_payload(
        media=[media],
        workflows=[wf],
        json_extra={"paging": {"inner": {"nextPageToken": "deep-tok"}}},
    )
    assert parse_project_listing(payload).complete is False


def test_parse_empty_or_null_marker_values_do_not_flag_incomplete() -> None:
    media_id, media, wf = named_pair()
    payload = listing_payload(
        media=[media],
        workflows=[wf],
        project_contents_extra={"nextPageToken": "", "cursor": None},
    )
    assert parse_project_listing(payload).complete is True


def test_parse_external_reference_media_contributes_nothing() -> None:
    """Preset voices etc. live in externalReferenceMedia — they must add
    neither names nor presence (a ghost-mark sweep keyed on them would
    resurrect or falsely retain rows)."""
    preset_id = new_id()
    payload = listing_payload(
        external_reference_media=[media_item(preset_id)],
        workflows=[workflow_item(primary_media_id=preset_id, display_name="Preset voice")],
    )
    parsed = parse_project_listing(payload)
    assert preset_id not in parsed.present
    # primaryMediaId join still only names what it names — but presence stays
    # media[]-only; an empty media[] here means empty presence.
    assert parsed.present == frozenset()


def test_parse_missing_project_contents_raises_value_error() -> None:
    with pytest.raises(ValueError):
        parse_project_listing({"result": {"data": {"json": {}}}})


# ---------------------------------------------------------------------------
# Orchestration — fakes for the client + repository seams
# ---------------------------------------------------------------------------


class FakeClient:
    """``fetch_project_listing`` seam: dict of project_id -> payload | error."""

    def __init__(self, listings: dict[str, Any]) -> None:
        self._listings = listings
        self.fetched: list[str] = []

    def fetch_project_listing(self, project_id: str) -> dict[str, Any]:
        self.fetched.append(project_id)
        result = self._listings[project_id]
        if isinstance(result, Exception):
            raise result
        return result


class FakeRepo:
    def __init__(self, work: list[SimpleNamespace]) -> None:
        self._work = work
        self.names: list[tuple[str, str]] = []  # (flow_media_id, name)
        self.ghosts: list[str] = []
        self.calls: list[tuple[str, str]] = []  # ("name" | "ghost", flow_media_id)

    def list_nameless_asset_projects(
        self,
        profile_name: str,
        *,
        limit: int | None = None,
        since: Any = None,
        project_ids: Any = None,
    ) -> list[SimpleNamespace]:
        return self._work

    def set_asset_display_name(
        self, profile_name: str, flow_media_id: str, name: str, *, source: str
    ) -> bool:
        self.names.append((flow_media_id, name))
        self.calls.append(("name", flow_media_id))
        return True

    def mark_asset_missing_remote(self, profile_name: str, flow_media_id: str) -> bool:
        self.ghosts.append(flow_media_id)
        self.calls.append(("ghost", flow_media_id))
        return True


def _work(project_id: str, *media_ids: str) -> SimpleNamespace:
    return SimpleNamespace(flow_project_id=project_id, media_ids=tuple(media_ids))


def _settings(history_prompts: str = "store") -> SimpleNamespace:
    return SimpleNamespace(history_prompts=history_prompts)


def _run(client: FakeClient, repo: FakeRepo, **overrides: Any) -> SyncSummary:
    kwargs: dict[str, Any] = {
        "profile_name": "default",
        "dry_run": False,
        "max_projects": 50,
        "on_progress": None,
    }
    kwargs.update(overrides)
    settings = kwargs.pop("settings", _settings())
    return run_sync(client, repo, settings, **kwargs)


def test_run_sync_writes_names_and_summarizes() -> None:
    id_a, media_a, wf_a = named_pair("Name A")
    id_b, media_b, wf_b = named_pair("Name B")
    p1, p2 = new_id(), new_id()
    client = FakeClient(
        {
            p1: listing_payload(media=[media_a], workflows=[wf_a]),
            p2: listing_payload(media=[media_b], workflows=[wf_b]),
        }
    )
    repo = FakeRepo([_work(p1, id_a), _work(p2, id_b)])

    summary = _run(client, repo)

    assert client.fetched == [p1, p2]
    assert repo.names == [(id_a, "Name A"), (id_b, "Name B")]
    assert summary.projects_visited == 2
    assert summary.names_written == 2
    assert summary.ghosts_marked == 0
    assert summary.rows_still_nameless == 0
    assert summary.failures == ()
    with pytest.raises(dataclasses.FrozenInstanceError):
        summary.names_written = 99  # type: ignore[misc]


def test_run_sync_waf_aborts_whole_run_keeps_partial_writes() -> None:
    """WAF 403 mid-sweep: continuing escalates the WAF score (risk register).
    The raw WafRejectionError propagates, later projects are NEVER fetched,
    and writes already made stay committed."""
    id_a, media_a, wf_a = named_pair("Kept write")
    p1, p2, p3 = new_id(), new_id(), new_id()
    client = FakeClient(
        {
            p1: listing_payload(media=[media_a], workflows=[wf_a]),
            p2: WafRejectionError("HTTP 403 mid-sweep"),
            p3: listing_payload(),
        }
    )
    repo = FakeRepo([_work(p1, id_a), _work(p2, new_id()), _work(p3, new_id())])

    with pytest.raises(WafRejectionError):
        _run(client, repo)

    assert client.fetched == [p1, p2]  # p3 never fetched
    assert repo.names == [(id_a, "Kept write")]  # partial writes kept


def test_run_sync_ghost_marked_only_on_complete_listing() -> None:
    id_present, media, wf = named_pair("Still here")
    id_ghost = new_id()
    project = new_id()
    client = FakeClient({project: listing_payload(media=[media], workflows=[wf])})
    repo = FakeRepo([_work(project, id_present, id_ghost)])

    summary = _run(client, repo)

    assert repo.ghosts == [id_ghost]
    assert summary.ghosts_marked == 1


def test_run_sync_named_but_absent_gets_name_and_ghost_mark() -> None:
    """Named-but-absent edge on a COMPLETE listing: a uuid with a workflow
    caption but no media[] row is BOTH name-cached and ghost-marked (name
    first, then ghost), and the summary counts both."""
    media_id = new_id()
    wf = workflow_item(primary_media_id=media_id, display_name="Orphan caption")
    project = new_id()
    client = FakeClient({project: listing_payload(media=[], workflows=[wf])})
    repo = FakeRepo([_work(project, media_id)])

    summary = _run(client, repo)

    assert repo.calls == [("name", media_id), ("ghost", media_id)]
    assert summary.names_written == 1
    assert summary.ghosts_marked == 1
    assert summary.rows_still_nameless == 0


def test_run_sync_incomplete_listing_never_ghost_marks() -> None:
    """False-permanent missing_remote from a partial listing is the top risk:
    a pagination marker means absence proves nothing."""
    id_present, media, wf = named_pair()
    id_absent = new_id()
    project = new_id()
    client = FakeClient(
        {
            project: listing_payload(
                media=[media],
                workflows=[wf],
                project_contents_extra={"nextPageToken": "more"},
            )
        }
    )
    repo = FakeRepo([_work(project, id_present, id_absent)])

    summary = _run(client, repo)

    assert repo.ghosts == []
    assert summary.ghosts_marked == 0


def test_run_sync_fetch_failure_no_ghosts_continues_then_partial_error() -> None:
    """A non-WAF per-project failure: no ghost marks for that project, the
    failure is recorded, the remaining projects still run, and the run ends
    by raising SyncPartialError with the summary attached."""
    from gflow_cli.errors import SyncPartialError  # new in #543 — red until S5

    id_b, media_b, wf_b = named_pair("Survivor")
    p1, p2 = new_id(), new_id()
    client = FakeClient(
        {
            p1: TransportTimeoutError("hung > 30s"),
            p2: listing_payload(media=[media_b], workflows=[wf_b]),
        }
    )
    repo = FakeRepo([_work(p1, new_id()), _work(p2, id_b)])

    with pytest.raises(SyncPartialError) as excinfo:
        _run(client, repo)

    assert client.fetched == [p1, p2]
    assert repo.ghosts == []  # never ghost-mark on fetch failure
    assert repo.names == [(id_b, "Survivor")]
    summary = excinfo.value.summary
    assert isinstance(summary, SyncSummary)
    assert len(summary.failures) == 1


def test_run_sync_all_projects_failed_reraises_first_typed_error() -> None:
    p1, p2 = new_id(), new_id()
    client = FakeClient(
        {
            p1: TransportTimeoutError("first failure"),
            p2: TransportTimeoutError("second failure"),
        }
    )
    repo = FakeRepo([_work(p1, new_id()), _work(p2, new_id())])

    with pytest.raises(TransportTimeoutError) as excinfo:
        _run(client, repo)

    assert "first failure" in str(excinfo.value)


def test_run_sync_dry_run_fetches_but_never_writes() -> None:
    id_a, media_a, wf_a = named_pair("Would set")
    project = new_id()
    id_ghost = new_id()
    client = FakeClient({project: listing_payload(media=[media_a], workflows=[wf_a])})
    repo = FakeRepo([_work(project, id_a, id_ghost)])

    summary = _run(client, repo, dry_run=True)

    assert client.fetched == [project]  # dry-run still visits
    assert repo.names == []  # ... but never writes
    assert repo.ghosts == []
    assert summary.names_written == 1  # counts would-writes
    assert summary.ghosts_marked == 1


def test_run_sync_redacted_refuses_before_any_fetch() -> None:
    client = FakeClient({})
    repo = FakeRepo([_work(new_id(), new_id())])

    with pytest.raises(ConfigurationError):
        _run(client, repo, settings=_settings("redacted"))

    assert client.fetched == []


def test_run_sync_max_projects_caps_visits() -> None:
    pairs = [named_pair(f"Name {i}") for i in range(3)]
    projects = [new_id() for _ in range(3)]
    client = FakeClient(
        {
            project: listing_payload(media=[media], workflows=[wf])
            for project, (_, media, wf) in zip(projects, pairs, strict=True)
        }
    )
    repo = FakeRepo(
        [
            _work(project, media_id)
            for project, (media_id, _, _) in zip(projects, pairs, strict=True)
        ]
    )

    summary = _run(client, repo, max_projects=2)

    assert client.fetched == projects[:2]
    assert summary.projects_visited == 2


def test_run_sync_progress_callback_receives_per_project_events() -> None:
    """Only pin THAT progress is reported per visited project — never the
    payload shape (single-channel rule, PLAN risk register)."""
    id_a, media_a, wf_a = named_pair()
    project = new_id()
    client = FakeClient({project: listing_payload(media=[media_a], workflows=[wf_a])})
    repo = FakeRepo([_work(project, id_a)])
    events: list[Any] = []

    _run(client, repo, on_progress=events.append)

    assert len(events) >= 1
