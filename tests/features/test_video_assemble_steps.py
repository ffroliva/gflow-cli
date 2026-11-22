"""CLI BDD contract for local video assembly."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import av
import numpy as np
import pytest
from click.testing import CliRunner
from pytest_bdd import given, scenarios, then, when

from gflow_cli.cli import main

scenarios("video_assemble.feature")


@pytest.fixture
def assembly_context(tmp_path: Path) -> dict[str, Any]:
    return {"directory": tmp_path}


def _make_clip(path: Path, *, size: tuple[int, int] = (64, 48)) -> None:
    with av.open(str(path), "w", format="mp4") as output:
        stream = output.add_stream("mpeg4", rate=10)
        stream.width, stream.height = size
        stream.pix_fmt = "yuv420p"
        for index in range(5):
            frame = av.VideoFrame.from_ndarray(
                np.zeros((size[1], size[0], 3), dtype=np.uint8), format="rgb24"
            )
            frame.pts = index
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


@given("two short local clips with different frame sizes")
def _two_clips(assembly_context: dict[str, Any]) -> None:
    first, second = (assembly_context["directory"] / name for name in ("a.mp4", "b.mp4"))
    _make_clip(first)
    _make_clip(second, size=(80, 60))
    assembly_context["clips"] = [first, second]


@given("one short local clip")
def _one_clip(assembly_context: dict[str, Any]) -> None:
    clip = assembly_context["directory"] / "source.mp4"
    _make_clip(clip)
    assembly_context["clips"] = [clip]


@when("I assemble the clips into one output file")
def _assemble(assembly_context: dict[str, Any]) -> None:
    output = assembly_context["directory"] / "result.mp4"
    args = [
        "video",
        "assemble",
        *(str(path) for path in assembly_context["clips"]),
        "--out-file",
        str(output),
        "--json",
    ]
    assembly_context["result"] = CliRunner().invoke(main, args)


@when("I assemble the clip over itself")
def _overwrite_source(assembly_context: dict[str, Any]) -> None:
    clip = assembly_context["clips"][0]
    assembly_context["source_bytes"] = clip.read_bytes()
    assembly_context["result"] = CliRunner().invoke(
        main, ["video", "assemble", str(clip), "--out-file", str(clip)]
    )


@then("assembly succeeds and reports two clips")
def _successful_result(assembly_context: dict[str, Any]) -> None:
    result = assembly_context["result"]
    assert result.exit_code == 0, result.output
    assert '"clips_count": 2' in result.output


@then("assembly fails without changing the source")
def _collision_result(assembly_context: dict[str, Any]) -> None:
    result = assembly_context["result"]
    assert result.exit_code != 0
    assert assembly_context["clips"][0].read_bytes() == assembly_context["source_bytes"]
