"""Click-runner tests for `gflow video chain` (Task 8).

These pin the CLI flag/gate behavior with NO network and NO credits: the
orchestrator (`run_chain`) and the data recorder are mocked, so the cost gate,
`--dry-run`, `--max-links`, model validation, and `ChainPartialError` -> exit 21
are exercised at the Click layer in isolation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog
from click.testing import CliRunner

from gflow_cli.cli_video import video


@pytest.fixture(autouse=True)
def _route_logs_to_capture(
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """Route structlog into the shared LogCapture (not stdout) for every test here.

    Without it, the warning-level ``chain_link_failed`` event the CLI emits on the
    ``--json`` partial path renders to stdout and breaks the "single parseable JSON
    document" assertion when this file runs in isolation. It only passed before
    because an earlier test in a broader run happened to configure capture first —
    a test-order dependency, now made deterministic.
    """


def _manifest(tmp_path: Path, n: int) -> Path:
    lines = [f'{{"prompt": "link {i}"}}' for i in range(n)]
    p = tmp_path / "chain.jsonl"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _fake_link_result(index: int, tmp_path: Path) -> Any:
    from gflow_cli.chain import ChainLinkResult

    clip = tmp_path / f"link{index}.mp4"
    clip.touch()
    return ChainLinkResult(
        index=index,
        prompt=f"link {index}",
        local_path=clip,
        media_id=f"media-{index}",
    )


def _assert_no_numeric_credit_claim(output: str) -> None:
    numeric_credit_lines = [
        line
        for line in output.splitlines()
        if any(char.isdigit() for char in line)
        and re.search(r"\bcredit(?:s|\(s\))?", line, re.IGNORECASE)
    ]
    assert not numeric_credit_lines, f"numeric credit claim(s): {numeric_credit_lines}"


def _patches(tmp_path: Path):
    """Common patches: profile resolution + a fake recorder (no DB)."""
    fake_recorder = MagicMock()
    fake_recorder.completed_links.return_value = []
    return (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch(
            "gflow_cli.data.chain_repo.ChainLinkRecorder.open",
            return_value=fake_recorder,
        ),
        fake_recorder,
    )


def test_chain_requires_manifest() -> None:
    runner = CliRunner()
    result = runner.invoke(video, ["chain"])
    assert result.exit_code != 0


def test_chain_missing_manifest_file_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    p_resolve, p_provider, p_rec, _ = _patches(tmp_path)
    missing = tmp_path / "nope.jsonl"
    with p_resolve, p_provider, p_rec:
        result = runner.invoke(video, ["chain", str(missing), "--yes"])
    assert result.exit_code != 0


def test_chain_dry_run_reports_pending_operations_and_variable_cost(tmp_path: Path) -> None:
    runner = CliRunner()
    manifest = _manifest(tmp_path, 3)
    p_resolve, p_provider, p_rec, _ = _patches(tmp_path)
    with (
        p_resolve,
        p_provider,
        p_rec,
        patch("gflow_cli.cli_video._apply_tools_to_chain_links") as mock_apply_tools,
        patch("gflow_cli.chain.run_chain", new_callable=AsyncMock) as mock_run,
        patch("gflow_cli.api.client.FlowApiClient.__init__") as mock_client_init,
    ):
        result = runner.invoke(
            video,
            ["chain", str(manifest), "--tool", "rewrite", "--dry-run"],
        )

    assert result.exit_code == 0, result.output
    mock_apply_tools.assert_not_called()
    mock_run.assert_not_awaited()
    mock_client_init.assert_not_called()
    assert "3 pending video operations" in result.output
    assert "current cost" in result.output.lower()
    assert "Flow" in result.output
    assert all(
        term in result.output.lower()
        for term in ("varies", "model", "duration", "account tier", "flow policy")
    )
    assert "Estimated credits" not in result.output
    _assert_no_numeric_credit_claim(result.output)
    assert "one per link" not in result.output.lower()


def test_chain_resumed_dry_run_counts_only_remaining_operations(tmp_path: Path) -> None:
    runner = CliRunner()
    manifest = _manifest(tmp_path, 3)
    fake_recorder = MagicMock()
    fake_recorder.completed_links.return_value = [MagicMock(), MagicMock()]

    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch(
            "gflow_cli.data.chain_repo.ChainLinkRecorder.open",
            return_value=fake_recorder,
        ),
        patch("gflow_cli.chain.run_chain", new_callable=AsyncMock) as mock_run,
        patch("gflow_cli.api.client.FlowApiClient.__init__") as mock_client_init,
    ):
        result = runner.invoke(
            video,
            ["chain", str(manifest), "--resume-from", "chain-abc", "--dry-run"],
        )

    assert result.exit_code == 0, result.output
    assert "2 already completed" in result.output
    assert "1 pending video operation" in result.output
    _assert_no_numeric_credit_claim(result.output)
    assert "one per link" not in result.output.lower()
    mock_run.assert_not_awaited()
    mock_client_init.assert_not_called()


def test_chain_resume_with_all_links_completed_returns_before_external_work(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    manifest = _manifest(tmp_path, 3)
    fake_recorder = MagicMock()
    fake_recorder.completed_links.return_value = [MagicMock(), MagicMock(), MagicMock()]

    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch(
            "gflow_cli.data.chain_repo.ChainLinkRecorder.open",
            return_value=fake_recorder,
        ),
        patch("gflow_cli.cli_video.click.confirm") as mock_confirm,
        patch("gflow_cli.cli_video._apply_tools_to_chain_links") as mock_apply_tools,
        patch("gflow_cli.cli_video.FlowApiClient") as mock_client,
        patch("gflow_cli.chain.run_chain", new_callable=AsyncMock) as mock_run,
    ):
        result = runner.invoke(
            video,
            ["chain", str(manifest), "--resume-from", "chain-abc", "--tool", "rewrite"],
        )

    assert result.exit_code == 0, result.output
    assert "already complete" in result.output.lower()
    mock_confirm.assert_not_called()
    mock_apply_tools.assert_not_called()
    mock_client.assert_not_called()
    mock_run.assert_not_awaited()


def test_chain_declined_confirmation_performs_no_external_work(tmp_path: Path) -> None:
    runner = CliRunner()
    manifest = _manifest(tmp_path, 2)
    p_resolve, p_provider, p_rec, _ = _patches(tmp_path)

    with (
        p_resolve,
        p_provider,
        p_rec,
        patch("gflow_cli.cli_video._apply_tools_to_chain_links") as mock_apply_tools,
        patch("gflow_cli.cli_video.FlowApiClient") as mock_client,
        patch("gflow_cli.chain.run_chain", new_callable=AsyncMock) as mock_run,
    ):
        result = runner.invoke(
            video,
            ["chain", str(manifest), "--tool", "rewrite"],
            input="n\n",
        )

    assert result.exit_code == 130, result.output
    mock_apply_tools.assert_not_called()
    mock_client.assert_not_called()
    mock_run.assert_not_awaited()
    prompt_line = next(line for line in result.output.splitlines() if "[y/N]" in line)
    assert "2 pending video operations" in prompt_line
    _assert_no_numeric_credit_claim(result.output)
    assert "one per link" not in result.output.lower()


def test_chain_max_links_rejects_overlong_manifest(tmp_path: Path) -> None:
    runner = CliRunner()
    manifest = _manifest(tmp_path, 5)
    p_resolve, p_provider, p_rec, _ = _patches(tmp_path)
    with (
        p_resolve,
        p_provider,
        p_rec,
        patch("gflow_cli.chain.run_chain", new_callable=AsyncMock) as mock_run,
        patch("gflow_cli.api.client.FlowApiClient.__init__") as mock_client_init,
    ):
        result = runner.invoke(video, ["chain", str(manifest), "--max-links", "3", "--yes"])

    # ChainManifestError -> ConfigurationError exit code 11.
    assert result.exit_code == 11, result.output
    mock_run.assert_not_awaited()
    mock_client_init.assert_not_called()


def test_chain_rejects_non_interpolation_model(tmp_path: Path) -> None:
    runner = CliRunner()
    manifest = _manifest(tmp_path, 2)
    p_resolve, p_provider, p_rec, _ = _patches(tmp_path)
    with p_resolve, p_provider, p_rec:
        # omni-flash is not in the Choice -> Click usage error (exit 2).
        result = runner.invoke(video, ["chain", str(manifest), "--model", "omni-flash", "--yes"])
    assert result.exit_code == 2
    assert "omni-flash" in result.output


def test_chain_happy_path_calls_run_chain_with_links_and_recorder(tmp_path: Path) -> None:
    runner = CliRunner()
    manifest = _manifest(tmp_path, 2)
    p_resolve, p_provider, p_rec, fake_recorder = _patches(tmp_path)
    results = [_fake_link_result(0, tmp_path), _fake_link_result(1, tmp_path)]

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    with (
        p_resolve,
        p_provider,
        p_rec,
        patch("gflow_cli.cli_video.FlowApiClient", return_value=fake_client),
        patch(
            "gflow_cli.chain.run_chain", new_callable=AsyncMock, return_value=results
        ) as mock_run,
    ):
        result = runner.invoke(video, ["chain", str(manifest), "--yes"])

    assert result.exit_code == 0, result.output
    mock_run.assert_awaited_once()
    kwargs = mock_run.await_args.kwargs
    assert list(kwargs["links"]) and len(kwargs["links"]) == 2
    assert kwargs["recorder"] is fake_recorder
    # Model defaulted to veo-lite and resolved to the interpolation-capable enum.
    from gflow_cli.api.video import VideoModel

    assert kwargs["model"] is VideoModel.VEO_3_1_LITE


def test_chain_yes_json_preserves_success_schema_and_bypasses_prompt(tmp_path: Path) -> None:
    runner = CliRunner()
    manifest = _manifest(tmp_path, 2)
    p_resolve, p_provider, p_rec, _ = _patches(tmp_path)
    results = [_fake_link_result(0, tmp_path), _fake_link_result(1, tmp_path)]

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    with (
        p_resolve,
        p_provider,
        p_rec,
        patch("gflow_cli.cli_video.FlowApiClient", return_value=fake_client),
        patch("gflow_cli.cli_video.click.confirm") as mock_confirm,
        patch("gflow_cli.chain.run_chain", new_callable=AsyncMock, return_value=results),
    ):
        result = runner.invoke(video, ["chain", str(manifest), "--yes", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert set(payload) == {
        "status",
        "command",
        "chain_id",
        "partial",
        "links",
        "completed_paths",
    }
    assert payload["status"] == "ok"
    assert payload["command"] == "video chain"
    assert payload["partial"] is False
    assert payload["links"] == [
        {
            "index": result_item.index,
            "media_id": result_item.media_id,
            "local_path": str(result_item.local_path),
        }
        for result_item in results
    ]
    assert payload["completed_paths"] == [str(result_item.local_path) for result_item in results]
    mock_confirm.assert_not_called()


def test_chain_wires_operation_recorder_and_records_each_link(tmp_path: Path) -> None:
    """The chain command opens an OperationRecorder and records EVERY link into
    the `videos` catalog (parity with t2v/i2v): record_started_video (forwarded
    as on_started into the transport) + record_completed_video per link, each
    with that link's own request (link 0 T2V, link 1 I2V).

    Uses the REAL run_chain so the per-link hooks actually fire; only the
    transport (FlowApiClient.generate_video) is mocked, so no network/credits.
    """
    from gflow_cli.api.video import (
        GenerateVideoRequest,
        Mode,
        VideoResult,
        VideoStarted,
        VideoStatus,
    )

    runner = CliRunner()
    manifest = _manifest(tmp_path, 2)
    p_resolve, p_provider, p_rec, _ = _patches(tmp_path)

    # Mock OperationRecorder (the catalog recorder).
    op_recorder = MagicMock()

    # Mock the transport client: generate_video downloads a clip + invokes the
    # forwarded on_started, then returns a successful VideoResult per link.
    media_ids = iter(["m0", "m1"])

    async def _gen(*, req: GenerateVideoRequest, on_started: Any = None, **_: Any) -> VideoResult:
        media_id = next(media_ids)
        clip = tmp_path / f"{media_id}.mp4"
        clip.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        if on_started is not None:
            on_started(VideoStarted(media_id=media_id, project_id="proj", flow_operation_id="op"))
        return VideoResult(
            status=VideoStatus(media_id=media_id, status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
            local_path=clip,
            project_id="proj",
            flow_operation_id="op",
        )

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.generate_video = AsyncMock(side_effect=_gen)

    # Avoid real frame extraction (no ffmpeg / real mp4): stub the extractor.
    # The CLI does not pass `extractor=`, so run_chain falls back to its
    # def-time default kwdefault — patch that binding directly.
    from gflow_cli.chain import run_chain as _real_run_chain

    def _fake_extract(src: Path, dst: Path, *, offset_ms: int = 0) -> Path:
        dst.write_bytes(b"\xff\xd8\xff\xd9")
        return dst

    assert _real_run_chain.__kwdefaults__ is not None
    with (
        p_resolve,
        p_provider,
        p_rec,
        patch("gflow_cli.cli_video.FlowApiClient", return_value=fake_client),
        patch("gflow_cli.cli_video.OperationRecorder.open", return_value=op_recorder),
        patch.dict(_real_run_chain.__kwdefaults__, {"extractor": _fake_extract}),
    ):
        result = runner.invoke(video, ["chain", str(manifest), "--yes"])

    assert result.exit_code == 0, result.output

    # An OperationRecorder was opened and closed.
    op_recorder.close.assert_called()

    # record_started_video fired once per link, each with the link's own request.
    assert op_recorder.record_started_video.call_count == 2
    started_modes = [
        c.kwargs["request"].mode for c in op_recorder.record_started_video.call_args_list
    ]
    assert started_modes == [Mode.T2V, Mode.I2V]

    # record_completed_video fired once per link, each with the link's own request.
    assert op_recorder.record_completed_video.call_count == 2
    completed_modes = [
        c.kwargs["request"].mode for c in op_recorder.record_completed_video.call_args_list
    ]
    assert completed_modes == [Mode.T2V, Mode.I2V]
    # The completed result carries the link's downloaded clip.
    completed_media = [
        c.kwargs["result"].status.media_id
        for c in op_recorder.record_completed_video.call_args_list
    ]
    assert completed_media == ["m0", "m1"]


def test_chain_partial_error_exits_21_with_resume_hint(tmp_path: Path) -> None:
    runner = CliRunner()
    manifest = _manifest(tmp_path, 3)
    p_resolve, p_provider, p_rec, _ = _patches(tmp_path)

    from gflow_cli.errors import ChainPartialError

    done_clip = tmp_path / "link0.mp4"
    done_clip.touch()

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    async def _boom(**_kwargs: Any) -> Any:
        raise ChainPartialError(detail="aborted at link 1", partial_results=[done_clip])

    with (
        p_resolve,
        p_provider,
        p_rec,
        patch("gflow_cli.cli_video.FlowApiClient", return_value=fake_client),
        patch("gflow_cli.chain.run_chain", side_effect=_boom),
    ):
        result = runner.invoke(video, ["chain", str(manifest), "--yes"])

    assert result.exit_code == 21, result.output
    # The remediation hint mentions --resume-from.
    assert "resume" in result.output.lower()


def test_chain_resume_skips_completed_links(tmp_path: Path) -> None:
    runner = CliRunner()
    manifest = _manifest(tmp_path, 3)

    # Recorder reports the first link already paid for.
    fake_recorder = MagicMock()
    fake_recorder.completed_links.return_value = [MagicMock()]  # 1 completed

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    results = [_fake_link_result(0, tmp_path), _fake_link_result(1, tmp_path)]

    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch(
            "gflow_cli.data.chain_repo.ChainLinkRecorder.open",
            return_value=fake_recorder,
        ),
        patch("gflow_cli.cli_video.FlowApiClient", return_value=fake_client),
        patch(
            "gflow_cli.chain.run_chain", new_callable=AsyncMock, return_value=results
        ) as mock_run,
    ):
        result = runner.invoke(
            video, ["chain", str(manifest), "--resume-from", "chain-abc", "--yes"]
        )

    assert result.exit_code == 0, result.output
    mock_run.assert_awaited_once()
    # Only the 2 remaining links are submitted; the paid link is skipped.
    assert len(mock_run.await_args.kwargs["links"]) == 2


def test_chain_partial_json_emits_single_parseable_document(tmp_path: Path) -> None:
    """--json + ChainPartialError must emit exactly ONE chain-shaped JSON doc.

    Re-raising through the shared handler would emit a second (error-shaped)
    document, leaving stdout unparseable.
    """
    runner = CliRunner()
    manifest = _manifest(tmp_path, 3)
    p_resolve, p_provider, p_rec, _ = _patches(tmp_path)

    from gflow_cli.errors import ChainPartialError

    done_clip = tmp_path / "link0.mp4"
    done_clip.touch()

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    async def _boom(**_kwargs: Any) -> Any:
        raise ChainPartialError(detail="aborted at link 1", partial_results=[done_clip])

    with (
        p_resolve,
        p_provider,
        p_rec,
        patch("gflow_cli.cli_video.FlowApiClient", return_value=fake_client),
        patch("gflow_cli.chain.run_chain", side_effect=_boom),
    ):
        result = runner.invoke(video, ["chain", str(manifest), "--yes", "--json"])

    assert result.exit_code == 21, result.output
    payload = json.loads(result.output)  # single parseable document
    assert payload["status"] == "fail"
    assert payload["partial"] is True
    assert payload["completed_paths"] == [str(done_clip)]


def test_chain_help_states_cost_and_scene_followup() -> None:
    runner = CliRunner()
    result = runner.invoke(video, ["chain", "--help"])
    assert result.exit_code == 0
    out = result.output.lower()
    assert "credit" in out
    assert "gflow scene" in out
    assert "duration" in out
    assert "4" in out and "6" in out and "8" in out
    assert "omni-flash" in out or "omni_flash" in out
    assert "10" in out
    assert "duration is rejected" not in out


def _manifest_raw(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "chain.jsonl"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def test_chain_dry_run_rejects_a_manifest_duration(tmp_path: Path) -> None:
    """#634: --dry-run must refuse what the real run refuses.

    run_chain's own guard sits behind the --dry-run short-circuit, so without the
    CLI-level call the documented pre-flight command exits 0 on a manifest that
    the real run rejects — green-lighting the crash it exists to prevent.
    """
    runner = CliRunner()
    manifest = _manifest_raw(
        tmp_path,
        ['{"prompt": "link 0"}', '{"prompt": "link 1", "duration": 10}'],
    )
    p_resolve, p_provider, p_rec, _ = _patches(tmp_path)
    with (
        p_resolve,
        p_provider,
        p_rec,
        patch("gflow_cli.chain.run_chain", new_callable=AsyncMock) as mock_run,
        patch("gflow_cli.api.client.FlowApiClient.__init__") as mock_client_init,
    ):
        result = runner.invoke(video, ["chain", str(manifest), "--dry-run"])

    assert result.exit_code != 0, result.output
    assert "duration" in result.output.lower()
    assert "omni_flash" in result.output.lower()
    mock_run.assert_not_awaited()
    mock_client_init.assert_not_called()


def test_chain_dry_run_rejects_a_per_link_omni_flash_override(tmp_path: Path) -> None:
    """#634: the per-link model override bypassed the chain-level omni check."""
    runner = CliRunner()
    manifest = _manifest_raw(
        tmp_path,
        ['{"prompt": "link 0"}', '{"prompt": "link 1", "model": "omni-flash"}'],
    )
    p_resolve, p_provider, p_rec, _ = _patches(tmp_path)
    with (
        p_resolve,
        p_provider,
        p_rec,
        patch("gflow_cli.chain.run_chain", new_callable=AsyncMock) as mock_run,
        patch("gflow_cli.api.client.FlowApiClient.__init__") as mock_client_init,
    ):
        result = runner.invoke(video, ["chain", str(manifest), "--dry-run"])

    assert result.exit_code != 0, result.output
    assert "omni" in result.output.lower()
    mock_run.assert_not_awaited()
    mock_client_init.assert_not_called()
