"""CLI tests for `gflow video edit`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from gflow_cli.api.video import VideoResult, VideoStarted, VideoStatus
from gflow_cli.cli import main

_PID = "11111111-1111-1111-1111-111111111111"
_MID = "22222222-2222-2222-2222-222222222222"
_WF = "33333333-3333-3333-3333-333333333333"
_MP4_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


def test_video_edit_help_lists_options_and_no_count() -> None:
    res = CliRunner().invoke(main, ["video", "edit", "--help"])
    assert res.exit_code == 0, res.output
    assert "--ref" in res.output
    assert "--max-credits" in res.output
    assert "--resolution" in res.output
    assert "--count" not in res.output


def test_video_edit_rejects_more_than_five_refs(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(_MP4_BYTES)
    args = ["video", "edit", str(clip), "glow"]
    for i in range(6):
        img = tmp_path / f"r{i}.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")
        args.extend(["--ref", str(img)])
    res = CliRunner().invoke(main, args)
    assert res.exit_code == 2
    assert "at most 5" in res.output


def test_video_edit_rejects_missing_source_file(tmp_path: Path) -> None:
    res = CliRunner().invoke(main, ["video", "edit", str(tmp_path / "nope.mp4"), "glow"])
    assert res.exit_code == 2
    assert "SOURCE" in res.output


def test_video_edit_uuid_requires_project_when_uncatalogued(tmp_path: Path) -> None:
    with (
        patch("gflow_cli.cli_video_edit._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video_edit.default_project", return_value=None),
        patch("gflow_cli.cli_video_edit.lookup_project_in_catalog", return_value=None),
    ):
        res = CliRunner().invoke(main, ["video", "edit", _MID, "add snow"])
    assert res.exit_code == 2
    assert "--project" in res.output


def test_video_edit_json_and_out_file_and_no_max_credits(tmp_path: Path, monkeypatch: Any) -> None:
    clip = tmp_path / "in.mp4"
    downloaded = tmp_path / "dl.mp4"
    dest = tmp_path / "final.mp4"
    ref_img = tmp_path / "ref.png"
    clip.write_bytes(_MP4_BYTES)
    downloaded.write_bytes(_MP4_BYTES)
    ref_img.write_bytes(b"\x89PNG\r\n\x1a\n")

    captured: dict[str, Any] = {}

    class _FakeClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def edit_video(self, **kwargs: Any) -> VideoResult:
            captured.update(kwargs)
            if kwargs.get("on_started"):
                kwargs["on_started"](
                    VideoStarted(
                        media_id=_MID, project_id=kwargs["project_id"] or _PID, workflow_id=_WF
                    )
                )
            return VideoResult(
                status=VideoStatus(media_id=_MID, status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
                local_path=downloaded,
                project_id=kwargs["project_id"] or _PID,
                workflow_id=_WF,
                quoted_credits=9,
            )

    monkeypatch.setenv("GFLOW_CLI_MAX_CREDITS", "5")
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(tmp_path / "gflow.db"))
    from gflow_cli.config import reset_settings

    reset_settings()
    try:
        with (
            patch("gflow_cli.cli_video_edit._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_video_edit._make_provider_dir", return_value=tmp_path),
            patch("gflow_cli.cli_video_edit.default_project", return_value=_PID),
            patch("gflow_cli.cli_video_edit.FlowApiClient", _FakeClient),
        ):
            res = CliRunner().invoke(
                main,
                [
                    "video",
                    "edit",
                    str(clip),
                    "golden lantern glow",
                    "--ref",
                    str(ref_img),
                    "--resolution",
                    "720p",
                    "--no-max-credits",
                    "--out-file",
                    str(dest),
                    "--json",
                ],
            )
        assert res.exit_code == 0, res.output
        payload = json.loads(res.output)
        assert payload["status"] == "ok"
        assert payload["command"] == "video edit"
        assert payload["media_id"] == _MID
        assert payload["quoted_credits"] == 9
        assert payload["local_path"] == str(dest)
        assert dest.is_file()
        assert captured["req"].max_credits is None
        assert len(captured["req"].reference_images) == 1
    finally:
        reset_settings()
        AsyncMock()
