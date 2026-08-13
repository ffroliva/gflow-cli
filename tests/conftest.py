"""Shared pytest fixtures for the gflow-cli test suite.

The ``install_log_capture`` fixture replaces 4+ inline copies of
``structlog.configure(processors=[merge_contextvars, cap])`` scattered across
test files. Centralizing prevents the most likely regression: a new test that
needs to assert on a contextvar-bound field (e.g. ``correlation_id``) and
silently omits ``merge_contextvars`` from its processor chain, yielding
mysteriously-missing fields in the captured event dict.

``_isolate_settings`` is an autouse fixture that prevents the cached
``get_settings()`` singleton from ever resolving to the developer's real
``platformdirs`` paths during test runs. It writes ``GFLOW_CLI_HOME`` and
``GFLOW_CLI_DB_PATH`` to per-test tmp dirs, then clears the ``lru_cache``
before and after every test. Without this, any test that calls
``get_settings()`` without first patching the env will open — and write into
— the developer's production catalog (issue #86).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Redirect GFLOW_CLI_HOME and GFLOW_CLI_DB_PATH to per-test tmp dirs.

    Clears the ``get_settings()`` lru_cache before the test so the first call
    inside the test (or in any fixture that runs after this one) reads the
    patched env vars, not a stale cached value from a previous test. Cache is
    cleared again at teardown so it does not bleed into the next test.

    Tests that need a specific DB (e.g. ``seeded_db`` in ``test_cli_data.py``)
    can override ``GFLOW_CLI_DB_PATH`` with another ``monkeypatch.setenv`` call
    after this fixture runs — the cache is already cleared so the next
    ``get_settings()`` will pick up the overridden value.

    **Tests that assert default-resolution behavior** (i.e. the "no explicit
    path" code path that lets ``platformdirs`` pick the location) must first
    undo the autouse env with ``monkeypatch.delenv("GFLOW_CLI_DB_PATH",
    raising=False)`` (see ``test_settings_resolves_default_db_path``). Tests in
    ``tests/test_config.py::TestDefaults`` use the ``clean_env`` fixture which
    strips all ``GFLOW_CLI_*`` vars and already covers this case.
    """
    from gflow_cli.config import reset_settings

    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path / "gflow_home"))
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(tmp_path / "test_gflow.db"))
    # #479: the per-test empty home means the update-check cache is always
    # stale — without this, any CliRunner test on a non-editable install
    # without CI set would spawn a real PyPI request. tests/test_update_check.py
    # re-enables it explicitly.
    monkeypatch.setenv("GFLOW_CLI_UPDATE_CHECK", "0")
    reset_settings()
    yield
    reset_settings()


@pytest.fixture
def install_log_capture() -> structlog.testing.LogCapture:
    """Install a fresh structlog ``LogCapture`` processor + ``merge_contextvars``.

    Use as a fixture argument::

        def test_event_shape(install_log_capture: structlog.testing.LogCapture) -> None:
            log = structlog.get_logger("test")
            log.info("hello", extra="value")
            assert install_log_capture.entries[0]["event"] == "hello"

    structlog 25.x's ``LogCapture()`` takes no constructor args; captured
    events accumulate on ``.entries``. ``merge_contextvars`` must run BEFORE
    ``LogCapture`` so contextvar-bound fields (``correlation_id``,
    ``cli_version``) land in the captured event dict — without it, any test
    that asserts on those fields silently fails with a confusing "key missing".
    """
    cap = structlog.testing.LogCapture()
    structlog.configure(
        processors=[structlog.contextvars.merge_contextvars, cap],
        cache_logger_on_first_use=False,
    )
    return cap
