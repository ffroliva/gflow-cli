"""S2 BearerTransport — capture Bearer once via Playwright, pure httpx after.

Per spec §§ 5.4.1 + 5.4.2:
  - Browser-fingerprint headers MUST be cloned on every httpx call (WAF bypass).
  - Proactive TTL refresh: check expiry before each call; single-retry-on-401.
  - Playwright is only invoked during setup() / refresh_auth() — kept lazy.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from playwright.async_api import Request as PlaywrightRequest

import structlog

from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import GenerateImageRequest, _build_batch_generate_images_body
from gflow_cli.api.transports._common import (
    BEARER_DEFAULT_TTL_S,
    FLOW_URL,
    PER_CALL_TIMEOUT_S,
    REFRESH_SAFETY_MARGIN_S,
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


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CachedAuth:
    """In-memory representation of a captured Bearer session."""

    token: str
    expires_at: float
    fingerprint: BrowserFingerprint

    def is_expired(self, safety_margin_s: float = REFRESH_SAFETY_MARGIN_S) -> bool:
        """True when the token will expire within *safety_margin_s* seconds."""
        return time.time() + safety_margin_s >= self.expires_at


class _BearerCache:
    """Persist / load Bearer auth to ``profile_dir / "transport_bearer.json"``.

    WARNING: contains live OAuth Bearer token — ``transport_bearer.json`` must
    NOT be committed to version control.  Add it to ``.gitignore``.

    JSON shape::

        {
            "token": "<bearer>",
            "expires_at": 1700000000.0,
            "fingerprint": {"headers": {"user-agent": "...", ...}}
        }
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def save(
        self,
        *,
        token: str,
        expires_at: float,
        fingerprint: BrowserFingerprint,
    ) -> None:
        """Write cache atomically (tmp → rename) with owner-only permissions."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "token": token,
            "expires_at": expires_at,
            "fingerprint": json.loads(fingerprint.to_json()),
        }
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload))
        # chmod before rename so the file is never world-readable, even briefly.
        # On Windows chmod(0o600) is a no-op but does not raise.
        tmp_path.chmod(0o600)
        os.replace(tmp_path, self.path)

    def load(self) -> _CachedAuth | None:
        """Return a ``_CachedAuth`` from disk, or ``None`` if absent or corrupted."""
        if not self.path.exists():
            return None
        try:
            data: dict[str, Any] = json.loads(self.path.read_text())
            fp_raw: Any = data.get("fingerprint", {})
            fp_headers: dict[str, str] = (
                cast(dict[str, str], fp_raw["headers"])
                if isinstance(fp_raw, dict) and "headers" in fp_raw
                else {}
            )
            fingerprint = BrowserFingerprint(headers=fp_headers)
            return _CachedAuth(
                token=str(data["token"]),
                expires_at=float(data["expires_at"]),
                fingerprint=fingerprint,
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            log.warning("bearer.cache_corrupt_ignored", path=str(self.path))
            return None


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


class BearerTransport:
    """S2: capture Bearer + fingerprint once via Playwright, replay via httpx."""

    name = "bearer"

    def __init__(self) -> None:
        self._cache: _BearerCache | None = None
        self._cached: _CachedAuth | None = None
        self._profile_dir: Path | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def setup(self, profile_dir: Path) -> None:
        """Initialise transport.

        Loads cached Bearer from disk if present and not expired; otherwise
        launches Playwright to capture a fresh Bearer + fingerprint.
        """
        self._profile_dir = profile_dir
        self._cache = _BearerCache(profile_dir / "transport_bearer.json")

        loaded = self._cache.load()
        if loaded is not None and not loaded.is_expired():
            self._cached = loaded
            log.info("bearer.setup_cache_hit", expires_at=loaded.expires_at)
            return

        token, expires_at, fp = await self._capture_bearer_via_playwright(profile_dir)
        self._cache.save(token=token, expires_at=expires_at, fingerprint=fp)
        self._cached = _CachedAuth(token=token, expires_at=expires_at, fingerprint=fp)
        log.info("bearer.setup_capture_done", expires_at=expires_at)

    async def teardown(self) -> None:
        """Drop in-memory Bearer. Idempotent. Disk cache is preserved."""
        self._cached = None

    # ------------------------------------------------------------------
    # Auth management
    # ------------------------------------------------------------------

    async def refresh_auth(self) -> None:
        """Re-capture Bearer via Playwright and update cache.

        Clears ``_cached`` immediately so that any failure leaves the transport
        in an unauthenticated state (callers will surface ``AuthMissingError``
        rather than re-using a stale token).

        Raises ``AuthExpiredError`` if re-capture fails (e.g. cookies expired,
        browser crash, or any other unexpected error).
        """
        # MEDIUM #14 — clear before capture; if capture fails, _cached stays None.
        self._cached = None

        if self._profile_dir is None:
            raise AuthExpiredError(
                "bearer: cannot refresh — setup() was never called"
            )
        try:
            token, expires_at, fp = await self._capture_bearer_via_playwright(
                self._profile_dir
            )
        except AuthExpiredError:
            raise
        except Exception as exc:
            raise AuthExpiredError(f"bearer: refresh failed: {exc}") from exc

        if self._cache is not None:
            self._cache.save(token=token, expires_at=expires_at, fingerprint=fp)
        self._cached = _CachedAuth(token=token, expires_at=expires_at, fingerprint=fp)
        log.info("bearer.refresh_done", expires_at=expires_at)

    async def _capture_bearer_via_playwright(
        self, profile_dir: Path
    ) -> tuple[str, float, BrowserFingerprint]:
        """Launch Playwright once, intercept the outgoing Bearer from Flow.

        Returns ``(token, expires_at, fingerprint)``.
        Raises ``AuthExpiredError`` if no Bearer is intercepted.
        """
        from playwright.async_api import async_playwright  # lazy import

        captured_token: str | None = None
        captured_fp = BrowserFingerprint()

        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=True,
                viewport={"width": 1280, "height": 720},
                locale="en-US",
            )
            try:
                page = await ctx.new_page()

                def _on_request(req: PlaywrightRequest) -> None:
                    nonlocal captured_token
                    if "aisandbox-pa.googleapis.com" not in req.url:
                        return
                    auth: str = req.headers.get("authorization", "")
                    if auth.startswith("Bearer ") and captured_token is None:
                        captured_token = auth[len("Bearer "):]

                page.on("request", _on_request)
                await page.goto(FLOW_URL, wait_until="networkidle", timeout=60_000)
                await page.wait_for_timeout(5_000)
                captured_fp = await capture_fingerprint(page)
            finally:
                await ctx.close()

        if not captured_token:
            raise AuthExpiredError(
                "bearer: failed to intercept Bearer token from Flow page. "
                "Run `gflow auth login --profile <name>` to refresh the session."
            )

        # Flow does not return an explicit expiry; assume default TTL.
        expires_at = time.time() + BEARER_DEFAULT_TTL_S
        return captured_token, expires_at, captured_fp

    # ------------------------------------------------------------------
    # Core operation
    # ------------------------------------------------------------------

    async def generate_images(
        self,
        *,
        project_id: str,
        request: GenerateImageRequest,
    ) -> list[GeneratedImage]:
        """Generate images via pure httpx, replaying the captured fingerprint.

        Proactive refresh if token is near expiry; single-retry on 401.
        Raises ``TransportTimeoutError`` if the call hangs > PER_CALL_TIMEOUT_S.
        """
        if self._cached is None:
            raise AuthMissingError("bearer: setup() was not called before generate_images()")

        if self._cached.is_expired(REFRESH_SAFETY_MARGIN_S):
            await self.refresh_auth()

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
                    "bearer: refresh succeeded but retry still returned 401"
                )

        return interpret_response("bearer", resp)

    async def _call_once(self, url: str, body_bytes: bytes) -> Any:
        """Build headers from current cached auth and fire one HTTP POST.

        Raises ``TransportTimeoutError`` on asyncio timeout.
        Raises ``NetworkError`` on httpx transport errors.
        """
        if self._cached is None:
            raise AuthMissingError("bearer: _cached is None inside _call_once")

        import httpx  # lazy import — keeps module import cheap

        headers: dict[str, str] = {
            **self._cached.fingerprint.to_dict(),
            "authorization": f"Bearer {self._cached.token}",
            "content-type": "text/plain;charset=UTF-8",
        }
        coro = self._http_post(
            url, headers=headers, content=body_bytes, timeout=PER_CALL_TIMEOUT_S
        )
        try:
            return await asyncio.wait_for(coro, timeout=PER_CALL_TIMEOUT_S)
        except TimeoutError as exc:
            raise TransportTimeoutError(
                f"bearer: hung > {PER_CALL_TIMEOUT_S}s on POST {url}"
            ) from exc
        except httpx.RequestError as exc:
            raise NetworkError(f"bearer: httpx request error: {exc}") from exc

    async def _http_post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        content: bytes,
        timeout: float,
    ) -> Any:
        """Injectable seam: real httpx POST.  Tests replace this with a fake."""
        import httpx  # lazy import

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            return await client.post(url, headers=headers, content=content)
