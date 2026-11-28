"""Live proof that `gflow data download` recovers an already-generated clip (#865, #871).

**Why this can only be proved live.** The offline tests in
`tests/api/transports/test_migrated_recover.py` replay a scripted `batchexecute` frame,
so they assert our decoding of a shape we chose. What they cannot show is that Flow still
serves that shape: that opening `/project/<pid>/edit/<mid>` makes the app fetch the
clip's status at all, that the reply carries a signed `flow-content.google` URL rather
than the poster token, and that the bytes behind it are the original rather than one of
the transcodes Flow also serves for the same clip.

Spends **zero credits**: every asset it touches was generated and billed already. The
test picks an existing video from the local catalog — preferring one with no local file,
which is precisely the stranded state #865 reports — and proves it can be fetched.

Opt-in: ``-m e2e_data`` with ``GFLOW_CLI_E2E_PROFILE`` set.
`[[feedback-e2e-is-the-required-evidence-layer]]`
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gflow_cli._cli_helpers import client_visibility
from gflow_cli.config import get_settings
from gflow_cli.data.queries import VideoRow, list_videos
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_data]


@pytest.fixture
def real_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the user's actual catalog, not the per-test isolated one.

    ``tests/conftest.py::_isolate_settings`` redirects ``GFLOW_CLI_DB_PATH`` to a tmp
    file, and ``e2e_profile_dir`` restores only ``GFLOW_CLI_HOME``. Without this the
    lookup runs against an empty database and the test skips itself for "no catalogued
    videos" — a green run that verified nothing. Same idiom as
    ``test_asset_tagging_e2e``.
    """
    from gflow_cli.config import reset_settings

    monkeypatch.delenv("GFLOW_CLI_DB_PATH", raising=False)
    reset_settings()


def _catalog_video(profile: str) -> VideoRow:
    """A real video row for *profile*, preferring a stranded one (no local file).

    `copy_count == 0` is the #865 state: the generation was billed, the clip is in the
    Flow project, and nothing is on disk. When no orphan exists the test still runs —
    re-fetching an already-downloaded clip exercises the identical path.
    """
    rows = list_videos(
        db_path=get_settings().resolved_db_path(), profile=profile, limit=200, offset=0
    )
    usable = [r for r in rows if r.project_id and r.status != "MEDIA_GENERATION_STATUS_FAILED"]
    if not usable:
        pytest.skip(f"no catalogued videos for profile {profile!r} to recover")
    return next((r for r in usable if r.copy_count == 0), usable[0])


async def test_download_recovers_a_billed_clip_and_records_it(
    e2e_profile_dir: Path, real_catalog: None, tmp_path: Path
) -> None:
    """The whole path: catalog row -> clip route -> signed URL -> verified bytes -> DB."""
    from gflow_cli.services.media_recovery import download_media

    profile = os.environ["GFLOW_CLI_E2E_PROFILE"].strip()
    row = _catalog_video(profile)

    result = await download_media(media_id=row.media_id, profile=profile, out_dir=tmp_path)

    assert result.media_id == row.media_id
    assert result.project_id == row.project_id
    # A workflow id only exists if a real status record came back off the wire — it is
    # not in the catalog for a stranded clip (measured: both #865 orphans had NULL).
    assert result.workflow_id, "no workflow id: the status record never arrived"

    written = result.path
    assert written.exists(), f"reported {written} but nothing was written"
    body = written.read_bytes()
    assert len(body) == result.bytes
    # The two checks that distinguish the asset from what else Flow will serve here:
    # `ftyp` rejects the poster JPEG, and the size rejects the 360p/720p transcodes.
    assert body[4:8] == b"ftyp", f"not an MP4: {body[:8].hex()}"
    assert len(body) > 100_000, f"implausibly small for an 8s clip: {len(body)} B"

    # The catalog must now know about the file, or `data list` keeps reporting
    # `copy_count: 0` for an asset that is on disk — the bug that hid the orphan.
    db_path = get_settings().resolved_db_path()
    try:
        with DataStore.open(db_path) as store:
            asset = DataRepository(store).get_asset_by_flow_media_id(profile, row.media_id)
        assert asset is not None
        recorded = {str(f.path) for f in asset.local_files if f.path is not None}
        assert str(written) in recorded, f"local_files has {recorded}, not {written}"
    finally:
        # This test runs against the user's REAL catalog, and `tmp_path` is deleted
        # after it. Leaving the row behind would report `copy_count: 1` for a file that
        # no longer exists — the inverse of the bug under test. Drop just this row.
        with DataStore.open(db_path) as store:
            store.conn.execute("DELETE FROM local_files WHERE path = ?", (str(written),))
            store.conn.commit()


async def test_download_reports_an_unknown_media_id_as_a_catalog_miss(
    e2e_profile_dir: Path, real_catalog: None
) -> None:
    """An id with no catalog row fails before any browser work, with a typed error."""
    from gflow_cli.errors import DataStoreError
    from gflow_cli.services.media_recovery import download_media

    profile = os.environ["GFLOW_CLI_E2E_PROFILE"].strip()
    with pytest.raises(DataStoreError, match="No local media record found"):
        await download_media(
            media_id="00000000-0000-0000-0000-000000000000",
            profile=profile,
            out_dir=None,
        )


async def test_download_recovers_after_the_first_as29s_reply_body_is_evicted_1484(
    e2e_profile_dir: Path, real_catalog: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1484: when Chrome evicts the first route-load `as29s` body, `_await_signed_record`
    re-opens the clip route and recovers the MP4 at $0."""
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api._generated import Response

    from gflow_cli.services.media_recovery import download_media

    profile = os.environ["GFLOW_CLI_E2E_PROFILE"].strip()
    row = _catalog_video(profile)
    real_text = Response.text
    evicted = {"count": 0}

    async def text(self: Response) -> str:
        if "as29s" in self.url and evicted["count"] == 0:
            evicted["count"] += 1
            raise PlaywrightError(
                "Response.text: Protocol error (Network.getResponseBody): "
                "No data found for resource with given identifier"
            )
        return await real_text(self)

    monkeypatch.setattr(Response, "text", text)
    result = await download_media(media_id=row.media_id, profile=profile, out_dir=tmp_path)
    db_path = get_settings().resolved_db_path()
    try:
        assert evicted["count"] == 1
        assert result.path.exists()
        body = result.path.read_bytes()
        assert body[4:8] == b"ftyp" and len(body) == result.bytes
    finally:
        with DataStore.open(db_path) as store:
            store.conn.execute("DELETE FROM local_files WHERE path = ?", (str(result.path),))
            store.conn.commit()


async def test_a_lost_status_poll_settles_the_run_from_the_live_listing_1484(
    e2e_profile_dir: Path, real_catalog: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1484, generation path at $0: a finished, already-billed clip stands in for a run
    whose terminal status poll body Chrome evicted. The recovery must open the real
    project listing in a second tab, settle the run from it, and hand back a record with
    no listing URL, so the download takes the clip route (#948)."""
    import asyncio
    import time

    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.transports import _mc_submit_observe as observe
    from gflow_cli.api.transports._mc_common import LISTING_RPC
    from gflow_cli.api.transports.batchexecute import generation_record
    from gflow_cli.api.transports.migrated_composer import MigratedComposer
    from gflow_cli.exceptions import WireFormatError

    profile = os.environ["GFLOW_CLI_E2E_PROFILE"].strip()
    row = _catalog_video(profile)
    assert row.project_id is not None
    monkeypatch.setattr(observe, "RESULT_URL_GRACE_S", 0.5)
    settings = get_settings()
    async with FlowApiClient(
        profile_dir=e2e_profile_dir, visibility=client_visibility(settings)
    ) as client:
        page = client._page  # noqa: SLF001 - the e2e drives the transport on the real page
        assert page is not None
        setup = await observe.read_project_listing(page, row.project_id, budget_s=60.0)
        if setup is None:
            pytest.skip("Flow did not answer the project listing; nothing to recover from")
        try:
            first = generation_record(LISTING_RPC, setup, media_id=row.media_id)
        except WireFormatError:
            pytest.skip(f"catalogued clip {row.media_id} is not in its project listing")
        if not first.is_done:
            pytest.skip(f"catalogued clip {row.media_id} is not done in its listing")
        loop = asyncio.get_running_loop()
        watch = observe._StatusWatch(  # noqa: SLF001
            loop.create_future(), loop.create_future(), {"id": first.workflow_id}
        )
        watch.status_lost.set()  # the terminal jwpduf body was evicted
        composer = MigratedComposer()
        reads: list[float] = []
        real_read = composer._read_listing  # noqa: SLF001

        async def counted(*args: object, **kw: float) -> object:
            reads.append(kw["budget_s"])
            return await real_read(*args, **kw)  # type: ignore[arg-type]

        monkeypatch.setattr(composer, "_read_listing", counted)
        rec = await observe._finish_submitted(  # noqa: SLF001
            composer,
            page,
            first=first,
            project_id=row.project_id,
            on_started=None,
            status_watch=watch,
            deadline=time.monotonic() + 120.0,
        )
    assert rec.workflow_id == first.workflow_id and rec.media_id == row.media_id
    assert rec.is_done and rec.video_url is None
    assert len(reads) == 1
