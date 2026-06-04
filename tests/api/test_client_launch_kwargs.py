"""Pins the ``FlowApiClient._persistent_context_kwargs()`` seam.

The seam was extracted from the inline ``launch_persistent_context(...)`` call so
a dev-scoped recording subclass can augment the launch without any recording
concern living in core (see ``scripts/dev/_recording_client.py``). This test
proves the extraction is value-for-value behavior-preserving and keeps the
seam's contract stable.
"""

from __future__ import annotations

from pathlib import Path

from gflow_cli.api.client import FlowApiClient


def test_persistent_context_kwargs_are_unchanged(tmp_path: Path) -> None:
    """The seam returns exactly the kwargs the client launched with before the
    refactor — proving the extraction changed no behavior."""
    client = FlowApiClient(profile_dir=tmp_path, headless=True)
    kwargs = client._persistent_context_kwargs()  # noqa: SLF001
    assert kwargs["user_data_dir"] == str(tmp_path)
    assert kwargs["headless"] is True
    assert kwargs["viewport"] == {"width": 1280, "height": 720}
    assert kwargs["locale"] == "en-US"
    assert kwargs["extra_http_headers"] == {"Accept-Language": "en-US,en;q=0.9"}
    assert kwargs["ignore_default_args"] == [
        "--enable-automation",
        "--no-sandbox",
        "--password-store=basic",
    ]
    assert kwargs["args"] == ["--disable-blink-features=AutomationControlled"]
    # channel is profile-derived; a marker-less tmp_path has no
    # .gflow_browser_strategy file, so channel_for_profile() returns None.
    assert kwargs["channel"] is None
