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
@pytest.mark.parametrize("returned", [4, 5])
async def test_a_full_reply_logs_nothing(returned: int) -> None:
    assert await _drive(returned=returned, requested=4) == []


def test_batch_summary_counts_and_labels_every_status(monkeypatch: pytest.MonkeyPatch) -> None:
    out = io.StringIO()
    monkeypatch.setattr(image_batch, "console", Console(file=out, width=200))
    item = BatchPromptItem(text="x")
    statuses = ["ok", "fail", "fail", "skipped", "skipped", "skipped"] + ["resumed"] * 4
    outcomes = [
        BatchOutcome(index=i, prompt=item, status=s, error=f"why-{s}", saved_paths=[Path("a.png")])
        for i, s in enumerate(statuses)
    ]
    render_image_batch_summary(outcomes, title="t")
    text = out.getvalue()
    assert "1/10 succeeded · 2 failure(s) · 3 skipped · 4 resumed" in text
    assert [text.count(w) for w in ("OK", "FAIL", "SKIPPED", "RESUMED")] == [1, 2, 3, 4]
    assert [text.count(w) for w in ("why-fail", "why-skipped")] == [2, 3]


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
        ("t2i", 4, {"media_ids": 7}, False),
        ("t2i", 4, ["not", "a", "dict"], False),  # a hand-edited checkpoint
        ("t2v", 4, "not a dict", False),
        ("t2i", None, {"media_ids": ["a"]}, False),  # no count in the payload means one
        ("t2v", 4, {"media_ids": ["a"]}, False),
    ],
)
def test_task_warnings(task_type: str, count: Any, checkpoint: Any, short: bool) -> None:
    payload = {} if count is None else {"count": count}
    task: Any = SimpleNamespace(task_type=task_type, payload=payload, checkpoint=checkpoint)
    types = [w["type"] for w in codec.task_warnings(task).get("warnings", [])]
    assert types == (["images_missing"] if short else [])


def test_a_bool_count_is_not_read_as_a_number(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[tuple[int, int]] = []
    monkeypatch.setattr(codec, "images_missing_warning", lambda r, n: called.append((r, n)) or [])
    task: Any = SimpleNamespace(
        task_type="t2i", payload={"count": True}, checkpoint={"media_ids": ["a"]}
    )
    assert codec.task_warnings(task) == {}
    assert called == []
