"""Tests for chained extends — the `--extend N` path.

The chain is where the predict council's two hard STOPs live, so they are
pinned as behaviour here:

* **serial by construction** — a 15-segment run submits one at a time. The
  110s generation floors the interval at the top of the measured-safe band
  (#241: 14 submissions in 10 min ended in a 403), and concurrency would
  collapse exactly that protection.
* **no auto-retry on refusal** — aborting with partials preserved is the
  documented contract; re-submitting into a wall is what ACCOUNT_SAFETY.md
  disowns.

Plus the property that makes an interrupted run recoverable: a segment is
recorded when it is SUBMITTED, not when it is downloaded. It is billed at
submit, so a crash in between must not make it look like it never happened.
"""

from __future__ import annotations

from typing import Any

import pytest

from gflow_cli.api.extend_chain import ExtendChainResult, run_extend_chain
from gflow_cli.api.video_extend import ExtendStarted
from gflow_cli.errors import WafRejectionError

MEDIA = "b9458021-fc2d-4d95-ab53-cf844c6f1079"
PROJECT = "7d3d6bd9-a39f-4c2d-b772-146e73e539cf"
SCENE = "d7d1cc78-7a31-4924-a4aa-0669141a1ed8"


class _FakeClient:
    """Records the order of submits so serialism is observable."""

    def __init__(self, *, fail_at: int | None = None) -> None:
        self.events: list[str] = []
        self.submits: list[dict[str, Any]] = []
        self._n = 0
        self._fail_at = fail_at

    async def extend_video(self, **kwargs: Any) -> ExtendStarted:
        self._n += 1
        if self._fail_at is not None and self._n == self._fail_at:
            self.events.append(f"submit{self._n}-refused")
            raise WafRejectionError("blocked")
        self.events.append(f"submit{self._n}")
        self.submits.append(kwargs)
        return ExtendStarted(
            media_id=f"media-{self._n}",
            workflow_id=f"wf-{self._n}",
            model_key="veo_3_1_extension_lite",
            unit_cost=10,
        )

    async def poll_video_status(self, media_id: str, **_k: Any) -> Any:
        self.events.append(f"poll-{media_id}")
        return object()


@pytest.mark.asyncio
async def test_submits_one_segment_at_a_time() -> None:
    """Each submit must be followed by its poll before the next submit. Any
    interleaving would mean two generations in flight on one profile."""
    c = _FakeClient()
    await run_extend_chain(
        c, media_id=MEDIA, project_id=PROJECT, scene_id=SCENE, prompts=["a", "b", "c"]
    )
    assert c.events == [
        "submit1",
        "poll-media-1",
        "submit2",
        "poll-media-2",
        "submit3",
        "poll-media-3",
    ]


@pytest.mark.asyncio
async def test_each_segment_seeds_from_the_previous_one() -> None:
    """Tail-only chaining: the server caps input at 8s
    (inputSpec.maxInputV2vVideoDuration), so segment N+1 continues segment N,
    never the whole growing scene."""
    c = _FakeClient()
    await run_extend_chain(
        c, media_id=MEDIA, project_id=PROJECT, scene_id=SCENE, prompts=["a", "b", "c"]
    )
    assert [s["media_id"] for s in c.submits] == [MEDIA, "media-1", "media-2"]
    assert [s["position"] for s in c.submits] == [1, 2, 3]


@pytest.mark.asyncio
async def test_reuses_the_last_prompt_when_fewer_than_segments() -> None:
    c = _FakeClient()
    await run_extend_chain(
        c, media_id=MEDIA, project_id=PROJECT, scene_id=SCENE, prompts=["a"], segments=3
    )
    assert [s["prompt"] for s in c.submits] == ["a", "a", "a"]


@pytest.mark.asyncio
async def test_aborts_without_retrying_and_keeps_partials() -> None:
    """A refusal ends the run. Retrying only raises per-profile heat and never
    succeeds — the tier-403 rule in errors.py applies here verbatim."""
    c = _FakeClient(fail_at=3)
    result = await run_extend_chain(
        c, media_id=MEDIA, project_id=PROJECT, scene_id=SCENE, prompts=["a", "b", "c", "d"]
    )
    assert isinstance(result, ExtendChainResult)
    assert result.completed_media_ids == ["media-1", "media-2"]
    assert result.aborted is True
    assert c.events.count("submit3-refused") == 1
    assert not any(e.startswith("submit4") for e in c.events)


@pytest.mark.asyncio
async def test_records_at_submit_not_at_completion() -> None:
    """A segment is billed when Flow accepts it. If the recorder only fired
    after the poll, an interrupt in between would lose a paid segment."""
    recorded: list[str] = []
    c = _FakeClient()
    await run_extend_chain(
        c,
        media_id=MEDIA,
        project_id=PROJECT,
        scene_id=SCENE,
        prompts=["a", "b"],
        on_submitted=lambda started: recorded.append(started.media_id),
    )
    # Recorded before its own poll ran, for every segment.
    assert recorded == ["media-1", "media-2"]
    assert c.events.index("poll-media-1") > 0


@pytest.mark.asyncio
async def test_paces_between_segments() -> None:
    """Submission cadence must not be machine-perfect. `chain.py` defaults
    jitter to 0.0; extend must not inherit that."""
    slept: list[float] = []

    async def _sleep(s: float) -> None:
        slept.append(s)

    c = _FakeClient()
    await run_extend_chain(
        c,
        media_id=MEDIA,
        project_id=PROJECT,
        scene_id=SCENE,
        prompts=["a", "b", "c"],
        jitter_range=(2.0, 10.0),
        sleep=_sleep,
    )
    # One pause between segments, none before the first or after the last.
    assert len(slept) == 2
    assert all(2.0 <= s <= 10.0 for s in slept)


@pytest.mark.asyncio
async def test_resume_continues_at_the_given_position() -> None:
    """Resuming a scene that already holds N clips must append at N, not
    overwrite position 1 — those earlier clips are billed and real."""
    c = _FakeClient()
    await run_extend_chain(
        c,
        media_id="tail-media",
        project_id=PROJECT,
        scene_id=SCENE,
        prompts=["a", "b"],
        start_position=3,
    )
    assert [s["position"] for s in c.submits] == [3, 4]
    assert c.submits[0]["media_id"] == "tail-media"
