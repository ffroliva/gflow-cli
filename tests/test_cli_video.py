"""`gflow video` is stubbed in Phase A — these tests pin the stub behavior.

Phase B reintroduces real `video` command tests once `cli_video.py` is rewired
to the UI-automation transport.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from gflow_cli.cli_video import video


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestVideoStub:
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
