"""Pins the ``FlowApiClient._persistent_context_kwargs()`` seam.

The seam was extracted from the inline ``launch_persistent_context(...)`` call so
a dev-scoped recording subclass can augment the launch without any recording
concern living in core (see ``scripts/dev/_recording_client.py``). This test
proves the extraction is value-for-value behavior-preserving and keeps the
seam's contract stable.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports.ui_automation import UiAutomationTransport


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
    ]
    # Regression for #222: generation must NOT suppress --password-store=basic.
    # auth login seals the profile cookies with the *basic* store; if generation
    # lets Chrome fall back to the macOS keychain, those cookies can't be
    # decrypted -> logged-out -> 401 on createProject. (Unit-level proxy: the
    # real failure only reproduces on a headed Chrome on macOS.)
    assert "--password-store=basic" not in kwargs["ignore_default_args"]
    assert kwargs["args"] == [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
    ]
    # channel is profile-derived; a marker-less tmp_path has no
    # .gflow_browser_strategy file, so channel_for_profile() returns None.
    assert kwargs["channel"] is None


@pytest.mark.asyncio
async def test_ui_automation_setup_passes_disable_dev_shm_usage(tmp_path: Path) -> None:
    """setup() must pass --disable-dev-shm-usage in args to launch_persistent_context."""
    fake_page = MagicMock()
    fake_page.goto = AsyncMock()
    fake_page.add_init_script = AsyncMock()

    fake_ctx = MagicMock()
    fake_ctx.pages = [fake_page]
    fake_ctx.add_init_script = AsyncMock()

    fake_chromium = MagicMock()
    fake_chromium.launch_persistent_context = AsyncMock(return_value=fake_ctx)

    fake_pw = MagicMock()
    fake_pw.chromium = fake_chromium

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=fake_pw)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    mock_async_playwright = MagicMock(return_value=mock_cm)

    with patch("gflow_cli.api.transports.ui_automation.async_playwright", mock_async_playwright):
        transport = UiAutomationTransport()
        await transport.setup(profile_dir=tmp_path)

    _call_kwargs = fake_chromium.launch_persistent_context.call_args
    args_passed = _call_kwargs.kwargs.get(
        "args",
        _call_kwargs.args[1] if len(_call_kwargs.args) > 1 else [],
    )
    assert "--disable-dev-shm-usage" in args_passed
