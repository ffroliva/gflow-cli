"""Browser fingerprint capture and replay helper for S2/S3 transport strategies.

Per spec § 5.4.1: Google's WAF correlates Bearer/SAPISIDHASH credentials with
browser-shaped headers. An httpx call carrying valid auth but a python-httpx UA
is silently rejected (403 or empty-media decoy 200). S2 and S3 MUST clone
Playwright's fingerprint and replay it on every request. This module provides
the shared utility for capturing and serializing that fingerprint.

capture_fingerprint() is NOT unit-tested (requires live Playwright page).
Phase D e2e tests exercise it. Phase B.0 tests cover the dataclass + constants.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from playwright.async_api import Page, Request

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_HEADERS: frozenset[str] = frozenset(
    {
        "user-agent",
        "accept",
        "accept-language",
        "sec-ch-ua",
        "sec-ch-ua-platform",
        "sec-ch-ua-mobile",
        "sec-fetch-dest",
        "sec-fetch-mode",
        "sec-fetch-site",
        "origin",
        "referer",
    },
)

# Preflight target that reliably triggers browser credential headers without
# producing a real API side-effect.
_PREFLIGHT_URL = "https://aisandbox-pa.googleapis.com/v1/internal/healthz"


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BrowserFingerprint:
    """Immutable snapshot of the browser headers needed for WAF bypass.

    Attributes:
        headers: Raw header dict captured from a Playwright request.
                 Keys may be mixed-case on construction; to_dict() normalises
                 them to lowercase for httpx compatibility.
    """

    headers: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, str]:
        """Return headers with all keys lowercased (httpx canonical form)."""
        return {k.lower(): v for k, v in self.headers.items()}

    def to_json(self) -> str:
        """Serialise to a JSON string (uses lowercase keys)."""
        return json.dumps({"headers": self.to_dict()})

    @classmethod
    def from_json(cls, raw: str) -> BrowserFingerprint:
        """Deserialise from a JSON string produced by to_json().

        If the 'headers' key is absent the instance is created with an empty
        dict, which is safe (caller should validate before use).
        """
        parsed = cast("dict[str, object]", json.loads(raw))
        headers_raw = parsed.get("headers", {})
        headers: dict[str, str]
        if isinstance(headers_raw, dict):
            headers = {str(k): str(v) for k, v in cast("dict[object, object]", headers_raw).items()}
        else:
            headers = {}
        return cls(headers=headers)


# ---------------------------------------------------------------------------
# Capture helper (requires live Playwright page — tested in Phase D e2e)
# ---------------------------------------------------------------------------


async def capture_fingerprint(page: Page) -> BrowserFingerprint:
    """Hook a one-shot request listener on *page*, fire an OPTIONS preflight
    to aisandbox-pa.googleapis.com, and return the browser's outgoing headers
    filtered to REQUIRED_HEADERS.

    Args:
        page: An active Playwright async Page object (already navigated to
              the target origin so cookies/credentials are in scope).

    Returns:
        BrowserFingerprint whose .headers contains only keys in REQUIRED_HEADERS.
    """
    captured: dict[str, str] = {}
    event = asyncio.Event()

    def on_request(request: Request) -> None:
        if event.is_set():
            return
        all_headers: dict[str, str] = request.headers or {}
        captured.update(
            {k.lower(): v for k, v in all_headers.items() if k.lower() in REQUIRED_HEADERS},
        )
        event.set()

    page.on("request", on_request)
    try:
        await page.evaluate(
            "() => fetch("
            f"'{_PREFLIGHT_URL}',"
            " {method: 'OPTIONS', credentials: 'include'}"
            ").catch(() => null)",
        )
        # Give the listener up to 5 s to fire; in practice it fires instantly.
        await asyncio.wait_for(event.wait(), timeout=5.0)
    finally:
        page.remove_listener("request", on_request)

    return BrowserFingerprint(headers=captured)
