"""The retirement warning at the one video chokepoint, ``FlowApiClient.generate_video`` (#1375).

Every video route reaches it: ``gflow video t2v/i2v/r2v/generate``, ``video chain``, ``movie
run`` and the MCP worker. The warning is emitted there before anything is submitted, and the
request always proceeds: the date only warns, Flow's menu decides.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from structlog.testing import capture_logs

from gflow_cli.api import _flow_generation
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.video import GenerateVideoRequest, Mode, VideoModel


class _Transport:
    """Records each call and how many ``model.retiring`` events existed when it came."""

    def __init__(self, logs: Sequence[Mapping[str, Any]]) -> None:
        self.logs = logs
        self.warnings_before_submit: list[int] = []

    async def generate_video(self, *, request: Any, project_id: str | None, **_: Any) -> Any:
        self.warnings_before_submit.append(
            sum(1 for e in self.logs if e["event"] == "model.retiring")
        )
        return MagicMock(project_id=project_id)


def _client(tmp_path: Path, logs: Sequence[Mapping[str, Any]]) -> tuple[FlowApiClient, _Transport]:
    client = FlowApiClient(profile_dir=tmp_path / "prof")
    transport = _Transport(logs)
    client.transport = transport  # type: ignore[assignment]
    return client, transport


def _today(monkeypatch: pytest.MonkeyPatch, day: date) -> None:
    monkeypatch.setattr(_flow_generation, "_today", lambda: day)


@pytest.mark.parametrize(
    ("day", "past"),
    [(date(2026, 12, 4), False), (date(2026, 12, 5), True), (date(2027, 1, 1), True)],
    ids=["before", "on-the-day", "after"],
)
async def test_a_leaving_model_warns_before_submit_and_is_never_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, day: date, past: bool
) -> None:
    _today(monkeypatch, day)
    with capture_logs() as logs:
        client, transport = _client(tmp_path, logs)
        await client.generate_video(
            req=GenerateVideoRequest(prompt="x", mode=Mode.T2V, model=VideoModel.VEO_3_1_FAST),
            project_id="p1",
        )
    events = [e for e in logs if e["event"] == "model.retiring"]
    assert [(e["model"], e["retires_on"], e["past"]) for e in events] == [
        ("veo_3_1_fast", "2026-12-05", past)
    ]
    assert events[0]["log_level"] == "warning"
    assert transport.warnings_before_submit == [1]  # warned first, then submitted


@pytest.mark.parametrize("model", [VideoModel.VEO_3_1_LITE, VideoModel.OMNI_1_2_FLASH, None])
async def test_a_model_flow_is_not_retiring_gets_no_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: VideoModel | None
) -> None:
    """``None`` is Flow's own default model: gflow names nothing, so it warns about nothing."""
    _today(monkeypatch, date(2026, 12, 4))
    with capture_logs() as logs:
        client, transport = _client(tmp_path, logs)
        await client.generate_video(
            req=GenerateVideoRequest(prompt="x", mode=Mode.T2V, model=model), project_id="p1"
        )
    assert not [e for e in logs if e["event"] == "model.retiring"]
    assert transport.warnings_before_submit == [0]
