"""#1489: a reply with fewer images than requested is reported, never silent."""

from __future__ import annotations

import io
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from rich.console import Console
from structlog.testing import capture_logs

from gflow_cli import image_batch
from gflow_cli.api._flow_generation import GenerationSurface
from gflow_cli.api.image import GenerateImageRequest
from gflow_cli.image_batch import BatchOutcome, BatchPromptItem, render_image_batch_summary
from gflow_cli.worker import codec

EVENT = "client.images_missing"


async def _drive(returned: int, requested: int) -> list[Any]:
    client = MagicMock()
    client.transport.uses_page_owned_image_recaptcha.return_value = True
    client.transport.generate_images = AsyncMock(
        return_value=[
            SimpleNamespace(media_name=f"m{i}", workflow_id=f"w{i}") for i in range(returned)
        ]
    )
    with capture_logs() as logs:
        images = await GenerationSurface._drive_images_generation(  # pyright: ignore[reportPrivateUsage]
            client,
            project_id="p",
            req=GenerateImageRequest(prompt="x", count=requested),
            recaptcha_action="imageGeneration",
        )
    assert len(images) == returned
    return [e for e in logs if e["event"] == EVENT]


@pytest.mark.asyncio
async def test_a_short_reply_is_kept_and_logged_for_every_transport() -> None:
    (event,) = await _drive(returned=3, requested=4)
    assert (event["requested"], event["returned"]) == (4, 3)
    assert event["log_level"] == "warning"


@pytest.mark.asyncio
async def test_a_full_reply_logs_nothing() -> None:
    assert await _drive(returned=4, requested=4) == []


@pytest.mark.parametrize(("saved", "note"), [(3, "3 of 4 images returned"), (4, None)])
def test_batch_summary_marks_the_short_row(
    monkeypatch: pytest.MonkeyPatch, saved: int, note: str | None
) -> None:
    out = io.StringIO()
    monkeypatch.setattr(image_batch, "console", Console(file=out, width=200))
    item = BatchPromptItem(index=0, text="x", aspect_ratio="1:1", model="nano2", count=4)
    outcome = BatchOutcome(
        index=0, prompt=item, status="ok", saved_paths=[Path(f"{i}.png") for i in range(saved)]
    )
    assert render_image_batch_summary([outcome], title="t") == 0
    found = re.search(r"\d of \d images returned", out.getvalue())
    assert (found and found[0]) == note


@pytest.mark.parametrize(
    ("task_type", "count", "checkpoint", "short"),
    [
        ("t2i", 4, {"media_ids": ["a", "b", "c"]}, True),
        ("i2i", 2, {"media_ids": ["a"]}, True),
        ("t2i", 2, {"media_ids": ["a", "b"]}, False),
        ("t2i", 4, {"media_ids": []}, False),  # nothing came back: a failure, not a short reply
        ("t2i", 4, None, False),  # a row without a checkpoint says nothing
        ("t2i", "4", {"media_ids": ["a"]}, False),  # a hand-edited row; the reply still goes out
        ("t2v", 4, {"media_ids": ["a"]}, False),
    ],
)
def test_task_warnings(task_type: str, count: Any, checkpoint: Any, short: bool) -> None:
    task: Any = SimpleNamespace(
        task_type=task_type, payload={"count": count}, checkpoint=checkpoint
    )
    types = [w["type"] for w in codec.task_warnings(task).get("warnings", [])]
    assert types == (["images_missing"] if short else [])
