"""Smoke tests that run without auth or network."""

from __future__ import annotations

import subprocess
import sys


def test_help_exits_zero() -> None:
    """`gflow --help` should print and exit 0."""
    result = subprocess.run(
        [sys.executable, "-m", "flow_cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "gflow" in result.stdout.lower() or "usage" in result.stdout.lower()


def test_imports_succeed() -> None:
    """All public modules import without error."""
    import flow_cli  # noqa
    import flow_cli.auth  # noqa
    import flow_cli.cli  # noqa
    import flow_cli.api.client  # noqa
    import flow_cli.api.dto  # noqa

    assert flow_cli.__version__ == "0.2.0a1"
