# pyright: reportUnusedFunction=false, reportPrivateUsage=false
"""The read path: the live browser's session headers and the shell tokens."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

import httpx
import structlog

from gflow_cli.api import flow_wire
from gflow_cli.api.submit_policy import not_sent
from gflow_cli.auth.cookies import sent_to_flow_host

if TYPE_CHECKING:
    from playwright.async_api import Page

log = structlog.get_logger(__name__)

#: The composer's saved picker state on flow.google.com (spike 2026-11-27-selected-video-model).
PROMPT_BOX_SETTINGS_KEY = "flow-prompt-box-settings"

_AUTH_REASONS = frozenset(
    {
        flow_wire.WireFallbackReason.HTTP_401,
        flow_wire.WireFallbackReason.SIGNED_OUT_SHELL,
        flow_wire.WireFallbackReason.REDIRECT,
    }
)


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

    Called before the mint, never after a send. Each fetch first drops the cached ``at`` and
    the signed-out mark: either may come from a read of the on-disk cookies, and this path
    sends the live browser's. A 401 fetches once more with freshly read cookies; a second
    401, a shell with no ``at`` or a redirect (to sign-in) is a signed-out session.
    """
    for refetch in (False, True):
        headers = await _session_headers(page, profile_dir)
        flow_wire.session_recovered(profile_dir)
        try:
            return await flow_wire.shell_tokens(client, profile_dir, headers)
        except flow_wire.WireReadUnavailable as exc:
            reason = exc.reason
            if reason is not flow_wire.WireFallbackReason.HTTP_401 or refetch:
                raise not_sent(reason.value, auth=reason in _AUTH_REASONS) from exc
        except Exception as exc:
            raise not_sent(f"shell_tokens:{type(exc).__name__}") from exc
    raise AssertionError("unreachable")  # pragma: no cover - the loop returns or raises


async def _prompt_box_settings(page: Page) -> dict[str, object] | None:
    """The account's selected composer settings, or ``None`` when they cannot be read.

    One ``localStorage`` read on a page already on flow.google.com (the armed mint page): no
    navigation, no click. ``localStorage`` belongs to the browser profile, which is the gflow
    profile, so this is the selection Flow's own UI would send.
    """
    try:
        raw = await page.evaluate("k => localStorage.getItem(k)", PROMPT_BOX_SETTINGS_KEY)
        parsed: object = json.loads(raw) if isinstance(raw, str) else None
    except Exception as exc:  # noqa: BLE001 - an unread selection routes to the UI path
        log.info("direct.selection_unread", cause=type(exc).__name__)
        return None
    return cast("dict[str, object]", parsed) if isinstance(parsed, dict) else None
