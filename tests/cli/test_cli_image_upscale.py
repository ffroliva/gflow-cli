"""CLI tests for `gflow image upscale` (issue #171).

FlowApiClient is patched with a MagicMock so no browser/reCAPTCHA is needed.
Covers: happy path, --scale validation (1k hint + unknown), malformed mediaId
rejected before any client is built, and the 4K/Ultra exit-code-22 path.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from gflow_cli.api.image_upscale import TargetResolution
from gflow_cli.errors import UpscaleUnavailableError

_MEDIA_ID = "3a56bb5e-92a2-44f4-9992-3c6a9bf0cd14"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _mock_client(saved: Path) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.upsample_image = AsyncMock(return_value=saved)
    return client


def _invoke(
    runner: CliRunner,
    args: list[str],
    client: MagicMock | None = None,
    tmp_path: Path | None = None,
):
    from gflow_cli.cli import main

    ctxs = []
    if client is not None:
        ctxs = [
            patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
            patch(
                "gflow_cli.cli_image._make_provider_dir", return_value=(tmp_path or Path()) / "prof"
            ),
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        ]
    if not ctxs:
        return runner.invoke(main, args, catch_exceptions=False)
    with ctxs[0], ctxs[1], ctxs[2]:
        return runner.invoke(main, args, catch_exceptions=False)


def test_upscale_happy_2k(runner: CliRunner, tmp_path: Path) -> None:
    saved = tmp_path / "images" / "2026-06-11" / f"{_MEDIA_ID}_2k.png"
    client = _mock_client(saved)

    result = _invoke(runner, ["image", "upscale", _MEDIA_ID, "--scale", "2k"], client, tmp_path)

    assert result.exit_code == 0, result.output
    client.upsample_image.assert_awaited_once()
    kwargs = client.upsample_image.call_args.kwargs
    assert kwargs["media_id"] == _MEDIA_ID
    assert kwargs["target_resolution"] is TargetResolution.RES_2K
    assert kwargs["out_path"].name == f"{_MEDIA_ID}_2k.png"
    # The save confirmation is shown (the exact path may be wrapped by rich).
    assert "Saved" in result.output and _MEDIA_ID in result.output


def test_upscale_unknown_scale_rejected(runner: CliRunner) -> None:
    result = _invoke(runner, ["image", "upscale", _MEDIA_ID, "--scale", "8k"])
    assert result.exit_code == 2, result.output


def test_upscale_1k_rejected_with_hint(runner: CliRunner) -> None:
    result = _invoke(runner, ["image", "upscale", _MEDIA_ID, "--scale", "1k"])
    assert result.exit_code == 2, result.output
    assert "original" in result.output.lower()


def test_upscale_bad_media_id_rejected_before_client(runner: CliRunner, tmp_path: Path) -> None:
    # FlowApiClient must NEVER be constructed for a malformed mediaId.
    with patch("gflow_cli.cli_image.FlowApiClient") as fake_client:
        result = _invoke(runner, ["image", "upscale", "not-a-uuid", "--scale", "2k"])
    assert result.exit_code == 2, result.output
    fake_client.assert_not_called()


def test_upscale_4k_unavailable_maps_exit_22(runner: CliRunner, tmp_path: Path) -> None:
    client = _mock_client(tmp_path / "x.png")
    client.upsample_image = AsyncMock(
        side_effect=UpscaleUnavailableError(detail="4K requires Ultra", status=403)
    )

    result = _invoke(runner, ["image", "upscale", _MEDIA_ID, "--scale", "4k"], client, tmp_path)

    assert result.exit_code == 22, result.output
