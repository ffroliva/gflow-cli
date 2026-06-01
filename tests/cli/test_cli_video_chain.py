"""Click-runner tests for `gflow video chain` (Task 8).

These pin the CLI flag/gate behavior with NO network and NO credits: the
orchestrator (`run_chain`) and the data recorder are mocked, so the cost gate,
`--dry-run`, `--max-links`, model validation, and `ChainPartialError` -> exit 21
are exercised at the Click layer in isolation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from gflow_cli.cli_video import video


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


def test_chain_dry_run_spends_nothing_and_prints_cost(tmp_path: Path) -> None:
    runner = CliRunner()
    manifest = _manifest(tmp_path, 3)
    p_resolve, p_provider, p_rec, _ = _patches(tmp_path)
    with (
        p_resolve,
        p_provider,
        p_rec,
        patch("gflow_cli.chain.run_chain", new_callable=AsyncMock) as mock_run,
        patch("gflow_cli.api.client.FlowApiClient.__init__") as mock_client_init,
    ):
        result = runner.invoke(video, ["chain", str(manifest), "--dry-run"])

    assert result.exit_code == 0, result.output
    # Plan + credit estimate printed, but nothing generated and no client built.
    assert "3 credit(s)" in result.output
    assert "no credits spent" in result.output
    mock_run.assert_not_awaited()
    mock_client_init.assert_not_called()


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
    import json

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
