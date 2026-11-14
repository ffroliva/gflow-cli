"""#1293: `video chain --out-file PATH` writes link i's clip to ``<stem>_<i><suffix>``.

The REAL ``run_chain`` runs; only the transport (``generate_video``), the frame
extractor and the two recorders are faked, so the move is seen by every consumer
of the clip path: the chain table, the videos catalog, the next link's seed
extraction and ``--json``.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner as _CliRunner

from gflow_cli._cli_helpers import _OUT_FILE_DEPRECATION_WARNING
from gflow_cli.cli import main


def _runner() -> _CliRunner:
    """stderr captured apart on every Click version pyproject allows (>=8.1)."""
    if "mix_stderr" in inspect.signature(_CliRunner.__init__).parameters:
        return _CliRunner(mix_stderr=False)  # type: ignore[call-arg]  # Click < 8.2
    return _CliRunner()


def _invoke_chain(
    tmp_path: Path, *, links: int, completed: int, args: list[str]
) -> tuple[Any, MagicMock, MagicMock, list[Path]]:
    """Run `gflow video chain` over ``links`` links, ``completed`` already recorded."""
    from gflow_cli.api.video import GenerateVideoRequest, VideoResult, VideoStatus
    from gflow_cli.chain import run_chain as real_run_chain

    manifest = tmp_path / "chain.jsonl"
    manifest.write_text(
        "\n".join(f'{{"prompt": "link {i}"}}' for i in range(links)), encoding="utf-8"
    )
    download_dir = tmp_path / "dl"
    download_dir.mkdir()
    media_ids = iter(f"m{i}" for i in range(links))

    async def _gen(*, req: GenerateVideoRequest, **_: Any) -> VideoResult:
        media_id = next(media_ids)
        clip = download_dir / f"{media_id}.mp4"
        clip.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        return VideoResult(
            status=VideoStatus(media_id=media_id, status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
            local_path=clip,
        )

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.generate_video = AsyncMock(side_effect=_gen)

    extracted_from: list[Path] = []

    def _extract(src: Path, dst: Path, *, offset_ms: int = 0) -> Path:
        assert src.exists(), f"seed extraction read a clip that is not on disk: {src}"
        extracted_from.append(src)
        dst.write_bytes(b"\xff\xd8\xff\xd9")
        return dst

    chain_recorder = MagicMock()
    chain_recorder.completed_links.return_value = [MagicMock()] * completed
    catalog = MagicMock()
    assert real_run_chain.__kwdefaults__ is not None
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path / "prof"),
        patch("gflow_cli.data.chain_repo.ChainLinkRecorder.open", return_value=chain_recorder),
        patch("gflow_cli.cli_video.OperationRecorder.open", return_value=catalog),
        patch("gflow_cli.cli_video.FlowApiClient", return_value=client),
        patch.dict(real_run_chain.__kwdefaults__, {"extractor": _extract}),
    ):
        result = _runner().invoke(
            main,
            [
                "video",
                "chain",
                str(manifest),
                "--yes",
                "--json",
                "--out-dir",
                str(download_dir),
                *args,
            ],
        )
    return result, chain_recorder, catalog, extracted_from


def _assert_written_and_reported(
    result: Any, chain_recorder: MagicMock, catalog: MagicMock, expected: list[Path]
) -> None:
    assert result.exit_code == 0, result.output
    for path in expected:
        assert path.is_file(), f"--out-file clip not written: {path}"
    payload = json.loads(result.stdout)
    assert [Path(link["local_path"]) for link in payload["links"]] == expected
    assert [Path(p) for p in payload["completed_paths"]] == expected
    assert [c.args[0].local_path for c in chain_recorder.record_chain_link.call_args_list] == (
        expected
    )
    assert [
        c.kwargs["result"].local_path for c in catalog.record_completed_video.call_args_list
    ] == expected


@pytest.mark.parametrize("flag", ["--out-file", "-o"])
def test_chain_out_file_writes_each_link_to_a_numbered_path(tmp_path: Path, flag: str) -> None:
    """Two links with ``--out-file out/clip.mp4`` land at clip_1.mp4 and clip_2.mp4."""
    target = tmp_path / "out" / "clip.mp4"
    result, chain_recorder, catalog, extracted_from = _invoke_chain(
        tmp_path, links=2, completed=0, args=[flag, str(target)]
    )

    expected = [target.parent / "clip_1.mp4", target.parent / "clip_2.mp4"]
    _assert_written_and_reported(result, chain_recorder, catalog, expected)
    assert extracted_from == [expected[0]]  # link 1 was seeded from the moved clip
    assert "does not apply --out-file" not in result.stderr
    if flag == "-o":
        assert result.stderr.strip() == _OUT_FILE_DEPRECATION_WARNING
    else:
        assert _OUT_FILE_DEPRECATION_WARNING not in result.stderr


def test_chain_out_file_resume_keeps_each_links_number(tmp_path: Path) -> None:
    """A resume of a 3-link chain with link 1 done writes clip_2 and clip_3, never clip_1."""
    target = tmp_path / "out" / "clip.mp4"
    result, chain_recorder, catalog, _ = _invoke_chain(
        tmp_path,
        links=3,
        completed=1,
        args=["--resume-from", "chain-abc", "--out-file", str(target)],
    )

    expected = [target.parent / "clip_2.mp4", target.parent / "clip_3.mp4"]
    _assert_written_and_reported(result, chain_recorder, catalog, expected)
    assert not (target.parent / "clip_1.mp4").exists()
