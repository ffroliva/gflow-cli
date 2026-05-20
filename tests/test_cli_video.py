"""`gflow video` is stubbed in Phase A — these tests pin the stub behavior.

Phase B reintroduces real `video` command tests once `cli_video.py` is rewired
to the UI-automation transport.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from gflow_cli.cli_video import video


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestVideoStub:
    @pytest.fixture(autouse=True)
    def _patch_profile(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Bypass profile resolution so the stub reaches `run_with_handlers`
        in environments without a configured profile (e.g. CI). Mirrors the
        helper used by `tests/cli/test_error_handling.py`."""
        monkeypatch.setattr("gflow_cli.cli_video._resolve_profile", lambda profile: "test")
        monkeypatch.setattr("gflow_cli.cli_video._make_provider_dir", lambda name: tmp_path)

    def test_t2v_reports_unavailable(self, runner: CliRunner) -> None:
        result = runner.invoke(video, ["t2v", "a prompt"])
        assert result.exit_code == 1
        assert "temporarily unavailable" in result.output

    def test_i2v_reports_unavailable(self, runner: CliRunner) -> None:
        result = runner.invoke(video, ["i2v", "img.png", "a prompt"])
        assert result.exit_code == 1
        assert "temporarily unavailable" in result.output

    def test_batch_reports_unavailable(self, runner: CliRunner) -> None:
        result = runner.invoke(video, ["batch", "manifest.tsv"])
        assert result.exit_code == 1
        assert "temporarily unavailable" in result.output
