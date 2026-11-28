# pyright: reportUnusedFunction=false, reportPrivateUsage=false
"""The read path: the live browser's session headers and the shell tokens."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from gflow_cli.api import flow_wire
from gflow_cli.api.transports.direct_http._gateway import not_sent
from gflow_cli.auth.cookies import sent_to_flow_host
from gflow_cli.config import get_settings

if TYPE_CHECKING:
    from playwright.async_api import Page


def _proxy_configured() -> bool:
    """A proxy anywhere means the browser and this request could leave by different routes.
    ``NO_PROXY`` alone shows up as the ``no`` key and configures no proxy."""
    return bool(get_settings().browser_proxy) or any(k != "no" for k in flow_wire.getproxies())


async def _session_headers(page: Page, profile_dir: Path) -> dict[str, str]:
    """Read path: the live browser's cookies (the on-disk store is locked while Chrome
    holds the profile) and the profile's own user agent. Read without a URL filter and
    matched by domain: a URL filter also drops path-scoped cookies (#222). Called before
    every request, never cached: cookies rotate in a long-lived process."""
    try:
        cookies = await page.context.cookies()
    except Exception as exc:
        raise not_sent(f"cookie_error:{type(exc).__name__}") from exc
    cookie_header = "; ".join(
        f"{name}={value}"
        for c in cookies
        if (name := c.get("name"))
        and (value := c.get("value"))
        and sent_to_flow_host(c.get("domain", ""))
    )
    if not cookie_header:
        raise not_sent("cookies_empty")
    headers = {"accept-language": "en-US,en;q=0.9", "cookie": cookie_header}
    if ua := flow_wire.user_agent(profile_dir):
        headers["user-agent"] = ua
    return headers


async def _shell(client: httpx.AsyncClient, page: Page, profile_dir: Path) -> flow_wire.Tokens:
    """Read path: ``at`` / ``f.sid`` / ``bl`` from flow_wire's shell GET.

    Called before the mint, never after a send. A 401 drops the cached ``at`` and the
    signed-out mark and fetches once more with freshly read cookies; a second 401 is a
    signed-out session.
    """
    for refetch in (False, True):
        headers = await _session_headers(page, profile_dir)
        try:
            return await flow_wire.shell_tokens(client, profile_dir, headers)
        except flow_wire.WireReadUnavailable as exc:
            reason = exc.reason
            if reason is not flow_wire.WireFallbackReason.HTTP_401:
                raise not_sent(reason.value) from exc
            if refetch:
                raise not_sent(reason.value, auth=True) from exc
            flow_wire.session_recovered(profile_dir)
        except Exception as exc:
            raise not_sent(f"shell_tokens:{type(exc).__name__}") from exc
    raise AssertionError("unreachable")  # pragma: no cover - the loop returns or raises
