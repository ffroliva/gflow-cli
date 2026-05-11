"""Shared BDD fixtures: mocked FlowApiClient, isolated tmp output dirs.

Required: BDD step defs MUST use only mocked clients — never live API.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli import config as _config


@pytest.fixture
def mock_flow_client() -> MagicMock:
    """A fully-stubbed FlowApiClient that doubles as its own async-context-manager.

    `FlowApiClient` is used in production as ``async with FlowApiClient(...) as c:``,
    so the mock must implement ``__aenter__`` / ``__aexit__``. We also wire the
    common async methods so step bindings can replace ``.return_value`` /
    ``.side_effect`` per scenario.
    """
    client = MagicMock(name="FlowApiClient")
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.create_project = AsyncMock(
        return_value=MagicMock(project_id="proj-123", title="bdd-project")
    )
    client.upload_image = AsyncMock(
        return_value=MagicMock(media_name="media-uuid-abc", name="asset-1")
    )
    client.generate_image = AsyncMock()
    client.generate_images_batch = AsyncMock()
    client.generate_video = AsyncMock()
    client.get_video_status = AsyncMock()
    client.download = AsyncMock()
    client.download_image = AsyncMock()
    return client


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent.parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_tmp_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[None, None, None]:
    """Point GFLOW_CLI_OUTPUT_DIR at a per-test tmp dir AND flush the cached
    Settings singleton so the new env var actually takes effect. Without the
    reset, a previously-cached Settings would carry the prior tmp dir into
    this test (the cache is lru-decorated in :mod:`gflow_cli.config`)."""
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(tmp_path))
    _config.reset_settings()
    yield
    _config.reset_settings()


@pytest.fixture(autouse=True)
def _forbid_live_playwright(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Strict mocked-only enforcement (spec §6 DoD wording).

    Any BDD step that accidentally tries to start Playwright — directly via
    ``async_playwright()`` or indirectly via an unmocked ``FlowApiClient`` —
    will fail this fixture's tripwire instead of opening a real browser.
    Catches test drift before it can hide a live-network regression.

    Uses ``yield`` even though ``monkeypatch`` would tear the patch down on
    its own — explicit yield is consistent with the other generator fixtures
    in this directory and signals "this fixture has a lifecycle" to future
    contributors who may add post-yield assertions (e.g. an
    ``assert _explode.call_count == 0`` invariant).
    """

    def _explode(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "BDD step attempted to start live Playwright. All steps MUST use "
            "the `mock_flow_client` fixture. If you need a new mocked response "
            "shape, extend mock_flow_client in conftest.py — do not bypass it."
        )

    monkeypatch.setattr("playwright.async_api.async_playwright", _explode)
    yield
