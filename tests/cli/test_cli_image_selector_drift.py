"""CLI tests for the ``UiSelectorDriftError`` surface (issue #183).

Proves the two user-visible guarantees the typed error exists for:

1. exit code 23 with the probe name in the output — instead of the old bare
   ``RuntimeError`` whose message was hashed by the unhandled-error handler,
   leaving the user with an opaque "Unexpected error" (exit 1);
2. the ``out_dir`` wiring on the image surface — the transport can only
   capture debug screenshots when ``FlowApiClient`` receives ``out_dir``,
   and it was silently ``None`` for every ``gflow image`` command.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner, Result

from gflow_cli.errors import UiSelectorDriftError


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _drifting_client() -> MagicMock:
    """Stub FlowApiClient whose generate_image hits selector drift."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.create_project = AsyncMock(
        return_value=MagicMock(project_id="proj-1", title="gflow-cli t2i")
    )
    client.generate_image = AsyncMock(
        side_effect=UiSelectorDriftError(
            "probe=mode_switch_trigger: no matching element found on the Flow editor."
        )
    )
    return client


def _invoke_t2i(runner: CliRunner, tmp_path: Path, out_dir: Path) -> tuple[Result, MagicMock]:
    client = _drifting_client()
    with (
        patch("gflow_cli.cli_image.FlowApiClient", return_value=client) as client_cls,
        patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
        patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
    ):
        from gflow_cli.cli import main

        result = runner.invoke(
            main,
            ["image", "t2i", "a cat", "--out", str(out_dir)],
            catch_exceptions=False,
        )
    return result, client_cls


def test_t2i_selector_drift_maps_exit_23_with_probe_name(runner: CliRunner, tmp_path: Path) -> None:
    result, _ = _invoke_t2i(runner, tmp_path, tmp_path / "out")
    assert result.exit_code == 23, result.output
    assert "mode_switch_trigger" in result.output
    # Regression guard for the issue-#183 root cause: the typed error's real
    # message must reach the user, not the hashed "Unexpected error" fallback.
    assert "Unexpected error" not in result.output


def test_t2i_wires_out_dir_into_client(runner: CliRunner, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    _, client_cls = _invoke_t2i(runner, tmp_path, out_dir)
    assert client_cls.call_args.kwargs["out_dir"] == out_dir
