# pyright: reportUnusedFunction=false, reportPrivateUsage=false
"""The single browser step: a reCAPTCHA token minted on the project page."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from gflow_cli.api._engine import mint_evaluate_kwargs
from gflow_cli.api.recaptcha import TokenMinter
from gflow_cli.api.submit_policy import not_sent
from gflow_cli.api.transports._common import flow_host_kind
from gflow_cli.api.transports.direct_http._gateway import _ORIGIN
from gflow_cli.errors import RecaptchaError

if TYPE_CHECKING:
    from playwright.async_api import Page

_MINT_BUDGET_S = 10.0  # how long a freshly navigated page gets to load reCAPTCHA
_MINT_RETRY_S = 0.5
_NAVIGATE_MS = 45_000  # the composer's own budget for reaching a project


async def _mint(page: Page, project_id: str, action: str) -> str:
    """The single browser step: a reCAPTCHA token for ``action``, minted on the project page.

    The page must end up on flow.google.com: an account Flow serves on labs.google is
    redirected there, and the direct path does not serve it. Right after a navigation the
    enterprise script is not loaded yet and the mint raises ``RecaptchaError`` (measured
    2026-11-23 on an i2i run with ``--project``). A mint spends nothing, so it is retried,
    every attempt bounded, until the script answers or the budget runs out.
    """
    target = f"{_ORIGIN}/project/{project_id}"
    try:
        if not page.url.startswith(target):
            await page.goto(target, wait_until="domcontentloaded", timeout=_NAVIGATE_MS)
    except Exception as exc:
        raise not_sent(f"navigation_failed:{type(exc).__name__}") from exc
    if flow_host_kind(page.url) != "migrated":
        raise not_sent("served_labs")
    minter = TokenMinter(page, mint_evaluate_kwargs=mint_evaluate_kwargs())
    deadline = time.monotonic() + _MINT_BUDGET_S
    while True:
        try:
            remaining = max(deadline - time.monotonic(), 0.1)
            return await asyncio.wait_for(minter.mint(action), remaining)
        except RecaptchaError as exc:
            if time.monotonic() >= deadline:
                raise not_sent("mint_failure:RecaptchaError") from exc
            await asyncio.sleep(_MINT_RETRY_S)
        except Exception as exc:
            raise not_sent(f"mint_failure:{type(exc).__name__}") from exc
