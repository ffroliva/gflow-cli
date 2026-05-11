"""S3 SapisidhashTransport — compute SAPISIDHASH from SAPISID cookie + httpx.

Per spec § 5.4.3:
  - Read SAPISID from Chromium SQLite cookie DB at Default/Network/Cookies.
  - Compute Authorization: SAPISIDHASH <ts>_<sha1(<ts> <SAPISID> <origin>)>.
  - Re-read SAPISID on 401 (cookie rotation).
  - Browser-fingerprint headers MUST be cloned on every httpx call (§ 5.4.1).
  - No Playwright after setup() — cheapest steady-state strategy.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import structlog

from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import GenerateImageRequest, _build_batch_generate_images_body
from gflow_cli.api.transports._common import (
    FLOW_URL,
    PER_CALL_TIMEOUT_S,
    interpret_response,
    mint_batch_id,
)
from gflow_cli.api.transports._fingerprint import BrowserFingerprint, capture_fingerprint
from gflow_cli.errors import (
    AuthExpiredError,
    AuthMissingError,
    NetworkError,
    TransportTimeoutError,
)

log = structlog.get_logger(__name__)

_ORIGIN = "https://labs.google"

# ---------------------------------------------------------------------------
# Module-level helpers (exported for testability)
# ---------------------------------------------------------------------------


def compute_sapisidhash(*, timestamp: int, sapisid: str, origin: str) -> str:
    """Return ``<timestamp>_<sha1("<timestamp> <sapisid> <origin>")>``."""
    payload = f"{timestamp} {sapisid} {origin}".encode()
    digest = hashlib.sha1(payload).hexdigest()
    return f"{timestamp}_{digest}"


def read_sapisid_from_profile(profile_dir: Path) -> str:
    """Read SAPISID cookie value from Chromium's persistent SQLite cookie DB.

    Chromium (v114+) stores cookies at ``<profile>/Default/Network/Cookies``.

    Raises:
        AuthMissingError: if the cookie DB file does not exist, or if no
            SAPISID row with a google.com host is present.
    """
    db_path = profile_dir / "Default" / "Network" / "Cookies"
    if not db_path.exists():
        raise AuthMissingError(
            f"sapisidhash: cookie DB not found at {db_path}. "
            "Run `gflow auth login --profile <name>` first."
        )
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT value FROM cookies "
            "WHERE name = 'SAPISID' AND host_key LIKE '%google.com%' "
            "LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row[0]:
        raise AuthMissingError(
            "sapisidhash: SAPISID cookie not found in profile. Re-login required."
        )
    return str(row[0])


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


class SapisidhashTransport:
    """S3: read SAPISID once, replay browser fingerprint, pure httpx requests."""

    name = "sapisidhash"

    def __init__(self) -> None:
        self._profile_dir: Path | None = None
        self._sapisid: str | None = None
        self._fingerprint: BrowserFingerprint = BrowserFingerprint()

    # ------------------------------------------------------------------
    # Seam: allow tests to override the cookie-read call
    # ------------------------------------------------------------------

    def _read_sapisid(self, profile_dir: Path) -> str:
        """Injectable seam: delegates to module-level helper. Tests replace this."""
        return read_sapisid_from_profile(profile_dir)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def setup(self, profile_dir: Path) -> None:
        """Read SAPISID and capture browser fingerprint via one Playwright run."""
        self._profile_dir = profile_dir
        self._sapisid = self._read_sapisid(profile_dir)
        self._fingerprint = await self._capture_fingerprint_via_playwright(profile_dir)
        log.info("sapisidhash.setup_done")

    async def teardown(self) -> None:
        """Drop in-memory state. Idempotent."""
        self._sapisid = None
        self._fingerprint = BrowserFingerprint()

    # ------------------------------------------------------------------
    # Auth management
    # ------------------------------------------------------------------

    async def refresh_auth(self) -> None:
        """Re-read SAPISID from disk (cookies rotate during long sessions).

        Does NOT re-launch Playwright — only a SQLite read.

        Raises:
            AuthExpiredError: if setup() was never called, or the cookie is now missing.
        """
        if self._profile_dir is None:
            raise AuthExpiredError(
                "sapisidhash: cannot refresh — setup() was never called"
            )
        try:
            self._sapisid = self._read_sapisid(self._profile_dir)
        except AuthMissingError as exc:
            raise AuthExpiredError(
                "sapisidhash: SAPISID missing from profile on refresh — re-login required"
            ) from exc
        log.info("sapisidhash.refresh_done")

    # ------------------------------------------------------------------
    # Playwright fingerprint capture (injectable for tests)
    # ------------------------------------------------------------------

    async def _capture_fingerprint_via_playwright(
        self, profile_dir: Path
    ) -> BrowserFingerprint:
        """One-shot Playwright launch to capture browser fingerprint headers."""
        from playwright.async_api import async_playwright  # lazy import

        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=True,
                viewport={"width": 1280, "height": 720},
                locale="en-US",
            )
            try:
                page = await ctx.new_page()
                await page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=30_000)
                fp = await capture_fingerprint(page)
            finally:
                await ctx.close()
        return fp

    # ------------------------------------------------------------------
    # Core operation
    # ------------------------------------------------------------------

    async def generate_images(
        self,
        *,
        project_id: str,
        request: GenerateImageRequest,
    ) -> list[GeneratedImage]:
        """Generate images via pure httpx, replaying SAPISID + fingerprint.

        Single-retry on 401 (SAPISID rotation); raises TransportTimeoutError
        if the call hangs beyond PER_CALL_TIMEOUT_S.
        """
        if self._sapisid is None or self._profile_dir is None:
            raise AuthMissingError("sapisidhash: setup() was not called before generate_images()")

        body = _build_batch_generate_images_body(
            request,
            project_id=project_id,
            batch_id=mint_batch_id(),
            seed=0,
            session_id=f";{int(time.time() * 1000)}",
        )
        url = (
            f"https://aisandbox-pa.googleapis.com/v1/projects/"
            f"{project_id}/flowMedia:batchGenerateImages"
        )
        body_bytes = json.dumps(body).encode("utf-8")

        resp = await self._call_once(url, body_bytes)

        if resp.status_code == 401:
            await self.refresh_auth()
            resp = await self._call_once(url, body_bytes)
            if resp.status_code == 401:
                raise AuthExpiredError(
                    "sapisidhash: refresh succeeded but retry still returned 401"
                )

        return interpret_response("sapisidhash", resp)

    async def _call_once(self, url: str, body_bytes: bytes) -> Any:
        """Build SAPISIDHASH headers from current state and fire one POST.

        Raises TransportTimeoutError on asyncio timeout.
        Raises NetworkError on httpx transport errors.
        """
        import httpx  # lazy import

        ts = int(time.time())
        hash_value = compute_sapisidhash(
            timestamp=ts, sapisid=self._sapisid or "", origin=_ORIGIN
        )
        headers: dict[str, str] = {
            **self._fingerprint.to_dict(),
            "authorization": f"SAPISIDHASH {hash_value}",
            "content-type": "text/plain;charset=UTF-8",
        }
        coro = self._http_post(
            url, headers=headers, content=body_bytes, timeout=PER_CALL_TIMEOUT_S
        )
        try:
            return await asyncio.wait_for(coro, timeout=PER_CALL_TIMEOUT_S)
        except TimeoutError as exc:
            raise TransportTimeoutError(
                f"sapisidhash: hung > {PER_CALL_TIMEOUT_S}s on POST {url}"
            ) from exc
        except httpx.RequestError as exc:
            raise NetworkError(f"sapisidhash: httpx request error: {exc}") from exc

    async def _http_post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        content: bytes,
        timeout: float,
    ) -> Any:
        """Injectable seam: real httpx POST. Tests replace this with a fake."""
        import httpx  # lazy import

        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, headers=headers, content=content)
