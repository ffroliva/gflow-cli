"""Integration tests for transport timeout budgets (criterion C5).

These tests verify that each transport raises ``TransportTimeoutError`` within
35 s when its inner I/O point hangs.  They do NOT hit the real Flow API — all
network calls are patched with a coroutine that sleeps for 60 s, and the
transport's own ``asyncio.wait_for`` budget must cut them off at ~30 s.

Moving from the e2e suite here is correct because:
  - No browser launch, no Google account, no Flow credits.
  - Fully deterministic: the "network" is a patched coroutine.
  - Fast enough (<35 s per parametrize case) for the integration gate.

Per-strategy patch surface (see comments below):
  evaluate_fetch → ``transport._page.evaluate``
  bearer         → ``transport._http_post``
  sapisidhash    → ``transport._http_post``
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gflow_cli.api.image import GenerateImageRequest, Model
from gflow_cli.api.transports import make_transport
from gflow_cli.errors import TransportTimeoutError

pytestmark = pytest.mark.integration

_PROMPT = "A motivational sunrise over mountains, cinematic, 4K"
_STRATEGIES = ["evaluate_fetch", "bearer", "sapisidhash"]


@pytest.mark.parametrize("strategy", _STRATEGIES)
@pytest.mark.asyncio
async def test_transport_raises_timeout_error_when_io_hangs(
    strategy: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C5: A transport whose inner I/O hangs for 60 s must raise
    ``TransportTimeoutError`` within 35 s via its own ``asyncio.wait_for`` budget.

    No real Flow auth is required — all I/O is patched.
    """
    from gflow_cli.api.transports._fingerprint import BrowserFingerprint

    transport = make_transport(strategy)

    async def _hang(*_args: object, **_kwargs: object) -> object:
        await asyncio.sleep(60)
        return None  # never reached

    if strategy == "evaluate_fetch":
        # EvaluateFetchTransport calls self._page.evaluate(...) inside
        # generate_images. Inject a fake page and mark setup as done so the
        # lazy-init path is skipped.
        fake_page = MagicMock()
        fake_page.evaluate = _hang  # type: ignore[assignment]
        transport._page = fake_page  # type: ignore[attr-defined]
        transport._setup_done = True  # type: ignore[attr-defined]

    elif strategy == "bearer":
        # BearerTransport calls self._http_post(...) after checking self._cached.
        # Build a valid in-memory credential so the proactive-refresh path is
        # skipped, then replace the HTTP send with the hang.
        from gflow_cli.api.transports.experimental.bearer import _CachedAuth

        transport._cached = _CachedAuth(  # type: ignore[attr-defined]
            token="fake-bearer-for-timeout-test",  # NOSONAR
            expires_at=time.time() + 3600,
            fingerprint=BrowserFingerprint(),
        )
        monkeypatch.setattr(transport, "_http_post", _hang)

    elif strategy == "sapisidhash":
        # SapisidhashTransport guards generate_images on
        # ``_sapisid is None or _profile_dir is None``.
        # Populate both + the captured fingerprint, then patch the I/O point.
        # Use os.devnull (portable) rather than Path("/dev/null") (POSIX-only).
        transport._sapisid = "fake-sapisid-for-timeout-test"  # type: ignore[attr-defined]
        transport._profile_dir = Path(os.devnull)  # type: ignore[attr-defined]
        transport._fingerprint = BrowserFingerprint()  # type: ignore[attr-defined]
        monkeypatch.setattr(transport, "_http_post", _hang)

    req = GenerateImageRequest(prompt=_PROMPT, model=Model.NARWHAL)
    t0 = time.monotonic()
    with pytest.raises(TransportTimeoutError):
        await transport.generate_images(
            project_id="00000000-0000-0000-0000-000000000000",
            request=req,
        )
    elapsed = time.monotonic() - t0
    assert elapsed < 35.0, f"TransportTimeoutError must fire within 35 s; took {elapsed:.1f} s"
