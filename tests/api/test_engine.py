"""Unit tests for the browser-engine resolver (gflow_cli.api._engine).

Covers the Critical/High scenarios from the patchright-engine plan: the
missing-dependency path maps to a typed exit-24 error (not a raw ImportError),
the reCAPTCHA mint kwargs differ per engine (the isolated_context fix), the
retry layer unions BOTH engines' distinct error classes, and the
engine-selection event carries no secrets.
"""

from __future__ import annotations

import sys

import pytest

from gflow_cli.api import _engine
from gflow_cli.config import BrowserEngine, reset_settings
from gflow_cli.errors import EXIT_CODE_MAP, BrowserEngineUnavailableError


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    reset_settings()
    yield
    reset_settings()


def test_active_engine_defaults_to_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GFLOW_CLI_BROWSER_ENGINE", raising=False)
    assert _engine.active_engine() == BrowserEngine.PLAYWRIGHT


def test_active_engine_reads_patchright_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_BROWSER_ENGINE", "patchright")
    assert _engine.active_engine() == BrowserEngine.PATCHRIGHT


def test_resolve_playwright_returns_playwright_factory() -> None:
    from playwright.async_api import async_playwright

    assert _engine.resolve_async_playwright(BrowserEngine.PLAYWRIGHT) is async_playwright


def test_resolve_patchright_missing_raises_typed_exit_24(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the optional import to fail regardless of whether patchright is installed,
    # so the missing-dependency UX is exercised deterministically in CI.
    monkeypatch.setitem(sys.modules, "patchright.async_api", None)
    with pytest.raises(BrowserEngineUnavailableError) as excinfo:
        _engine.resolve_async_playwright(BrowserEngine.PATCHRIGHT)
    assert "pip install patchright" in (excinfo.value.remediation_hint or "")
    assert EXIT_CODE_MAP[BrowserEngineUnavailableError] == 24


@pytest.mark.parametrize(
    ("engine", "expected"),
    [
        (BrowserEngine.PLAYWRIGHT, {}),
        (BrowserEngine.PATCHRIGHT, {"isolated_context": False}),
    ],
)
def test_mint_evaluate_kwargs_per_engine(engine: BrowserEngine, expected: dict) -> None:
    assert _engine.mint_evaluate_kwargs(engine) == expected


def test_retryable_engine_errors_includes_playwright_classes() -> None:
    from playwright.async_api import Error
    from playwright.async_api import TimeoutError as PwTimeout

    errs = _engine.retryable_engine_errors()
    assert Error in errs
    assert PwTimeout in errs


def test_retryable_engine_errors_unions_distinct_patchright_classes() -> None:
    pytest.importorskip("patchright")
    from patchright.async_api import TimeoutError as PtTimeout  # type: ignore[import]
    from playwright.async_api import TimeoutError as PwTimeout

    errs = _engine.retryable_engine_errors()
    assert PtTimeout in errs
    # The whole reason both must be in the predicate: they are NOT the same class.
    assert PtTimeout is not PwTimeout


def test_log_engine_selected_emits_event_without_secrets() -> None:
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        _engine.log_engine_selected(BrowserEngine.PATCHRIGHT)
    events = [e for e in logs if e.get("event") == "browser.engine_selected"]
    assert events
    assert events[0]["engine"] == "patchright"
    assert not any(
        k in events[0] for k in ("token", "authorization", "cookie", "sapisid", "bearer")
    )
