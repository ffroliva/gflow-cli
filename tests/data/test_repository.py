import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gflow_cli.data.models import (
    AssetKind,
    AssetRecord,
    LocalFileRecord,
    OperationAssetRole,
    OperationKind,
    OperationRecord,
    OperationStatus,
    ProjectRecord,
)
from gflow_cli.data.repository import DataRepository, verified_local_path
from gflow_cli.data.store import DataStore
from gflow_cli.errors import DataIntegrityError


def test_upserts_project_asset_operation_and_file(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/profile_default"))
        project = repo.upsert_project(
            ProjectRecord(
                id="project-local",
                profile_name="default",
                flow_project_id="flow-project-1",
                title="gflow-cli t2i",
                source="generated",
            )
        )
        asset = repo.upsert_asset(
            AssetRecord(
                id="asset-local",
                profile_name="default",
                flow_project_id=project.flow_project_id,
                flow_media_id="media-1",
                flow_workflow_id="workflow-1",
                flow_media_generation_id="generation-1",
                kind=AssetKind.IMAGE,
                status="ready",
                model="NARWHAL",
                aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
                width=1024,
                height=1792,
                duration_seconds=None,
                seed=123,
                metadata_json={},
            )
        )
        operation = repo.insert_operation(
            OperationRecord(
                id="operation-local",
                profile_name="default",
                flow_project_id=project.flow_project_id,
                command="gflow image t2i",
                mode=OperationKind.T2I,
                status=OperationStatus.SUCCEEDED,
                flow_operation_id=None,
                flow_batch_id=None,
                prompt="a prompt",
                prompt_hash=None,
                prompt_redacted=False,
                model="NARWHAL",
                aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
                error_type=None,
                error_detail=None,
            )
        )
        repo.link_operation_asset(operation.id, asset.id, OperationAssetRole.OUTPUT, 0)
        repo.upsert_local_file(
            LocalFileRecord(
                id="file-local",
                profile_name="default",
                asset_id=asset.id,
                path=tmp_path / "media-1.png",
                media_type="image/png",
                bytes=10,
                sha256="a" * 64,
            )
        )

        found = repo.get_asset_by_flow_media_id("default", "media-1")
        assert found is not None
        assert found.flow_project_id == "flow-project-1"
        assert found.local_files[0].path == tmp_path / "media-1.png"


@pytest.mark.parametrize("raw_metadata", ["{broken", "[]", '"caption"'])
def test_asset_lookup_normalizes_malformed_or_non_object_metadata(
    tmp_path: Path, raw_metadata: str
) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/profile_default"))
        repo.upsert_asset(
            AssetRecord.minimal_image(
                id="asset-1",
                profile_name="default",
                flow_project_id="project-1",
                flow_media_id="media-1",
            )
        )
        store.conn.execute(
            "UPDATE assets SET metadata_json = ? WHERE id = ?",
            (raw_metadata, "asset-1"),
        )

        found = repo.get_asset_by_flow_media_id("default", "media-1")

        assert found is not None
        assert found.metadata_json == {}


def test_verified_local_path_requires_recorded_sha256(tmp_path: Path) -> None:
    path = tmp_path / "legacy.png"
    path.write_bytes(b"same-size replacement")
    record = LocalFileRecord(
        id="file-1",
        profile_name="default",
        asset_id="asset-1",
        path=path,
        media_type="image/png",
        bytes=path.stat().st_size,
        sha256=None,
    )

    assert verified_local_path(record) is None


def test_operation_asset_position_is_unique(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/profile_default"))
        project = repo.upsert_project(
            ProjectRecord(
                id="project-local",
                profile_name="default",
                flow_project_id="flow-project-1",
                title="title",
                source="generated",
            )
        )
        asset_one = repo.upsert_asset(
            AssetRecord.minimal_image(
                id="asset-1",
                profile_name="default",
                flow_project_id=project.flow_project_id,
                flow_media_id="media-1",
            )
        )
        asset_two = repo.upsert_asset(
            AssetRecord.minimal_image(
                id="asset-2",
                profile_name="default",
                flow_project_id=project.flow_project_id,
                flow_media_id="media-2",
            )
        )
        operation = repo.insert_operation(
            OperationRecord.minimal(
                id="operation-1",
                profile_name="default",
                flow_project_id=project.flow_project_id,
                mode=OperationKind.I2I,
            )
        )
        repo.link_operation_asset(operation.id, asset_one.id, OperationAssetRole.INPUT, 0)
        with pytest.raises(DataIntegrityError):
            repo.link_operation_asset(operation.id, asset_two.id, OperationAssetRole.INPUT, 0)


def test_upsert_project_natural_key_conflict_is_idempotent(tmp_path: Path) -> None:
    """A second upsert with the same (profile_name, flow_project_id) but a
    fresh random ``id`` must dedupe on the natural key instead of crashing.

    Regression for the live ``UNIQUE constraint failed:
    projects.profile_name, projects.flow_project_id`` bug that blocked any
    second operation in an already-recorded project.
    """
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/default"))
        repo.upsert_project(
            ProjectRecord(
                id="project-a",
                profile_name="default",
                flow_project_id="flow-project-1",
                title="A",
                source="generated",
            )
        )

        # Second upsert: same natural key, DIFFERENT (fresh) id. Must NOT raise.
        repo.upsert_project(
            ProjectRecord(
                id="project-b",
                profile_name="default",
                flow_project_id="flow-project-1",
                title="B",
                source="generated",
            )
        )

        rows = store.conn.execute(
            "SELECT id, title FROM projects WHERE profile_name = ? AND flow_project_id = ?",
            ("default", "flow-project-1"),
        ).fetchall()
        # Exactly one project row for the natural key.
        assert len(rows) == 1
        # The original row id is preserved (natural-key dedupe, not a new row).
        assert rows[0]["id"] == "project-a"
        # Mutable field (title) is updated on repeat.
        assert rows[0]["title"] == "B"


def test_upsert_project_repeat_preserves_existing_title_when_none(tmp_path: Path) -> None:
    """A repeat upsert that passes ``title=None`` must NOT clobber an existing
    non-null title (COALESCE keeps the stored value)."""
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/default"))
        repo.upsert_project(
            ProjectRecord(
                id="project-a",
                profile_name="default",
                flow_project_id="flow-project-1",
                title="Original Title",
                source="generated",
            )
        )
        repo.upsert_project(
            ProjectRecord(
                id="project-b",
                profile_name="default",
                flow_project_id="flow-project-1",
                title=None,
                source="recorded",
            )
        )

        row = store.conn.execute(
            "SELECT id, title, source, created_at FROM projects "
            "WHERE profile_name = ? AND flow_project_id = ?",
            ("default", "flow-project-1"),
        ).fetchone()
        assert row["id"] == "project-a"
        # Existing non-null title preserved despite the None on repeat.
        assert row["title"] == "Original Title"
        # Mutable source still updated.
        assert row["source"] == "recorded"


def test_upsert_asset_natural_key_conflict_raises_data_integrity_error(tmp_path: Path) -> None:
    """#543 sync invariant: the conflict target is ``ON CONFLICT(id)``.

    A re-record with the same (profile_name, flow_media_id) but a fresh id
    must raise — NOT silently replace the existing row. If someone widens the
    conflict target to the natural key, the second upsert here would succeed
    and clobber ``metadata_json`` (display_name, sync provenance), and this
    test fails.
    """
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/default"))
        repo.upsert_project(
            ProjectRecord(
                id="project-local",
                profile_name="default",
                flow_project_id="flow-project-1",
                title="title",
                source="generated",
            )
        )
        repo.upsert_asset(
            dataclasses.replace(
                AssetRecord.minimal_image(
                    id="asset-a",
                    profile_name="default",
                    flow_project_id="flow-project-1",
                    flow_media_id="media-1",
                ),
                metadata_json={"display_name": "Keep me"},
            )
        )
        with pytest.raises(DataIntegrityError):
            repo.upsert_asset(
                AssetRecord.minimal_image(
                    id="asset-b",
                    profile_name="default",
                    flow_project_id="flow-project-1",
                    flow_media_id="media-1",
                )
            )

        rows = store.conn.execute(
            "SELECT id, metadata_json FROM assets WHERE profile_name = ? AND flow_media_id = ?",
            ("default", "media-1"),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["id"] == "asset-a"
        assert json.loads(rows[0]["metadata_json"]) == {"display_name": "Keep me"}


def test_resolve_seed_image_returns_project_media_and_path(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", tmp_path / "profile_default")
        project = repo.upsert_project(
            ProjectRecord(
                id="project-local",
                profile_name="default",
                flow_project_id="flow-project-1",
                title="title",
                source="generated",
            )
        )
        asset = repo.upsert_asset(
            AssetRecord.minimal_image(
                id="asset-local",
                profile_name="default",
                flow_project_id=project.flow_project_id,
                flow_media_id="media-image-1",
            )
        )
        image_path = tmp_path / "image.png"
        image_path.write_bytes(b"image-bytes")
        repo.upsert_local_file(
            LocalFileRecord(
                id="file-local",
                profile_name="default",
                asset_id=asset.id,
                path=image_path,
                media_type="image/png",
                bytes=11,
                sha256="b" * 64,
            )
        )
        seed = repo.resolve_seed_image("default", "media-image-1")
        assert seed is not None
        assert seed.flow_project_id == "flow-project-1"
        assert seed.flow_media_id == "media-image-1"
        assert seed.local_path == image_path.resolve()


def test_seed_read_api_resolves_by_path_latest_project_and_candidate(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", tmp_path / "profile_default")
        repo.upsert_project(
            ProjectRecord(
                id="project-local",
                profile_name="default",
                flow_project_id="flow-project-1",
                title="title",
                source="generated",
            )
        )
        image_path = tmp_path / "latest.png"
        image_path.write_bytes(b"image-bytes")
        asset = repo.upsert_asset(
            AssetRecord.minimal_image(
                id="asset-latest",
                profile_name="default",
                flow_project_id="flow-project-1",
                flow_media_id="media-latest",
            )
        )
        repo.upsert_local_file(
            LocalFileRecord(
                id="file-latest",
                profile_name="default",
                asset_id=asset.id,
                path=image_path,
                media_type="image/png",
                bytes=11,
                sha256=None,
            )
        )
        by_path = repo.resolve_seed_image_by_path("default", image_path)
        latest = repo.resolve_latest_image("default", None, None, None)
        assert by_path is not None
        assert latest is not None
        assert by_path.flow_media_id == "media-latest"
        assert latest.flow_media_id == "media-latest"
        project_images = repo.list_project_images("default", "flow-project-1")
        assert [item.flow_media_id for item in project_images] == ["media-latest"]
        assert repo.candidate_image_exists("default", "media-latest") is True


def test_get_asset_by_any_id_resolves_media_workflow_and_asset_ids(tmp_path: Path) -> None:
    """PR #237: UUID→asset resolution must work for every id kind and hydrate
    the fields the MCP display-name lookup relies on (flow_workflow_id,
    metadata_json) — the original submission constructed AssetLookup with
    fields the dataclass did not declare (TypeError)."""
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/profile_default"))
        repo.upsert_project(
            ProjectRecord(
                id="project-any",
                profile_name="default",
                flow_project_id="flow-project-any",
                title="t",
                source="generated",
            )
        )
        repo.upsert_asset(
            AssetRecord(
                id="asset-any",
                profile_name="default",
                flow_project_id="flow-project-any",
                flow_media_id="media-any",
                flow_workflow_id="workflow-any",
                flow_media_generation_id="generation-any",
                kind=AssetKind.IMAGE,
                status="ready",
                model="NARWHAL",
                aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
                width=1024,
                height=1792,
                duration_seconds=None,
                seed=1,
                metadata_json={"display_name": "A cozy cabin"},
            )
        )

        for ref in ("media-any", "workflow-any", "asset-any"):
            found = repo.get_asset_by_any_id("default", ref)
            assert found is not None, ref
            assert found.flow_media_id == "media-any"
            assert found.flow_workflow_id == "workflow-any"
            assert found.metadata_json == {"display_name": "A cozy cabin"}

        assert repo.get_asset_by_any_id("default", "nope") is None
        assert repo.get_asset_by_any_id("other-profile", "media-any") is None


# ---------------------------------------------------------------------------
# Catalog sync writers + work list (#543, Task S1 red -> S4)
#
# Contract for the three new DataRepository methods:
# - set_asset_display_name(profile, media_id, name, *, source) -> bool —
#   atomic json_set patch of metadata_json: sets $.display_name,
#   $.sync.named_at (UTC ISO), $.sync.source; preserves unrelated keys;
#   overwrites a changed name (rename semantics); returns False without any
#   write for an unknown media id (never inserts) AND when the name is
#   already identical (no timestamp churn).
# - mark_asset_missing_remote(profile, media_id) -> bool — sets
#   $.sync.status='missing_remote' + $.sync.checked_at; same preservation
#   and unknown-id rules.
# - list_nameless_asset_projects(profile, *, limit, since, project_ids) ->
#   items with .flow_project_id + .media_ids (nameless, non-ghost rows only),
#   ordered by the project's earliest asset created_at DESCENDING.
# ---------------------------------------------------------------------------


def _seed_sync_asset(
    repo: DataRepository,
    *,
    asset_id: str,
    flow_project_id: str,
    flow_media_id: str,
    metadata: dict[str, object] | None = None,
    created_at: str | None = None,
) -> None:
    record = AssetRecord.minimal_image(
        id=asset_id,
        profile_name="default",
        flow_project_id=flow_project_id,
        flow_media_id=flow_media_id,
    )
    record = dataclasses.replace(record, metadata_json=metadata or {}, created_at=created_at)
    repo.upsert_asset(record)


def _asset_metadata(store: DataStore, asset_id: str) -> dict[str, object] | None:
    row = store.conn.execute(
        "SELECT metadata_json FROM assets WHERE id = ?", (asset_id,)
    ).fetchone()
    assert row is not None
    raw = row["metadata_json"]
    return json.loads(raw) if raw is not None else None


def _parseable_utc_iso(value: object) -> bool:
    assert isinstance(value, str)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None  # named contract: UTC ISO, tz-aware
    return True


def test_set_asset_display_name_patches_metadata_preserving_other_keys(
    tmp_path: Path,
) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/default"))
        _seed_sync_asset(
            repo,
            asset_id="asset-1",
            flow_project_id="flow-project-1",
            flow_media_id="media-1",
            metadata={"fife_url": "https://lh3.example/signed", "seed_of": "op-1"},
        )

        assert (
            repo.set_asset_display_name("default", "media-1", "A cozy cabin", source="sync") is True
        )

        metadata = _asset_metadata(store, "asset-1")
        assert metadata is not None
        assert metadata["display_name"] == "A cozy cabin"
        # Unrelated keys survive the json_set patch.
        assert metadata["fife_url"] == "https://lh3.example/signed"
        assert metadata["seed_of"] == "op-1"
        sync = metadata["sync"]
        assert isinstance(sync, dict)
        assert sync["source"] == "sync"
        assert _parseable_utc_iso(sync["named_at"])


def test_set_asset_display_name_overwrites_changed_name(tmp_path: Path) -> None:
    """Names are mutable via the Flow Agent — a changed remote name REPLACES
    the cached one (locked decision), always provenance-stamped."""
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/default"))
        _seed_sync_asset(
            repo,
            asset_id="asset-1",
            flow_project_id="flow-project-1",
            flow_media_id="media-1",
            metadata={"display_name": "Old name"},
        )

        assert repo.set_asset_display_name("default", "media-1", "New name", source="sync") is True

        metadata = _asset_metadata(store, "asset-1")
        assert metadata is not None
        assert metadata["display_name"] == "New name"
        sync = metadata["sync"]
        assert isinstance(sync, dict)
        assert _parseable_utc_iso(sync["named_at"])


def test_set_asset_display_name_unknown_media_returns_false_never_inserts(
    tmp_path: Path,
) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/default"))

        assert repo.set_asset_display_name("default", "media-ghost", "Nope", source="sync") is False

        count = store.conn.execute("SELECT COUNT(*) AS n FROM assets").fetchone()
        assert count["n"] == 0  # never inserts/resurrects rows


def test_set_asset_display_name_identical_name_is_noop(tmp_path: Path) -> None:
    """Re-running sync with an unchanged name must not churn sync.named_at —
    return False and leave metadata byte-for-byte identical."""
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/default"))
        _seed_sync_asset(
            repo,
            asset_id="asset-1",
            flow_project_id="flow-project-1",
            flow_media_id="media-1",
        )
        assert repo.set_asset_display_name("default", "media-1", "Same name", source="sync") is True
        before = _asset_metadata(store, "asset-1")

        assert (
            repo.set_asset_display_name("default", "media-1", "Same name", source="sync") is False
        )

        assert _asset_metadata(store, "asset-1") == before


def test_mark_asset_missing_remote_sets_status_preserving_other_keys(
    tmp_path: Path,
) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/default"))
        _seed_sync_asset(
            repo,
            asset_id="asset-1",
            flow_project_id="flow-project-1",
            flow_media_id="media-1",
            metadata={"fife_url": "https://lh3.example/signed"},
        )

        assert repo.mark_asset_missing_remote("default", "media-1") is True

        metadata = _asset_metadata(store, "asset-1")
        assert metadata is not None
        assert metadata["fife_url"] == "https://lh3.example/signed"
        sync = metadata["sync"]
        assert isinstance(sync, dict)
        assert sync["status"] == "missing_remote"
        assert _parseable_utc_iso(sync["checked_at"])


def test_mark_asset_missing_remote_unknown_media_returns_false(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/default"))

        assert repo.mark_asset_missing_remote("default", "media-ghost") is False

        count = store.conn.execute("SELECT COUNT(*) AS n FROM assets").fetchone()
        assert count["n"] == 0


def test_list_nameless_asset_projects_orders_and_filters(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/default"))
        for n, flow_project_id in ((1, "flow-project-a"), (2, "flow-project-b")):
            repo.upsert_project(
                ProjectRecord(
                    id=f"project-{n}",
                    profile_name="default",
                    flow_project_id=flow_project_id,
                    title=f"P{n}",
                    source="generated",
                )
            )
        # Project A: earliest nameless asset 2026-01-01 (plus a later one).
        _seed_sync_asset(
            repo,
            asset_id="asset-a1",
            flow_project_id="flow-project-a",
            flow_media_id="media-a1",
            created_at="2026-01-01T00:00:00.000Z",
        )
        _seed_sync_asset(
            repo,
            asset_id="asset-a2",
            flow_project_id="flow-project-a",
            flow_media_id="media-a2",
            metadata={"display_name": ""},  # empty string counts as nameless
            created_at="2026-03-01T00:00:00.000Z",
        )
        # Project B: earliest asset 2026-02-01 -> most recent project first.
        _seed_sync_asset(
            repo,
            asset_id="asset-b1",
            flow_project_id="flow-project-b",
            flow_media_id="media-b1",
            created_at="2026-02-01T00:00:00.000Z",
        )
        # Excluded rows: already named / already ghost-marked.
        _seed_sync_asset(
            repo,
            asset_id="asset-a3",
            flow_project_id="flow-project-a",
            flow_media_id="media-a3",
            metadata={"display_name": "Named already"},
            created_at="2026-01-02T00:00:00.000Z",
        )
        _seed_sync_asset(
            repo,
            asset_id="asset-b2",
            flow_project_id="flow-project-b",
            flow_media_id="media-b2",
            metadata={"sync": {"status": "missing_remote"}},
            created_at="2026-02-02T00:00:00.000Z",
        )
        # NULL metadata_json (recorder legacy rows) also counts as nameless.
        store.conn.execute("UPDATE assets SET metadata_json = NULL WHERE id = 'asset-a1'")

        work = repo.list_nameless_asset_projects("default")
        assert [item.flow_project_id for item in work] == [
            "flow-project-b",
            "flow-project-a",
        ]
        by_project = {item.flow_project_id: item for item in work}
        assert set(by_project["flow-project-a"].media_ids) == {"media-a1", "media-a2"}
        assert set(by_project["flow-project-b"].media_ids) == {"media-b1"}
        assert isinstance(by_project["flow-project-a"].media_ids, tuple)

        limited = repo.list_nameless_asset_projects("default", limit=1)
        assert [item.flow_project_id for item in limited] == ["flow-project-b"]

        scoped = repo.list_nameless_asset_projects("default", project_ids=["flow-project-a"])
        assert [item.flow_project_id for item in scoped] == ["flow-project-a"]

        since = repo.list_nameless_asset_projects(
            "default", since=datetime(2026, 1, 15, tzinfo=UTC)
        )
        # Project A's only qualifying-window rows: media-a2 (2026-03-01).
        assert {item.flow_project_id for item in since} == {
            "flow-project-a",
            "flow-project-b",
        }
        since_by_project = {item.flow_project_id: item for item in since}
        # Media-level filtering: the pre-since row is excluded, not just
        # project membership.
        assert "media-a1" not in since_by_project["flow-project-a"].media_ids
        assert since_by_project["flow-project-a"].media_ids == ("media-a2",)
        since_late = repo.list_nameless_asset_projects(
            "default", since=datetime(2026, 2, 15, tzinfo=UTC)
        )
        assert [item.flow_project_id for item in since_late] == ["flow-project-a"]

        other_profile = repo.list_nameless_asset_projects("nobody")
        assert other_profile == []


def test_clear_missing_remote_removes_status_preserving_other_keys(tmp_path: Path) -> None:
    """Un-ghost on reappearance (#543): json_remove drops ONLY $.sync.status;
    display_name, sync.named_at, and unrelated keys survive; checked_at is
    re-stamped."""
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/default"))
        _seed_sync_asset(
            repo,
            asset_id="asset-1",
            flow_project_id="flow-project-1",
            flow_media_id="media-1",
            metadata={
                "display_name": "Kept name",
                "fife_url": "https://lh3.example/signed",
                "sync": {
                    "status": "missing_remote",
                    "named_at": "2026-01-01T00:00:00.000Z",
                    "checked_at": "2026-01-01T00:00:00.000Z",
                },
            },
        )

        assert repo.clear_missing_remote("default", "media-1") is True

        metadata = _asset_metadata(store, "asset-1")
        assert metadata is not None
        assert metadata["display_name"] == "Kept name"
        assert metadata["fife_url"] == "https://lh3.example/signed"
        sync = metadata["sync"]
        assert isinstance(sync, dict)
        assert "status" not in sync  # the tombstone flag is gone
        assert sync["named_at"] == "2026-01-01T00:00:00.000Z"
        assert _parseable_utc_iso(sync["checked_at"])
        assert sync["checked_at"] != "2026-01-01T00:00:00.000Z"  # re-stamped


def test_clear_missing_remote_non_ghost_or_unknown_returns_false(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/default"))
        _seed_sync_asset(
            repo,
            asset_id="asset-1",
            flow_project_id="flow-project-1",
            flow_media_id="media-1",
            metadata={"display_name": "Not a ghost"},
        )
        before = _asset_metadata(store, "asset-1")

        assert repo.clear_missing_remote("default", "media-1") is False  # not ghost-marked
        assert repo.clear_missing_remote("default", "media-unknown") is False  # unknown id

        assert _asset_metadata(store, "asset-1") == before  # untouched


def test_list_missing_remote_media_scopes_by_project_and_profile(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/default"))
        ghost_meta: dict[str, object] = {"sync": {"status": "missing_remote"}}
        _seed_sync_asset(
            repo,
            asset_id="asset-1",
            flow_project_id="flow-project-a",
            flow_media_id="media-ghost-1",
            metadata=dict(ghost_meta),
        )
        _seed_sync_asset(
            repo,
            asset_id="asset-2",
            flow_project_id="flow-project-a",
            flow_media_id="media-alive",
            metadata={"display_name": "Alive"},
        )
        _seed_sync_asset(
            repo,
            asset_id="asset-3",
            flow_project_id="flow-project-b",
            flow_media_id="media-ghost-2",
            metadata=dict(ghost_meta),
        )

        ghosts = repo.list_missing_remote_media("default", "flow-project-a")
        assert ghosts == ("media-ghost-1",)  # ghost rows only, this project only
        assert isinstance(ghosts, tuple)
        assert repo.list_missing_remote_media("nobody", "flow-project-a") == ()
