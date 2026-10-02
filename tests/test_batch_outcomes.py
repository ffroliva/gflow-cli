"""Batch outcomes with intra-batch references: identity, skips, download vs generation (#913).

SCENARIO #8, #9, #10, #13, #35, #38. A fake client stands in for Flow; every row is
checked by its own index, never by its position in the run order.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.api.dto import GeneratedImage, ProjectInfo
from gflow_cli.api.recaptcha import RecaptchaError
from gflow_cli.errors import TransportTimeoutError
from gflow_cli.image_batch import (
    BatchOutcome,
    BatchPromptItem,
    render_image_batch_summary,
    run_image_batch,
)


def _img(media: str) -> GeneratedImage:
    return GeneratedImage(
        media_name=media,
        workflow_id=f"wf-{media}",
        seed=1,
        prompt="p",
        model_name_type=None,
        aspect_ratio="IMAGE_ASPECT_RATIO_SQUARE",
        fife_url=f"https://lh3.googleusercontent.com/{media}",
        dimensions=(1024, 1024),
        display_name=f"caption {media}",
    )


class FakeClient:
    """Generation and download behaviour scripted per prompt text."""

    def __init__(self, *, fail_generate: set[str], fail_download: set[str]) -> None:
        self.fail_generate = fail_generate
        self.fail_download = fail_download
        self.fail_mint: set[str] = set()
        self.generated: list[str] = []

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def create_project(self, title: str) -> ProjectInfo:
        return ProjectInfo(project_id="11111111-1111-1111-1111-111111111111", title=title)

    async def generate_image(self, project_id: str, req: Any) -> GeneratedImage:
        self.generated.append(req.prompt)
        if req.prompt in self.fail_generate:
            raise TransportTimeoutError(detail="scripted generation failure")
        if req.prompt in self.fail_mint:
            raise RecaptchaError("scripted mint failure")
        return _img(f"media-{req.prompt}")

    async def download_image(self, img: GeneratedImage, target: Path) -> Path:
        if img.media_name.removeprefix("media-") in self.fail_download:
            raise ValueError("scripted download failure")
        target.write_bytes(b"\x89PNG")
        return target


def _run(
    tmp_path: Path,
    rows: list[BatchPromptItem],
    *,
    fail_generate: set[str] = frozenset(),  # type: ignore[assignment]
    fail_download: set[str] = frozenset(),  # type: ignore[assignment]
    continue_on_error: bool = True,
) -> tuple[list[BatchOutcome], FakeClient]:
    client = FakeClient(fail_generate=set(fail_generate), fail_download=set(fail_download))
    outcomes = asyncio.run(
        run_image_batch(
            profile_dir=tmp_path,
            headless=True,
            transport=None,
            prompts=tuple(rows),
            output_dir=tmp_path / "out",
            continue_on_error=continue_on_error,
            project_title="t",
            client_factory=lambda **_: client,
            jitter_range=(0, 0),
            _command="run",
        )
    )
    return outcomes, client


def _row(index: int, text: str, ref: str | None = None) -> BatchPromptItem:
    return BatchPromptItem(text=text, index=index, ref=ref)


def _by_index(outcomes: list[BatchOutcome]) -> dict[int, BatchOutcome]:
    return {o.index: o for o in outcomes}


def test_no_references_is_unchanged(tmp_path: Path) -> None:
    outcomes, client = _run(tmp_path, [_row(0, "a"), _row(1, "b")])
    assert client.generated == ["a", "b"]
    assert [o.index for o in outcomes] == [0, 1]
    assert (tmp_path / "out" / "prompt_0_0.png").exists()
    assert (tmp_path / "out" / "prompt_1_0.png").exists()


def test_dependency_order_keeps_each_rows_identity(tmp_path: Path) -> None:
    outcomes, client = _run(tmp_path, [_row(0, "child", "batch:1"), _row(1, "parent")])
    assert client.generated == ["parent", "child"]
    out = _by_index(outcomes)
    assert out[0].prompt.text == "child"
    assert out[0].saved_paths == [tmp_path / "out" / "prompt_0_0.png"]
    assert out[1].saved_paths == [tmp_path / "out" / "prompt_1_0.png"]
    assert [o.index for o in outcomes] == [0, 1]  # reported in file order


def test_failed_parent_skips_its_descendants(tmp_path: Path) -> None:
    rows = [_row(0, "a"), _row(1, "b", "batch:0"), _row(2, "c", "batch:1"), _row(3, "d")]
    outcomes, client = _run(tmp_path, rows, fail_generate={"a"})
    out = _by_index(outcomes)
    assert client.generated == ["a", "d"]  # b and c never submitted
    assert out[1].status == out[2].status == "skipped"
    assert "parent row 0 failed" in (out[1].error or "")
    assert "parent row 1" in (out[2].error or "")
    assert out[3].status == "ok"


def test_fail_fast_skips_carry_their_own_index(tmp_path: Path) -> None:
    rows = [_row(0, "child", "batch:1"), _row(1, "parent"), _row(2, "other")]
    outcomes, _ = _run(tmp_path, rows, fail_generate={"parent"}, continue_on_error=False)
    out = _by_index(outcomes)
    assert out[1].status == "fail"
    assert out[0].status == "skipped"
    assert out[2].status == "skipped"
    assert sorted(out) == [0, 1, 2]


def test_download_failure_keeps_the_generated_image_for_children(tmp_path: Path) -> None:
    rows = [_row(0, "a"), _row(1, "b", "batch:0")]
    outcomes, client = _run(tmp_path, rows, fail_download={"a"})
    out = _by_index(outcomes)
    assert out[0].status == "fail"
    assert "download" in (out[0].error or "").lower()
    assert [i.media_name for i in out[0].images] == ["media-a"]
    assert client.generated == ["a", "b"]  # the child still runs
    assert out[1].status == "ok"


def test_summary_shows_why_a_row_was_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outcomes, _ = _run(tmp_path, [_row(0, "a"), _row(1, "b", "batch:0")], fail_generate={"a"})
    code = render_image_batch_summary(outcomes, title="t")
    printed = capsys.readouterr().out
    assert "parent row 0 failed" in printed
    assert code != 0


def test_a_mint_failure_fails_its_row_and_the_run_continues(tmp_path: Path) -> None:
    """#915: a RecaptchaError used to escape the batch (not a GFlowError) and end the run."""
    client = FakeClient(fail_generate=set(), fail_download=set())
    client.fail_mint = {"a"}
    outcomes = asyncio.run(
        run_image_batch(
            profile_dir=tmp_path,
            headless=True,
            transport=None,
            prompts=(_row(0, "a"), _row(1, "b"), _row(2, "c")),
            output_dir=tmp_path / "out",
            continue_on_error=True,
            project_title="t",
            client_factory=lambda **_: client,
            jitter_range=(0, 0),
            _command="run",
        )
    )
    assert client.generated == ["a", "b", "c"]
    assert [(o.index, o.status) for o in outcomes] == [(0, "fail"), (1, "ok"), (2, "ok")]
    assert "RecaptchaError" in (outcomes[0].error or "")
