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

import asyncio
import contextlib
import importlib
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.config import BrowserEngine, get_settings
from gflow_cli.errors import BrowserEngineUnavailableError

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

logger = structlog.get_logger(__name__)

_PATCHRIGHT_PIP_HINT = (
    "GFLOW_CLI_BROWSER_ENGINE=patchright but the 'patchright' package is not "
    "installed. Install it with `pip install patchright` (or "
    "`uv pip install gflow-cli[patchright]`). When using system Chrome "
    "(channel=chrome, the gflow default) you do NOT need `patchright install "
    "chromium`. Or unset GFLOW_CLI_BROWSER_ENGINE to use the default playwright "
    "engine."
)


# Teardown deadlines (issue #293). The graceful bound is generous on purpose:
# for a persistent context, close() is Chrome flushing profile state (the
# cookie DB is this product's auth store) and Playwright's driver hard-kills
# the process if a second close arrives mid-graceful-close — so escalating a
# merely-slow close would risk corrupting the profile. The force bound is
# short: it only matters when the graceful close already failed, and a dead
# driver connection would otherwise burn the full window as pure delay.
_CONTEXT_CLOSE_TIMEOUT_S = 30.0
_FORCE_CLOSE_TIMEOUT_S = 5.0
DRIVER_STOP_TIMEOUT_S = 10.0
# Backstop for the whole context-teardown step (graceful close + force-close
# fallback). close_context_bounded already bounds each inner await; this is the
# outer wait_for in run_teardown_step, generous so it never pre-empts a normal
# close.
CONTEXT_TEARDOWN_TIMEOUT_S = _CONTEXT_CLOSE_TIMEOUT_S + _FORCE_CLOSE_TIMEOUT_S + 5.0


async def run_teardown_step(
    coro: Coroutine[Any, Any, Any],
    *,
    timeout: float,  # NOSONAR S7483 - bounded-teardown helper; timeout is its contract
    owner: str,
    step: str,
) -> BaseException | None:
    """Run one browser-teardown coroutine, bounded and shielded from an outer
    cancellation (D4).

    Each teardown step (close context/browser, stop the driver, exit the pw
    context manager) is run through here so that:

    * a slow/wedged step cannot hang the caller — it is bounded by ``timeout``;
    * an outer ``CancelledError`` cannot interrupt a step *and* skip a later
      ownership-release step — ``asyncio.shield`` keeps the cancellation from
      propagating into ``coro``, so the caller keeps control and runs the
      remaining steps (driver stop, store close, lease release) before
      re-raising;
    * a failure never propagates — it is logged, so it can never abort teardown
      partway and leak the profile lease.

    Returns the ``CancelledError`` if the CALLER's task was cancelled while this
    step ran (the caller re-raises it *last*, after the remaining teardown),
    else ``None``. Never raises ``Exception``.
    """
    inner = asyncio.ensure_future(asyncio.wait_for(coro, timeout=timeout))
    try:
        await asyncio.shield(inner)
    except asyncio.CancelledError as exc:  # NOSONAR S7497 - caller re-raises last (docstring)
        # Our task was cancelled. shield left ``inner`` running detached; it is
        # bounded by its own wait_for, but abandon it now (the driver stop that
        # runs next force-kills chrome anyway) so the lease release the caller
        # does after this can never be held hostage by a wedged close.
        inner.cancel()
        with contextlib.suppress(BaseException):
            await inner
        return exc
    except Exception:
        logger.warning("browser_teardown.step_failed", owner=owner, step=step, exc_info=True)
    return None


async def _force_close_browser(context: Any, *, owner: str) -> None:
    """Force-close ``context.browser``, bounded — never raises Exception."""
    try:
        browser = context.browser
        if browser is not None:
            await asyncio.wait_for(browser.close(), timeout=_FORCE_CLOSE_TIMEOUT_S)
            logger.warning(
                "browser_teardown.force_close_returned",
                owner=owner,
                note=(
                    "graceful context close failed; browser.close() returned "
                    "(the browser may also simply have been gone already)"
                ),
            )
    except Exception:
        # ponytail: an OS-level tree kill needs a chrome PID that
        # playwright-python does not expose for persistent contexts.
        logger.error(
            "browser_teardown.force_close_failed",
            owner=owner,
            remediation=(
                "a browser may still hold this profile dir — close Chrome "
                "windows using it, or kill the Chrome processes (chrome.exe "
                "on Windows) whose command line names the profile dir"
            ),
            exc_info=True,
        )


async def close_context_bounded(context: Any, *, owner: str) -> bool:
    """Close a persistent BrowserContext without hanging or leaking chrome.

    Issue #293: an unbounded ``context.close()`` can hang forever on a wedged
    page, and a close that FAILS leaves the system-Chrome tree alive holding
    the profile dir once the driver stops — the graceful close is what asks
    Chrome to exit. Bounds the graceful close, then force-closes the browser
    on any failure. On cancellation (Ctrl-C mid-teardown) the force-close is
    still attempted best-effort before the cancellation propagates.

    Returns ``True`` only when the GRACEFUL close completed — a timed-out or
    failed close (force-close path) returns ``False`` so callers that need
    honesty about finalization state (e.g. the incident recorder's HAR
    reporting) can distinguish "closed cleanly" from "gave up and force-closed".
    Existing callers that ignore the return value are unaffected.
    """
    try:
        await asyncio.wait_for(context.close(), timeout=_CONTEXT_CLOSE_TIMEOUT_S)
    except BaseException as exc:
        logger.warning("browser_teardown.context_close_error", owner=owner, exc_info=True)
        await _force_close_browser(context, owner=owner)
        if not isinstance(exc, Exception):
            raise
        return False
    return True


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
