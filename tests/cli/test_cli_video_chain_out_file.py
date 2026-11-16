"""`video chain --out-file` (#1293) and `--resume-from` link numbering (#1300).

The REAL ``run_chain`` and the REAL ``ChainLinkRecorder`` run, on the per-test database
the autouse ``_isolate_settings`` fixture provides. Only the transport
(``generate_video``), the frame extractor and the videos catalog are faked, so every
consumer of a clip path is the real one: the chain table, the next link's seed and
``--json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog

from gflow_cli.api.video import GenerateVideoRequest, Mode, VideoResult, VideoStatus
from gflow_cli.cli_video import video
from tests.cli.test_out_file_deprecation import CliRunner

_KEPT_EVENT = "chain_clip_kept_at_download_path"


@pytest.fixture(autouse=True)
def _capture(install_log_capture: structlog.testing.LogCapture) -> structlog.testing.LogCapture:
    """Keep structlog off stdout so ``--json`` stays one parseable document."""
    return install_log_capture


class _Flow:
    """A fake Flow: one clip per successful link, a 403 for the prompts in ``fail``."""

    def __init__(self, tmp_path: Path) -> None:
        self.download_dir = tmp_path / "dl"
        self.download_dir.mkdir(exist_ok=True)
        self.fail: set[str] = set()
        self.calls: list[tuple[str, Mode, str | None, bool]] = []  # prompt, mode, seed, ok

    async def generate_video(self, *, req: GenerateVideoRequest, **_: Any) -> VideoResult:
        from gflow_cli.errors import WafRejectionError

        seed = req.start_image.read_text() if req.start_image is not None else None
        ok = req.prompt not in self.fail
        self.calls.append((req.prompt, req.mode, seed, ok))
        if not ok:
            raise WafRejectionError("403")
        media_id = "m" + req.prompt.removeprefix("link ")
        clip = self.download_dir / f"{media_id}.mp4"
        clip.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        return VideoResult(
            status=VideoStatus(media_id=media_id, status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
            local_path=clip,
        )


def _extract(src: Path, dst: Path, *, offset_ms: int = 0) -> Path:
    assert src.exists(), f"seed extraction read a clip that is not on disk: {src}"
    dst.write_text(src.name)  # the frame names the clip it came from
    return dst


def _invoke(tmp_path: Path, flow: _Flow, *, links: int, args: list[str]) -> tuple[Any, MagicMock]:
    """Run `gflow video chain` once over a ``links``-link manifest."""
    from gflow_cli.chain import run_chain as real_run_chain

    manifest = tmp_path / "chain.jsonl"
    manifest.write_text(
        "\n".join(f'{{"prompt": "link {i}"}}' for i in range(links)), encoding="utf-8"
    )
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.generate_video = AsyncMock(side_effect=flow.generate_video)
    catalog = MagicMock()
    assert real_run_chain.__kwdefaults__ is not None
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path / "prof"),
        patch("gflow_cli.cli_video.OperationRecorder.open", return_value=catalog),
        patch("gflow_cli.cli_video.FlowApiClient", return_value=client) as client_cls,
        patch.dict(real_run_chain.__kwdefaults__, {"extractor": _extract}),
    ):
        result = CliRunner().invoke(
            video,
            ["chain", str(manifest), "--yes", "--json", "--out-dir", str(flow.download_dir)] + args,
        )
    catalog.flow_client_cls = client_cls  # lets a test see whether a browser was opened
    return result, catalog


def _rows(tmp_path: Path, chain_id: str) -> list[tuple[int, Path]]:
    from gflow_cli.config import get_settings
    from gflow_cli.data.chain_repo import ChainLinkRecorder

    recorder = ChainLinkRecorder.open(
        get_settings(), profile_name="default", profile_dir=tmp_path / "prof", chain_id=chain_id
    )
    try:
        return [(r.link_index, Path(r.local_path)) for r in recorder.completed_links()]
    finally:
        recorder.close()


def _catalog_paths(catalog: MagicMock) -> list[Path]:
    return [c.kwargs["result"].local_path for c in catalog.record_completed_video.call_args_list]


def test_chain_out_file_writes_each_link_to_a_numbered_path(tmp_path: Path) -> None:
    """Two links with ``--out-file out/clip.mp4`` land at clip_1.mp4 and clip_2.mp4."""
    flow = _Flow(tmp_path)
    out = tmp_path / "out"
    result, catalog = _invoke(tmp_path, flow, links=2, args=["--out-file", str(out / "clip.mp4")])

    assert result.exit_code == 0, result.output
    expected = [out / "clip_1.mp4", out / "clip_2.mp4"]
    assert all(p.is_file() for p in expected), list(out.iterdir())
    payload = json.loads(result.stdout)
    assert [Path(link["local_path"]) for link in payload["links"]] == expected
    assert _rows(tmp_path, payload["chain_id"]) == list(enumerate(expected))
    assert _catalog_paths(catalog) == expected
    assert flow.calls[1][2] == "clip_1.mp4"  # link 2 was seeded from the moved clip
    assert "does not apply --out-file" not in result.stderr


def test_chain_dash_o_path_rejected_with_remediation(tmp_path: Path) -> None:
    """#1158: -o/--output selects format; passing a path is rejected with remediation."""
    flow = _Flow(tmp_path)
    out = tmp_path / "out"
    result, _ = _invoke(tmp_path, flow, links=2, args=["-o", str(out / "clip.mp4")])
    assert result.exit_code == 2
    assert "--output now selects the format; use --out-file" in result.output


@pytest.mark.sharpit(allow_n_plus_one=True)  # three runs of one chain, on purpose (#1300)
def test_resumes_keep_manifest_indexes_seed_i2v_and_bill_each_link_once(tmp_path: Path) -> None:
    """#1300: a 3-link chain stops at link 1, then at link 2, then completes."""
    flow = _Flow(tmp_path)
    out = tmp_path / "out"
    clips = [out / f"clip_{n}.mp4" for n in (1, 2, 3)]

    flow.fail = {"link 1"}
    first, _ = _invoke(tmp_path, flow, links=3, args=["--out-file", str(out / "clip.mp4")])
    assert first.exit_code == 21, first.output
    chain_id = json.loads(first.stdout)["chain_id"]
    assert _rows(tmp_path, chain_id) == [(0, clips[0])]

    flow.fail = {"link 2"}
    resume = ["--resume-from", chain_id, "--out-file", str(out / "clip.mp4")]
    second, _ = _invoke(tmp_path, flow, links=3, args=resume)
    assert second.exit_code == 21, second.output
    assert _rows(tmp_path, chain_id) == [(0, clips[0]), (1, clips[1])]

    flow.fail = set()
    third, _ = _invoke(tmp_path, flow, links=3, args=resume)
    assert third.exit_code == 0, third.output
    assert [link["index"] for link in json.loads(third.stdout)["links"]] == [2]
    assert _rows(tmp_path, chain_id) == list(enumerate(clips))
    assert all(p.is_file() for p in clips)

    assert flow.calls == [
        ("link 0", Mode.T2V, None, True),
        ("link 1", Mode.I2V, "clip_1.mp4", False),
        ("link 1", Mode.I2V, "clip_1.mp4", True),  # resumed: i2v, seeded from link 1's clip
        ("link 2", Mode.I2V, "clip_2.mp4", False),
        ("link 2", Mode.I2V, "clip_2.mp4", True),
    ]
    billed = [prompt for prompt, _, _, ok in flow.calls if ok]
    assert billed == ["link 0", "link 1", "link 2"]  # no link generated twice


def test_a_resume_whose_seed_clip_is_gone_is_refused_before_submit(tmp_path: Path) -> None:
    """#1300: with the last paid clip missing, the next link cannot be seeded; never t2v."""
    from gflow_cli.chain import ChainLinkResult
    from gflow_cli.config import get_settings
    from gflow_cli.data.chain_repo import ChainLinkRecorder

    missing = tmp_path / "dl" / "m0.mp4"
    recorder = ChainLinkRecorder.open(
        get_settings(), profile_name="default", profile_dir=tmp_path / "prof", chain_id="c1"
    )
    recorder.record_chain_link(
        ChainLinkResult(index=0, prompt="link 0", local_path=missing, media_id="m0")
    )
    recorder.close()
    flow = _Flow(tmp_path)

    result, catalog = _invoke(tmp_path, flow, links=2, args=["--resume-from", "c1"])

    assert result.exit_code == 20, result.output
    assert str(missing) in json.loads(result.stdout)["error"]["detail"]
    assert flow.calls == []
    catalog.flow_client_cls.assert_not_called()


def test_an_existing_target_is_never_overwritten(
    tmp_path: Path, _capture: structlog.testing.LogCapture
) -> None:
    """A second run with the same --out-file keeps the old files and its own clips."""
    flow = _Flow(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "clip_1.mp4").write_bytes(b"earlier run")
    (out / "clip_2.mp4").mkdir()

    result, catalog = _invoke(tmp_path, flow, links=2, args=["--out-file", str(out / "clip.mp4")])

    assert result.exit_code == 0, result.output
    assert (out / "clip_1.mp4").read_bytes() == b"earlier run"
    kept = [flow.download_dir / "m0.mp4", flow.download_dir / "m1.mp4"]
    assert all(p.is_file() for p in kept)
    payload = json.loads(result.stdout)
    assert [Path(link["local_path"]) for link in payload["links"]] == kept
    assert _rows(tmp_path, payload["chain_id"]) == list(enumerate(kept))
    assert _catalog_paths(catalog) == kept
    warnings = [e for e in _capture.entries if e["event"] == _KEPT_EVENT]
    assert [(w["link"], w["media_id"], w["kept_at"]) for w in warnings] == [
        (1, "m0", str(kept[0])),
        (2, "m1", str(kept[1])),
    ]


def test_a_failed_move_keeps_the_clip_and_removes_its_partial_copy(
    tmp_path: Path, _capture: structlog.testing.LogCapture
) -> None:
    """A move that dies mid-copy leaves no half file, and the paid clip stays recorded."""
    import shutil

    real_move = shutil.move
    calls = 0

    def _move(src: Any, dst: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            Path(dst).write_bytes(b"half")
            raise OSError(28, "No space left on device")
        return real_move(src, dst)

    flow = _Flow(tmp_path)
    out = tmp_path / "out"
    with patch("shutil.move", _move):
        result, catalog = _invoke(
            tmp_path, flow, links=2, args=["--out-file", str(out / "clip.mp4")]
        )

    assert result.exit_code == 0, result.output
    assert not (out / "clip_2.mp4").exists()
    expected = [out / "clip_1.mp4", flow.download_dir / "m1.mp4"]
    payload = json.loads(result.stdout)
    assert _rows(tmp_path, payload["chain_id"]) == list(enumerate(expected))
    assert _catalog_paths(catalog) == expected
    warnings = [e for e in _capture.entries if e["event"] == _KEPT_EVENT]
    assert [(w["link"], w["media_id"], w["kept_at"]) for w in warnings] == [
        (2, "m1", str(expected[1]))
    ]


@pytest.mark.parametrize("gate", ["--yes", "--dry-run"])
def test_an_out_file_under_a_file_is_a_usage_error_before_anything_runs(
    tmp_path: Path, gate: str
) -> None:
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    manifest = tmp_path / "chain.jsonl"
    manifest.write_text('{"prompt": "a"}\n{"prompt": "b"}\n', encoding="utf-8")

    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path / "prof"),
        patch("gflow_cli.cli_video.FlowApiClient") as client_cls,
    ):
        result = CliRunner().invoke(
            video, ["chain", str(manifest), gate, "--out-file", str(blocker / "clip.mp4")]
        )

    assert result.exit_code == 2, result.output
    assert "--out-file" in result.stderr
    assert str(blocker) in result.stderr
    client_cls.assert_not_called()
