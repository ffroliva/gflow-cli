"""FlowApiClient.relaunch(visibility) and display check tests.

Covers:
- test_relaunch_keeps_the_same_lease_instance (row 8)
- test_relaunch_raises_no_profile_locked_in_process (row 8)
- test_other_process_still_sees_the_lock_during_relaunch (row 8)
- test_relaunch_waits_for_checked_out_pages_and_quiesce (row 8)
- test_failed_relaunch_keeps_lease_until_aexit (row 8)
- test_headed_launch_without_display_raises_before_playwright (row 20)
- test_headless_launch_needs_no_display (row 20)
- test_relaunch_resets_transport_on_new_page (row 7)
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.api.client import FlowApiClient, RelaunchFailed
from gflow_cli.api.submit_policy import BrowserVisibility
from gflow_cli.errors import UiModeUnavailableError


def _probe_lease_subprocess(profile_dir: Path) -> bool:
    """Run try_acquire() in an isolated subprocess to bypass in-process registry."""
    code = (
        "import sys; from pathlib import Path; "
        "from gflow_cli.profile_lease import ProfileLease; "
        "lease = ProfileLease(Path(sys.argv[1])); "
        "sys.exit(0 if lease.try_acquire() else 1)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(profile_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _build_fake_context(pages: list[MagicMock] | None = None) -> MagicMock:
    ctx = MagicMock()
    page_list = pages or [MagicMock(name=f"Page{i}") for i in range(4)]
    for p in page_list:
        p.goto = AsyncMock()
    counter = {"i": 0}

    async def _new_page() -> MagicMock:
        i = counter["i"]
        counter["i"] += 1
        return page_list[i % len(page_list)]

    ctx.new_page = AsyncMock(side_effect=_new_page)
    ctx.close = AsyncMock()
    ctx.add_init_script = AsyncMock()
    ctx.cookies = AsyncMock(return_value=[])
    ctx.pages = []
    return ctx


@pytest.fixture
def mock_pw() -> tuple[MagicMock, MagicMock]:
    """Return (pw_instance, fake_context)."""
    fake_ctx = _build_fake_context()
    pw = MagicMock()
    pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_ctx)
    pw.stop = AsyncMock()
    return pw, fake_ctx


@pytest.mark.asyncio
async def test_relaunch_keeps_the_same_lease_instance(
    tmp_path: Path, mock_pw: tuple[MagicMock, MagicMock]
) -> None:
    pw, _ = mock_pw
    with (
        patch("gflow_cli.api.client.async_playwright") as mock_pw_factory,
        patch("gflow_cli.api._flow_bootstrap.read_account_locale", return_value="en"),
        patch("gflow_cli.api.client.ensure_profile_hardened"),
    ):
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        client = FlowApiClient(profile_dir=tmp_path, visibility=BrowserVisibility.HEADLESS)
        async with client:
            lease_before = client._lease
            assert lease_before is not None

            await client.relaunch(BrowserVisibility.HEADLESS)

            assert client._lease is lease_before
            assert client._lease is not None

        assert client._lease is None


@pytest.mark.asyncio
async def test_relaunch_raises_no_profile_locked_in_process(
    tmp_path: Path, mock_pw: tuple[MagicMock, MagicMock]
) -> None:
    pw, _ = mock_pw
    with (
        patch("gflow_cli.api.client.async_playwright") as mock_pw_factory,
        patch("gflow_cli.api._flow_bootstrap.read_account_locale", return_value="en"),
        patch("gflow_cli.api.client.ensure_profile_hardened"),
    ):
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        client = FlowApiClient(profile_dir=tmp_path, visibility=BrowserVisibility.HEADLESS)
        async with client:
            # Must not raise ProfileLockedError("already held in this process")
            await client.relaunch(BrowserVisibility.HEADLESS)


@pytest.mark.asyncio
async def test_other_process_still_sees_the_lock_during_relaunch(
    tmp_path: Path, mock_pw: tuple[MagicMock, MagicMock]
) -> None:
    pw, _ = mock_pw
    probe_mid_relaunch_result: list[bool] = []

    with (
        patch("gflow_cli.api.client.async_playwright") as mock_pw_factory,
        patch("gflow_cli.api._flow_bootstrap.read_account_locale", return_value="en"),
        patch("gflow_cli.api.client.ensure_profile_hardened"),
    ):
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        client = FlowApiClient(profile_dir=tmp_path, visibility=BrowserVisibility.HEADLESS)
        async with client:
            orig_launch = pw.chromium.launch_persistent_context

            async def _probing_launch(*args: Any, **kwargs: Any) -> Any:
                # Subprocess probe called while old browser closed and new browser launching
                probe_mid_relaunch_result.append(_probe_lease_subprocess(tmp_path))
                return await orig_launch(*args, **kwargs)

            pw.chromium.launch_persistent_context = _probing_launch
            await client.relaunch(BrowserVisibility.HEADLESS)

            assert len(probe_mid_relaunch_result) == 1
            # Other process cannot acquire lease: try_acquire() returns False
            assert probe_mid_relaunch_result[0] is False

    # After __aexit__, other process CAN acquire lease
    assert _probe_lease_subprocess(tmp_path) is True


@pytest.mark.asyncio
async def test_relaunch_waits_for_checked_out_pages_and_quiesce(
    tmp_path: Path, mock_pw: tuple[MagicMock, MagicMock]
) -> None:
    pw, _ = mock_pw
    events: list[str] = []

    class _QuiesceContextManager:
        async def __aenter__(self) -> None:
            events.append("quiesce_entered")

        async def __aexit__(self, *args: object) -> None:
            events.append("quiesce_exited")

    mock_transport = MagicMock()
    mock_transport.quiesce = MagicMock(return_value=_QuiesceContextManager())
    mock_transport.setup = AsyncMock()
    mock_transport.teardown = AsyncMock()

    with (
        patch("gflow_cli.api.client.async_playwright") as mock_pw_factory,
        patch("gflow_cli.api._flow_bootstrap.read_account_locale", return_value="en"),
        patch("gflow_cli.api.client.ensure_profile_hardened"),
        patch("gflow_cli.api.client.make_transport", return_value=mock_transport),
    ):
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        client = FlowApiClient(
            profile_dir=tmp_path,
            visibility=BrowserVisibility.HEADLESS,
        )
        async with client:
            orig_close_resources = client._close_browser_resources

            async def _recording_close(*args: Any, **kwargs: Any) -> None:
                events.append("close_resources")
                await orig_close_resources(*args, **kwargs)

            client._close_browser_resources = _recording_close  # type: ignore[method-assign]

            # Checkout page (simulating in-flight download)
            page = await client._checkout_page()
            events.append("page_checked_out")

            relaunch_task = asyncio.create_task(client.relaunch(BrowserVisibility.HEADLESS))
            # Yield control to let relaunch start and wait
            await asyncio.sleep(0.05)

            assert "quiesce_entered" in events
            assert "close_resources" not in events  # blocked waiting for page

            events.append("returning_page")
            client._checkin_page(page)

            await relaunch_task
            assert "close_resources" in events
            assert events.index("returning_page") < events.index("close_resources")


@pytest.mark.asyncio
async def test_failed_relaunch_keeps_lease_until_aexit(
    tmp_path: Path, mock_pw: tuple[MagicMock, MagicMock]
) -> None:
    pw, _ = mock_pw
    with (
        patch("gflow_cli.api.client.async_playwright") as mock_pw_factory,
        patch("gflow_cli.api._flow_bootstrap.read_account_locale", return_value="en"),
        patch("gflow_cli.api.client.ensure_profile_hardened"),
    ):
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        client = FlowApiClient(profile_dir=tmp_path, visibility=BrowserVisibility.HEADLESS)
        async with client:
            held_lease = client._lease
            assert held_lease is not None

            # Fail the relaunch
            pw.chromium.launch_persistent_context = AsyncMock(
                side_effect=RuntimeError("Chrome crash")
            )

            with pytest.raises(RelaunchFailed) as exc_info:
                await client.relaunch(BrowserVisibility.HEADLESS)

            assert "Chrome crash" in str(exc_info.value)
            # Lease is still held on client
            assert client._lease is held_lease
            # Other process still cannot acquire
            assert _probe_lease_subprocess(tmp_path) is False

        # __aexit__ released the lease
        assert client._lease is None
        assert _probe_lease_subprocess(tmp_path) is True


@pytest.mark.asyncio
async def test_headed_launch_without_display_raises_before_playwright(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gflow_cli.api.client.sys.platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        client = FlowApiClient(profile_dir=tmp_path, visibility=BrowserVisibility.HEADED)
        with pytest.raises(UiModeUnavailableError) as exc_info:
            async with client:
                pass

        err = exc_info.value
        assert "DISPLAY" in (err.remediation_hint or str(err)) or "WAYLAND_DISPLAY" in (
            err.remediation_hint or str(err)
        )
        assert "xvfb-run" in (err.remediation_hint or str(err))
        # Playwright must NEVER have been started
        mock_pw_factory.assert_not_called()


@pytest.mark.asyncio
async def test_headless_launch_needs_no_display(
    tmp_path: Path,
    mock_pw: tuple[MagicMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pw, _ = mock_pw
    monkeypatch.setattr("gflow_cli.api.client.sys.platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    with (
        patch("gflow_cli.api.client.async_playwright") as mock_pw_factory,
        patch("gflow_cli.api._flow_bootstrap.read_account_locale", return_value="en"),
        patch("gflow_cli.api.client.ensure_profile_hardened"),
    ):
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        client = FlowApiClient(profile_dir=tmp_path, visibility=BrowserVisibility.HEADLESS)
        async with client:
            assert client._page is not None
        mock_pw_factory.assert_called_once()


@pytest.mark.asyncio
async def test_relaunch_resets_transport_on_new_page(
    tmp_path: Path, mock_pw: tuple[MagicMock, MagicMock]
) -> None:
    pw, _ = mock_pw
    page1 = MagicMock(name="Page1")
    page1.goto = AsyncMock()
    page2 = MagicMock(name="Page2")
    page2.goto = AsyncMock()

    ctx1 = _build_fake_context([page1])
    ctx2 = _build_fake_context([page2])

    contexts = [ctx1, ctx2]

    mock_transport = MagicMock()
    mock_transport.setup = AsyncMock()
    mock_transport.teardown = AsyncMock()

    with (
        patch("gflow_cli.api.client.async_playwright") as mock_pw_factory,
        patch("gflow_cli.api._flow_bootstrap.read_account_locale", return_value="en"),
        patch("gflow_cli.api.client.ensure_profile_hardened"),
        patch("gflow_cli.api.client.make_transport", return_value=mock_transport),
    ):
        pw.chromium.launch_persistent_context = AsyncMock(side_effect=lambda **kw: contexts.pop(0))
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        client = FlowApiClient(
            profile_dir=tmp_path,
            visibility=BrowserVisibility.HEADLESS,
        )
        async with client:
            assert client._page is page1
            mock_transport.setup.assert_awaited_with(tmp_path, page=page1)

            await client.relaunch(BrowserVisibility.HEADLESS)

            assert client._page is page2
            mock_transport.setup.assert_awaited_with(tmp_path, page=page2)
