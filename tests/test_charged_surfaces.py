"""#1201 review: billed paths must not report ``charged: no``, and raise sites that
submitted nothing must not read ``reconcile``.

Each test drives the real raise site (or the real orchestration) rather than a copy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.video import GenerateVideoRequest, VideoModel, VideoResult, VideoStatus
from gflow_cli.chain import ChainLinkSpec, run_chain
from gflow_cli.errors import FlowAppError, InsufficientCreditsError, is_retryable


class _PageAt:
    def __init__(self, url: str) -> None:
        self.url = url


def _extract(src: Path, dst: Path, *, offset_ms: int = 0) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(b"\xff\xd8\xff\xd9")
    return dst


@pytest.mark.asyncio
async def test_an_unwrapped_error_after_billed_links_reads_billed(tmp_path: Path) -> None:
    """Link 0 is billed; link 1 fails with a class whose own default is ``no``."""
    done = tmp_path / "link0.mp4"
    calls = 0

    async def _gen(*, req: GenerateVideoRequest, **_: Any) -> VideoResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            done.write_bytes(b"\x00\x00\x00\x18ftypmp42")
            return VideoResult(
                status=VideoStatus(media_id="m0", status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
                local_path=done,
            )
        raise InsufficientCreditsError("short")

    client = MagicMock()
    client.generate_video = AsyncMock(side_effect=_gen)
    with pytest.raises(InsufficientCreditsError) as info:
        await run_chain(
            client=client,
            links=[ChainLinkSpec(prompt="a"), ChainLinkSpec(prompt="b")],
            out_dir=tmp_path,
            model=VideoModel.VEO_3_1_LITE,
            extractor=_extract,
        )
    pd = info.value.to_problem_details()
    assert (pd["charged"], pd["next_action"]) == ("yes", "reconcile")
    assert is_retryable(info.value) is False


@pytest.mark.asyncio
async def test_an_error_at_link_zero_keeps_its_own_answer(tmp_path: Path) -> None:
    client = MagicMock()
    client.generate_video = AsyncMock(side_effect=InsufficientCreditsError("short"))
    with pytest.raises(InsufficientCreditsError) as info:
        await run_chain(
            client=client,
            links=[ChainLinkSpec(prompt="a")],
            out_dir=tmp_path,
            model=VideoModel.VEO_3_1_LITE,
            extractor=_extract,
        )
    assert info.value.to_problem_details()["charged"] == "no"


def test_the_extend_editor_guard_submitted_nothing() -> None:
    from gflow_cli.api.transports.migrated_extend import (
        _confirm_editor,  # pyright: ignore[reportPrivateUsage]
    )

    with pytest.raises(FlowAppError) as info:
        _confirm_editor(_PageAt("https://flow.google.com/about"), project_id="p", media_id="m")  # type: ignore[arg-type]
    pd = info.value.to_problem_details()
    assert (pd["charged"], pd["next_action"]) == ("no", "none")


@pytest.mark.asyncio
async def test_the_about_landing_says_reauth_not_reconcile() -> None:
    from gflow_cli.api.transports._common import raise_if_known_landing

    with pytest.raises(FlowAppError) as info:
        await raise_if_known_landing(
            _PageAt("https://flow.google.com/about"), requested="x", at="test"
        )
    pd = info.value.to_problem_details()
    assert (pd["charged"], pd["next_action"]) == ("no", "reauth")


@pytest.mark.asyncio
async def test_a_labs_video_poll_timeout_after_submit_is_reconciled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.errors import TransportTimeoutError

    media = "37930141-ee54-4fe2-9f60-9eb959ca11ff"
    c = FlowApiClient(profile_dir=tmp_path)
    active = {
        "media": [
            {
                "name": media,
                "mediaMetadata": {
                    "mediaStatus": {"mediaGenerationStatus": "MEDIA_GENERATION_STATUS_ACTIVE"}
                },
            }
        ]
    }
    c._post_json = AsyncMock(return_value=active)  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
    ticks = iter([0.0] + [1000.0] * 50)
    monkeypatch.setattr("gflow_cli.api.client.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("gflow_cli.api.client.asyncio.sleep", AsyncMock())
    with pytest.raises(TransportTimeoutError) as info:
        await c.poll_video_status(media, project_id="p", initial_delay_s=0.0, timeout_s=60.0)
    pd = info.value.to_problem_details()
    assert (pd["charged"], pd["next_action"], pd["operation_id"]) == ("maybe", "reconcile", media)


def test_a_failed_video_result_carries_the_members() -> None:
    from gflow_cli.api.video import Aspect, Mode
    from gflow_cli.json_output import video_result

    req = GenerateVideoRequest(prompt="p", mode=Mode.T2V, aspect=Aspect.LANDSCAPE)
    failed = VideoResult(
        status=VideoStatus(media_id="m", status="MEDIA_GENERATION_STATUS_FAILED"), local_path=None
    )
    payload = video_result(command="video t2v", request=req, result=failed)
    assert (payload["charged"], payload["next_action"]) == ("maybe", "reconcile")
    ok = VideoResult(
        status=VideoStatus(media_id="m", status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
        local_path=None,
    )
    assert "charged" not in video_result(command="video t2v", request=req, result=ok)


def test_a_partial_chain_document_carries_the_error_members(tmp_path: Path) -> None:
    from gflow_cli.cli_video import _chain_json  # pyright: ignore[reportPrivateUsage]
    from gflow_cli.errors import ChainPartialError

    exc = ChainPartialError("x", partial_results=[tmp_path / "a.mp4"])
    payload = _chain_json(
        chain_id="c", results=[], partial=True, completed_paths=exc.partial_results, error=exc
    )
    assert (payload["charged"], payload["next_action"]) == ("yes", "reconcile")


def test_the_task_reply_fallback_carries_the_members(tmp_path: Path) -> None:
    from gflow_cli.data.store import DataStore
    from gflow_cli.mcp.tools import task_payload
    from gflow_cli.worker.queue import QueueRepository

    db = tmp_path / "q.db"
    with DataStore.open(db) as store:
        store.conn.execute(
            "INSERT OR IGNORE INTO profiles (name, first_seen_at) VALUES ('p', CURRENT_TIMESTAMP)"
        )
        QueueRepository(store).enqueue_task(
            task_id="t1", profile_name="p", task_type="t2i", payload={}
        )
    err = task_payload("p", "t1", db)["error"]
    assert (err["charged"], err["next_action"], err["retryable"]) == ("maybe", "reconcile", False)
