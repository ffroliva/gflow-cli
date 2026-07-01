"""Browser-engine resolver — Playwright (default) or Patchright (opt-in).

Patchright is an OPT-IN, drop-in patched Playwright (Chromium) that runs page
evaluations in an isolated execution context to avoid the ``Runtime.enable`` CDP
leak, for stronger reCAPTCHA-Enterprise evasion on the **headed** path. It is
selected via ``GFLOW_CLI_BROWSER_ENGINE=patchright`` and must be installed
separately (``pip install patchright``). It is **not** a headless unlock. The
default engine is Playwright and is entirely unaffected by this module — callers
only route through the resolver when the non-default engine is selected.

This module is the single place that reconciles the two engines' differences:

* **Optional import.** ``patchright`` is not a hard dependency. Selecting it when
  it is absent raises a typed :class:`BrowserEngineUnavailableError` (exit 24)
  with a pip remediation hint, never a raw ``ImportError``.
* **Isolated-context mint.** Patchright's ``page.evaluate`` defaults to an
  isolated world where the page's main-world ``grecaptcha`` global is undefined,
  so the reCAPTCHA mint must run with ``isolated_context=False`` under patchright
  (:func:`mint_evaluate_kwargs`). Playwright has no such kwarg.
* **Distinct error classes.** Patchright raises its OWN ``Error`` / ``TimeoutError``
  (``patchright._impl``), which are NOT caught by ``except playwright...``. The
  retry layer must treat both engines' classes as retryable
  (:func:`retryable_engine_errors`) or a patchright transport hiccup surfaces raw.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.config import BrowserEngine, get_settings
from gflow_cli.errors import BrowserEngineUnavailableError

if TYPE_CHECKING:
    from collections.abc import Callable

logger = structlog.get_logger(__name__)

_PATCHRIGHT_PIP_HINT = (
    "GFLOW_CLI_BROWSER_ENGINE=patchright but the 'patchright' package is not "
    "installed. Install it with `pip install patchright` (or "
    "`uv pip install gflow-cli[patchright]`). When using system Chrome "
    "(channel=chrome, the gflow default) you do NOT need `patchright install "
    "chromium`. Or unset GFLOW_CLI_BROWSER_ENGINE to use the default playwright "
    "engine."
)


def active_engine() -> BrowserEngine:
    """Return the configured browser engine (validated ``BrowserEngine`` enum)."""
    return get_settings().browser_engine


def resolve_async_playwright(engine: BrowserEngine | str) -> Callable[[], Any]:
    """Return the ``async_playwright`` factory for *engine*.

    For ``patchright``, raises :class:`BrowserEngineUnavailableError` (exit 24)
    with a pip remediation hint when the optional package is not installed. For
    the default ``playwright`` engine, returns playwright's own factory.
    """
    if engine == BrowserEngine.PATCHRIGHT:
        try:
            mod = importlib.import_module("patchright.async_api")
        except ImportError as exc:
            raise BrowserEngineUnavailableError(
                detail="the 'patchright' package is not installed",
                remediation_hint=_PATCHRIGHT_PIP_HINT,
            ) from exc
        return cast("Callable[[], Any]", mod.async_playwright)
    from playwright.async_api import async_playwright

    return async_playwright


def mint_evaluate_kwargs(engine: BrowserEngine | str | None = None) -> dict[str, Any]:
    """Extra kwargs for the reCAPTCHA ``page.evaluate`` mint call.

    Patchright evaluates in an isolated world by default, where the page's
    main-world ``grecaptcha`` global is undefined; force the main world so the
    mint can see it. Playwright has no such kwarg, so returns ``{}``.
    """
    eng = engine if engine is not None else active_engine()
    if eng == BrowserEngine.PATCHRIGHT:
        return {"isolated_context": False}
    return {}


def retryable_engine_errors() -> tuple[type[BaseException], ...]:
    """Transport-level error classes to retry, unioned across installed engines.

    Patchright's ``Error`` / ``TimeoutError`` are DISTINCT classes from
    playwright's (``patchright._impl`` vs ``playwright._impl``), so both must be
    in the retry predicate — otherwise a patchright TCP reset / connect timeout
    would surface raw to callers instead of being retried.
    """
    from playwright.async_api import Error as PwError
    from playwright.async_api import TimeoutError as PwTimeout

    errs: list[type[BaseException]] = [PwError, PwTimeout]
    try:
        mod = importlib.import_module("patchright.async_api")
    except ImportError:
        return tuple(errs)
    errs.append(cast("type[BaseException]", mod.Error))
    errs.append(cast("type[BaseException]", mod.TimeoutError))
    return tuple(errs)


def log_engine_selected(engine: BrowserEngine | str) -> None:
    """Emit the one-shot engine-selection event (carries the name only — no secrets)."""
    logger.info("browser.engine_selected", engine=str(engine))
