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
import json
import sqlite3
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.async_api import Page

    from gflow_cli.api.dto import GeneratedImage

import structlog

from gflow_cli.api._sapisidhash import compute_sapisidhash
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
from gflow_cli.profile_lease import ProfileLease

log = structlog.get_logger(__name__)

_ORIGIN = "https://labs.google"

# ---------------------------------------------------------------------------
# Module-level helpers (exported for testability)
# ---------------------------------------------------------------------------


def read_sapisid_from_profile(profile_dir: Path) -> str:
    """Read SAPISID cookie value from Chromium's persistent SQLite cookie DB.

    Chromium (v114+) stores cookies at ``<profile>/Default/Network/Cookies``.

    Raises:
        AuthMissingError: if the cookie DB file does not exist, or if no
            SAPISID row with a google.com host is present.
    """
    db_path = profile_dir / "Default" / "Network" / "Cookies"
    if not db_path.exists():
        msg = (
            f"sapisidhash: cookie DB not found at {db_path}. "
            "Run `gflow auth login --profile <name>` first."
        )
        raise AuthMissingError(
            msg,
        )
    # HIGH #6: open read-only via URI mode — Chromium holds an exclusive write
    # lock on this file; an RW open can raise OperationalError or corrupt the
    # journal on some platforms.
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT value, encrypted_value FROM cookies "
                "WHERE name = 'SAPISID' AND host_key LIKE '%google.com%' "
                "LIMIT 1",
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        # MEDIUM #17: map DB errors to AuthMissingError so callers see a
        # consistent error type (e.g. schema mismatch on a different Chromium).
        msg = (
            f"sapisidhash: failed reading SAPISID from {db_path}: {exc}. "
            "Run `gflow auth login --profile <name>`."
        )
        raise AuthMissingError(
            msg,
        ) from exc
    if row is None:
        msg = (
            "sapisidhash: SAPISID cookie not found in profile. "
            "Run `gflow auth login --profile <name>`."
        )
        raise AuthMissingError(
            msg,
        )
    value, encrypted_value = row[0], row[1]
    if not value and encrypted_value:
        # HIGH #8: Chromium 80+ stores the cookie in encrypted_value (DPAPI /
        # libsecret / Keychain); the plaintext value column is empty.
        msg = (
            "sapisidhash: SAPISID cookie is encrypted "
            "(Chromium 80+ DPAPI/libsecret/Keychain). "
            "S3 cannot decrypt this. "
            "Use the `evaluate_fetch` or `bearer` strategy instead via "
            "`--transport evaluate_fetch` or `GFLOW_CLI_TRANSPORT=bearer`."
        )
        raise AuthMissingError(
            msg,
        )
    if not value:
        msg = (
            "sapisidhash: SAPISID cookie not found in profile. "
            "Run `gflow auth login --profile <name>`."
        )
        raise AuthMissingError(
            msg,
        )
    return str(value)


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

    async def setup(self, profile_dir: Path, *, page: Page | None = None) -> None:
        """Read SAPISID and capture browser fingerprint via one Playwright run.

        The ``page`` kwarg is accepted for Protocol conformance with the S1
        shared-page fix (spec § 5.4.4) but is intentionally ignored — S3
        reads SAPISID from the SQLite cookie DB and has its own Playwright
        fingerprint-capture step.
        """
        del page  # accepted for Protocol conformance — see docstring
        self._profile_dir = profile_dir
        self._sapisid = self._read_sapisid(profile_dir)
        self._fingerprint = await self._capture_fingerprint_via_playwright(profile_dir)
        log.info("sapisidhash.setup_done")

    async def teardown(self) -> None:
        """Drop in-memory state. Idempotent."""
        await asyncio.sleep(0)  # yield to event loop — Protocol-required async signature
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
        await asyncio.sleep(0)  # yield to event loop — Protocol-required async signature
        if self._profile_dir is None:
            msg = "sapisidhash: cannot refresh — setup() was never called"
            raise AuthExpiredError(msg)
        try:
            self._sapisid = self._read_sapisid(self._profile_dir)
        except AuthMissingError as exc:
            msg = "sapisidhash: SAPISID missing from profile on refresh — re-login required"
            raise AuthExpiredError(
                msg,
            ) from exc
        log.info("sapisidhash.refresh_done")

    # ------------------------------------------------------------------
    # Playwright fingerprint capture (injectable for tests)
    # ------------------------------------------------------------------

    async def _capture_fingerprint_via_playwright(self, profile_dir: Path) -> BrowserFingerprint:
        """One-shot Playwright launch to capture browser fingerprint headers."""
        from playwright.async_api import async_playwright  # lazy import

        # Own the profile for this momentary fingerprint-capture context (D3).
        # Lease is the OUTER context so it releases only after the driver stops.
        async with ProfileLease(profile_dir), async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=True,
                viewport={"width": 1280, "height": 720},
                locale="en-US",
                args=["--password-store=basic"],
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
        project_id: str | None,
        request: GenerateImageRequest,
    ) -> list[GeneratedImage]:
        """Generate images via pure httpx, replaying SAPISID + fingerprint.

        Single-retry on 401 (SAPISID rotation); raises TransportTimeoutError
        if the call hangs beyond PER_CALL_TIMEOUT_S.
        """
        if project_id is None:
            msg = "SapisidhashTransport requires an explicit project_id"
            raise ValueError(msg)
        if self._sapisid is None or self._profile_dir is None:
            msg = "sapisidhash: setup() was not called before generate_images()"
            raise AuthMissingError(msg)

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
                msg = "sapisidhash: refresh succeeded but retry still returned 401"
                raise AuthExpiredError(
                    msg,
                )

        return interpret_response("sapisidhash", resp)

    async def _call_once(self, url: str, body_bytes: bytes) -> Any:
        """Build SAPISIDHASH headers from current state and fire one POST.

        Raises TransportTimeoutError on asyncio timeout.
        Raises NetworkError on httpx transport errors.
        """
        import httpx  # lazy import

        ts = int(time.time())
        hash_value = compute_sapisidhash(timestamp=ts, sapisid=self._sapisid or "", origin=_ORIGIN)
        headers: dict[str, str] = {
            **self._fingerprint.to_dict(),
            "authorization": f"SAPISIDHASH {hash_value}",
            "content-type": "text/plain;charset=UTF-8",
        }
        # MEDIUM #16: explicitly set origin so it always matches the
        # SAPISIDHASH-computed origin, regardless of what the fingerprint captured.
        headers["origin"] = _ORIGIN

        # HIGH #7: pass NO timeout to _http_post — asyncio.wait_for below is
        # the sole wall-clock guard.  Having both caused httpx.ReadTimeout
        # (mapped to NetworkError) to fire before asyncio saw the hang.
        coro = self._http_post(url, headers=headers, content=body_bytes)
        try:
            return await asyncio.wait_for(coro, timeout=PER_CALL_TIMEOUT_S)
        except TimeoutError as exc:
            msg = f"sapisidhash: hung > {PER_CALL_TIMEOUT_S}s on POST {url}"
            raise TransportTimeoutError(
                msg,
            ) from exc
        except httpx.RequestError as exc:
            msg = f"sapisidhash: httpx request error: {exc}"
            raise NetworkError(msg) from exc

    async def _http_post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        content: bytes,
    ) -> Any:
        """Injectable seam: real httpx POST. Tests replace this with a fake.

        No timeout is set on the client — the asyncio.wait_for in _call_once
        is the sole wall-clock guard (HIGH #7).
        """
        import httpx  # lazy import

        async with httpx.AsyncClient(timeout=None) as client:
            return await client.post(url, headers=headers, content=content)
