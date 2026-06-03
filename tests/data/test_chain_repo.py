"""Tests for the chain-link persistence layer (migration 0005 + recorder).

Each test opens its own ``DataStore`` on a ``tmp_path`` DB, so the autouse
``_isolate_settings`` fixture's redirected DB is never touched and tests cannot
pollute a real catalog (see memory: data-layer-test-pollution-trap).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.chain import ChainLinkResult, ChainRecorder
from gflow_cli.data.chain_repo import ChainLinkRecorder
from gflow_cli.data.models import ChainLinkRecord
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import DataStoreError


def _recorder(store: DataStore, tmp_path: Path, chain_id: str = "chain-1") -> ChainLinkRecorder:
    return ChainLinkRecorder(
        DataRepository(store),
        profile_name="default",
        profile_dir=tmp_path / "profile_default",
        chain_id=chain_id,
    )


# ----------------------------------------------------------------------
# Migration
# ----------------------------------------------------------------------


def test_migration_0005_creates_chain_links_table(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        row = store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chain_links'",
        ).fetchone()
        assert row is not None
        versions = [
            r["version"]
            for r in store.conn.execute("SELECT version FROM schema_migrations").fetchall()
        ]
        assert "0005" in versions


def test_migration_0005_is_idempotent_and_checksum_valid(tmp_path: Path) -> None:
    db = tmp_path / "gflow.db"
    with DataStore.open(db):
        pass
    # Re-opening must not re-apply (checksum match) and must not raise.
    with DataStore.open(db) as store:
        rows = store.conn.execute(
            "SELECT version FROM schema_migrations WHERE version = '0005'",
        ).fetchall()
        assert len(rows) == 1


# ----------------------------------------------------------------------
# record_chain_link — insert + retrieve
# ----------------------------------------------------------------------


def test_record_chain_link_inserts_retrievable_row(tmp_path: Path) -> None:
    clip = tmp_path / "link0.mp4"
    clip.write_bytes(b"mp4")
    frame = tmp_path / "link0_lastframe.jpg"
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = _recorder(store, tmp_path)
        recorder.record_chain_link(
            ChainLinkResult(
                index=0,
                prompt="opening shot",
                local_path=clip,
                media_id="media-0",
                frame_path=frame,
                project_id="flow-project-1",
                flow_operation_id="op-0",
            ),
        )
        links = recorder.completed_links()
        assert len(links) == 1
        link = links[0]
        assert link.link_index == 0
        assert link.flow_media_id == "media-0"
        assert link.local_path == str(clip)
        assert link.seed_frame_path == str(frame)
        assert link.flow_project_id == "flow-project-1"
        assert link.flow_operation_id == "op-0"
        assert link.prompt == "opening shot"


def test_record_chain_link_satisfies_protocol(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder: ChainRecorder = _recorder(store, tmp_path)
        recorder.record_chain_link(
            ChainLinkResult(
                index=0,
                prompt="p",
                local_path=tmp_path / "c.mp4",
                media_id="m",
            ),
        )
        assert isinstance(recorder, ChainLinkRecorder)


def test_record_chain_link_is_idempotent_on_index(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = _recorder(store, tmp_path)
        result = ChainLinkResult(
            index=0,
            prompt="p",
            local_path=tmp_path / "c.mp4",
            media_id="m",
            frame_path=tmp_path / "f.jpg",
        )
        recorder.record_chain_link(result)
        recorder.record_chain_link(result)  # re-record same link
        links = recorder.completed_links()
        assert len(links) == 1


# ----------------------------------------------------------------------
# Resume query — needs-extraction vs fully-done
# ----------------------------------------------------------------------


def test_completed_links_distinguishes_needs_extraction(tmp_path: Path) -> None:
    """A link with seed_frame_path NULL -> restart at extraction, not regen."""
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = _recorder(store, tmp_path)
        # Link 0: fully done (clip + seed frame).
        recorder.record_chain_link(
            ChainLinkResult(
                index=0,
                prompt="p0",
                local_path=tmp_path / "l0.mp4",
                media_id="m0",
                frame_path=tmp_path / "l0.jpg",
            ),
        )
        # Link 1: clip recorded but extraction never ran (crash in the gap).
        recorder.record_chain_link(
            ChainLinkResult(
                index=1,
                prompt="p1",
                local_path=tmp_path / "l1.mp4",
                media_id="m1",
                frame_path=None,
            ),
        )
        links = recorder.completed_links()
        assert [link.link_index for link in links] == [0, 1]
        assert links[0].seed_frame_path is not None  # fully done
        assert links[1].seed_frame_path is None  # needs extraction


def test_completed_links_scoped_by_chain_and_ordered(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        rec_a = _recorder(store, tmp_path, chain_id="chain-a")
        rec_b = _recorder(store, tmp_path, chain_id="chain-b")
        # Insert out of order to prove ordering by link_index.
        for idx in (1, 0):
            rec_a.record_chain_link(
                ChainLinkResult(
                    index=idx,
                    prompt=f"a{idx}",
                    local_path=tmp_path / f"a{idx}.mp4",
                    media_id=f"ma{idx}",
                ),
            )
        rec_b.record_chain_link(
            ChainLinkResult(
                index=0,
                prompt="b0",
                local_path=tmp_path / "b0.mp4",
                media_id="mb0",
            ),
        )
        a_links = rec_a.completed_links()
        assert [link.link_index for link in a_links] == [0, 1]
        b_links = rec_b.completed_links()
        assert len(b_links) == 1
        assert b_links[0].flow_media_id == "mb0"


def test_completed_links_empty_for_unknown_chain(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = _recorder(store, tmp_path, chain_id="never-run")
        assert recorder.completed_links() == []


# ----------------------------------------------------------------------
# Error semantics — recorder failure -> DataStoreError (exit 16)
# ----------------------------------------------------------------------


def test_record_chain_link_wraps_sqlite_error_as_datastoreerror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlite3

    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = _recorder(store, tmp_path)

        def boom(record: ChainLinkRecord) -> ChainLinkRecord:
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(recorder.repository, "upsert_chain_link", boom)
        with pytest.raises(DataStoreError):
            recorder.record_chain_link(
                ChainLinkResult(
                    index=0,
                    prompt="p",
                    local_path=tmp_path / "c.mp4",
                    media_id="m",
                ),
            )
