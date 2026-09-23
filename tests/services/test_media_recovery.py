"""Catalog-side behaviour of `gflow data download` (#865, #871).

The browser half is covered by `tests/api/transports/test_migrated_recover.py` and
proved live by `tests/e2e/test_data_download_e2e.py`. What is tested here is the join:
which catalog row a media id resolves to, how ambiguity and absence are reported, and
that a recovered file is recorded so `data list` stops saying `copy_count: 0`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import ConfigurationError, DataStoreError
from gflow_cli.services.media_recovery import (
    DownloadedMedia,
    download_media,
    record_local_file,
    resolve_asset,
)
from tests.fixtures.seeded_catalog import build_seeded_catalog

_ROUTE = "test.route"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 60


@pytest.fixture
def catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "catalog.db"
    store, _ = build_seeded_catalog(db)
    store.close()
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    from gflow_cli.config import reset_settings

    reset_settings()
    return db


class TestResolveAsset:
    def test_finds_a_media_id_across_all_profiles(self, catalog: Path) -> None:
        asset = resolve_asset("vid-media-alice-0", profile=None, route=_ROUTE)

        assert asset.profile_name == "alice"
        assert asset.flow_project_id == "flow-proj-alice-000"

    def test_finds_a_media_id_within_one_profile(self, catalog: Path) -> None:
        asset = resolve_asset("vid-media-alice-0", profile="alice", route=_ROUTE)

        assert asset.flow_media_id == "vid-media-alice-0"

    def test_reports_an_unknown_media_id(self, catalog: Path) -> None:
        with pytest.raises(DataStoreError, match="No local media record found"):
            resolve_asset("nope", profile=None, route=_ROUTE)

    def test_reports_an_unknown_media_id_within_a_profile(self, catalog: Path) -> None:
        """The scoped miss names the profile, so the fix is obvious from the message."""
        with pytest.raises(DataStoreError, match="profile='alice'"):
            resolve_asset("vid-media-bob-0", profile="alice", route=_ROUTE)

    def test_refuses_to_guess_between_profiles(self, catalog: Path) -> None:
        """Same media id under two profiles: name both rather than pick one."""
        with DataStore.open(catalog) as store:
            repo = DataRepository(store)
            row = repo.get_asset_by_flow_media_id("alice", "vid-media-alice-0")
            assert row is not None
            store.conn.execute(
                "INSERT INTO assets (id, profile_name, flow_project_id, flow_media_id, kind,"
                " status, created_at)"
                " VALUES (?, 'bob', 'flow-proj-bob-000', ?, 'video', 'completed', ?)",
                ("dup-asset-id", "vid-media-alice-0", "2026-09-17T00:00:00Z"),
            )
            store.conn.commit()

        with pytest.raises(DataStoreError, match="multiple profiles") as exc:
            resolve_asset("vid-media-alice-0", profile=None, route=_ROUTE)
        assert "alice" in str(exc.value)
        assert "bob" in str(exc.value)


class TestRecordLocalFile:
    def test_writes_a_row_with_size_and_checksum(self, catalog: Path, tmp_path: Path) -> None:
        asset = resolve_asset("vid-media-alice-0", profile="alice", route=_ROUTE)
        written = tmp_path / "clip.mp4"
        written.write_bytes(MP4)

        record_local_file(asset, written, len(MP4))

        with DataStore.open(catalog) as store:
            after = DataRepository(store).get_asset_by_flow_media_id("alice", "vid-media-alice-0")
        assert after is not None
        row = next(f for f in after.local_files if f.path == written)
        assert row.bytes == len(MP4)
        assert row.media_type == "video/mp4"
        # A real checksum, so `verified_local_path` can later tell this file from a
        # replaced one — a stubbed hash would silently defeat that check.
        assert row.sha256 == hashlib.sha256(MP4).hexdigest()

    def test_is_idempotent_for_the_same_path(self, catalog: Path, tmp_path: Path) -> None:
        """Re-running a recovery must not accumulate duplicate rows for one file."""
        asset = resolve_asset("vid-media-alice-0", profile="alice", route=_ROUTE)
        written = tmp_path / "clip.mp4"
        written.write_bytes(MP4)

        record_local_file(asset, written, len(MP4))
        record_local_file(asset, written, len(MP4))

        with DataStore.open(catalog) as store:
            after = DataRepository(store).get_asset_by_flow_media_id("alice", "vid-media-alice-0")
        assert after is not None
        assert [f.path for f in after.local_files].count(written) == 1


class TestDownloadMedia:
    @pytest.mark.asyncio
    async def test_refuses_a_row_with_no_project_id(
        self, catalog: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without a project id the clip route cannot be built — fail before the browser."""
        with DataStore.open(catalog) as store:
            store.conn.execute(
                "UPDATE assets SET flow_project_id = NULL WHERE flow_media_id = ?",
                ("vid-media-alice-0",),
            )
            store.conn.commit()

        def _boom(*_a: Any, **_k: Any) -> None:  # pragma: no cover - must not run
            raise AssertionError("a browser was launched for an unroutable row")

        monkeypatch.setattr("gflow_cli._cli_helpers._make_provider_dir", _boom)

        with pytest.raises(DataStoreError, match="no project id"):
            await download_media(media_id="vid-media-alice-0", profile="alice", out_dir=None)

    @pytest.mark.asyncio
    async def test_refuses_an_image_before_launching_a_browser(
        self, catalog: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#877: recovery is video-only, and the refusal has to be immediate.

        The signed URL comes from the ``as29s`` record a clip route emits; an image's
        route never emits one, so the old code accepted the id, opened Chrome, waited
        45 s and then blamed the project, the trash and the user's prompt — three
        guesses, all false, for a condition the catalog row states outright.
        """

        def _boom(*_a: Any, **_k: Any) -> None:  # pragma: no cover - must not run
            raise AssertionError("a browser was launched for an image media id")

        monkeypatch.setattr("gflow_cli._cli_helpers._make_provider_dir", _boom)

        with pytest.raises(ConfigurationError, match="video") as excinfo:
            await download_media(media_id="img-media-alice-0-0", profile="alice", out_dir=None)

        detail = str(excinfo.value)
        assert "877" in detail, detail
        # Never repeat the old guesses: the row says it is an image.
        assert "trash" not in detail.lower(), detail

        # The REMEDIATION, not just the detail. A live run caught this: the first
        # version of the fix left ConfigurationError's class default in place, so the
        # message said "check that the transport name is registered via
        # make_transport()" — one misleading hint swapped for another, with every
        # test still green because they only read the detail.
        hint = excinfo.value.remediation_hint
        assert "make_transport" not in hint, hint
        assert "transport" not in hint.lower(), hint
        assert "image" in hint.lower(), hint

    @pytest.mark.asyncio
    async def test_the_mcp_twin_refuses_an_image_too(
        self, catalog: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The MCP tool is a separate surface, so it gets its own assertion.

        It shares the service, and the service was never the risk — the adapter is.
        Here the adapter deliberately does NOT re-raise: `_guarded` funnels a
        `GFlowError` into a problem-details envelope. So the thing to pin is that the
        reason survives that conversion — an agent that got a generic failure would
        retry a media id that can never work.
        """
        from gflow_cli.mcp import tools as mcp_tools

        def _boom(*_a: Any, **_k: Any) -> None:  # pragma: no cover - must not run
            raise AssertionError("the MCP twin launched a browser for an image")

        monkeypatch.setattr("gflow_cli._cli_helpers._make_provider_dir", _boom)

        result = await mcp_tools.gflow_download_media(
            media_id="img-media-alice-0-0", profile="alice"
        )

        assert result["status"] == "error", result
        blob = str(result)
        assert "video only" in blob, blob
        assert "877" in blob, blob
        # Not the masked generic envelope — that would lose the reason entirely.
        assert "unexpected" not in blob.lower(), blob

    @pytest.mark.asyncio
    async def test_reports_the_written_file_and_records_it(
        self, catalog: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole join, with the browser half stubbed at the transport boundary."""
        from gflow_cli.api.transports.migrated_recover import RecoveredClip

        written = tmp_path / "out" / "vid-media-alice-0.mp4"
        written.parent.mkdir(parents=True)
        written.write_bytes(MP4)

        class _Client:
            page = object()

            async def __aenter__(self) -> _Client:
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

        async def _fake_recover(_page: Any, **kwargs: Any) -> RecoveredClip:
            assert kwargs["project_id"] == "flow-proj-alice-000"
            assert kwargs["out_dir"] == tmp_path / "out"
            return RecoveredClip(
                media_id=kwargs["media_id"],
                workflow_id="wf-1",
                path=written,
                bytes=len(MP4),
            )

        monkeypatch.setattr("gflow_cli._cli_helpers._make_provider_dir", lambda _n: tmp_path)
        monkeypatch.setattr("gflow_cli.api.client.FlowApiClient", lambda **_k: _Client())
        monkeypatch.setattr("gflow_cli.api.transports.migrated_recover.recover_clip", _fake_recover)

        result = await download_media(
            media_id="vid-media-alice-0", profile="alice", out_dir=tmp_path / "out"
        )

        assert isinstance(result, DownloadedMedia)
        assert result.workflow_id == "wf-1"
        assert result.profile_name == "alice"
        assert result.project_id == "flow-proj-alice-000"
        assert result.bytes == len(MP4)

        # The catalog write is the half that stops `data list` reporting copy_count: 0.
        with DataStore.open(catalog) as store:
            after = DataRepository(store).get_asset_by_flow_media_id("alice", "vid-media-alice-0")
        assert after is not None
        assert written in [f.path for f in after.local_files]
