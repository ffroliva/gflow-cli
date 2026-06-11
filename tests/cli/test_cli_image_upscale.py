"""CLI tests for `gflow image upscale` (issue #171).

FlowApiClient and the catalog lookup are patched so no browser/DB is touched.
Covers: explicit --project happy path, catalog-resolved projectId, fail-fast
when the project can't be resolved, --scale validation (1k hint + unknown),
malformed mediaId rejected before any client is built, and the 4K exit-22 path.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from gflow_cli.api.image_upscale import TargetResolution
from gflow_cli.errors import UpscaleUnavailableError

_MEDIA_ID = "3a56bb5e-92a2-44f4-9992-3c6a9bf0cd14"
_PROJECT_ID = "ffb768fb-cf2d-48b7-a135-92978667c37d"


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
    *,
    client: MagicMock | None = None,
    tmp_path: Path | None = None,
    catalog_project: str | None = None,
):
    """Invoke the CLI with the browser + catalog patched out.

    ``catalog_project`` is what ``_lookup_project_in_catalog`` returns (None =
    not recorded). FlowApiClient is patched only when ``client`` is given.
    """
    from gflow_cli.cli import main

    with ExitStack() as stack:
        # Always stub the environment shims so tests never touch the real
        # profile store / catalog DB.
        stack.enter_context(
            patch("gflow_cli.cli_image._lookup_project_in_catalog", return_value=catalog_project)
        )
        stack.enter_context(patch("gflow_cli.cli_image._resolve_profile", return_value="default"))
        stack.enter_context(
            patch(
                "gflow_cli.cli_image._make_provider_dir",
                return_value=(tmp_path or Path()) / "prof",
            )
        )
        if client is not None:
            stack.enter_context(patch("gflow_cli.cli_image.FlowApiClient", return_value=client))
        return runner.invoke(main, args, catch_exceptions=False)


def test_upscale_happy_explicit_project(runner: CliRunner, tmp_path: Path) -> None:
    client = _mock_client(tmp_path / f"{_MEDIA_ID}_2k.png")
    result = _invoke(
        runner,
        ["image", "upscale", _MEDIA_ID, "--scale", "2k", "--project", _PROJECT_ID],
        client=client,
        tmp_path=tmp_path,
    )
    assert result.exit_code == 0, result.output
    kwargs = client.upsample_image.call_args.kwargs
    assert kwargs["media_id"] == _MEDIA_ID
    assert kwargs["project_id"] == _PROJECT_ID
    assert kwargs["target_resolution"] is TargetResolution.RES_2K
    assert "Saved" in result.output and _MEDIA_ID in result.output


def test_upscale_resolves_project_from_catalog(runner: CliRunner, tmp_path: Path) -> None:
    client = _mock_client(tmp_path / f"{_MEDIA_ID}_2k.png")
    result = _invoke(
        runner,
        ["image", "upscale", _MEDIA_ID, "--scale", "2k"],
        client=client,
        tmp_path=tmp_path,
        catalog_project=_PROJECT_ID,
    )
    assert result.exit_code == 0, result.output
    assert client.upsample_image.call_args.kwargs["project_id"] == _PROJECT_ID


def test_upscale_fails_fast_when_project_unresolvable(runner: CliRunner) -> None:
    # No --project and not in the catalog -> exit 2, browser never launched.
    with patch("gflow_cli.cli_image.FlowApiClient") as fake_client:
        result = _invoke(
            runner, ["image", "upscale", _MEDIA_ID, "--scale", "2k"], catalog_project=None
        )
    assert result.exit_code == 2, result.output
    assert "--project" in result.output
    fake_client.assert_not_called()


def test_upscale_unknown_scale_rejected(runner: CliRunner) -> None:
    result = _invoke(runner, ["image", "upscale", _MEDIA_ID, "--scale", "8k"])
    assert result.exit_code == 2, result.output


def test_upscale_1k_rejected_with_hint(runner: CliRunner) -> None:
    result = _invoke(runner, ["image", "upscale", _MEDIA_ID, "--scale", "1k"])
    assert result.exit_code == 2, result.output
    assert "original" in result.output.lower()


def test_upscale_bad_media_id_rejected_before_client(runner: CliRunner) -> None:
    with patch("gflow_cli.cli_image.FlowApiClient") as fake_client:
        result = _invoke(runner, ["image", "upscale", "not-a-uuid", "--scale", "2k"])
    assert result.exit_code == 2, result.output
    fake_client.assert_not_called()


def test_upscale_4k_unavailable_maps_exit_22(runner: CliRunner, tmp_path: Path) -> None:
    client = _mock_client(tmp_path / "x.png")
    client.upsample_image = AsyncMock(
        side_effect=UpscaleUnavailableError(detail="4K requires Ultra", status=403)
    )
    result = _invoke(
        runner,
        ["image", "upscale", _MEDIA_ID, "--scale", "4k", "--project", _PROJECT_ID],
        client=client,
        tmp_path=tmp_path,
    )
    assert result.exit_code == 22, result.output
