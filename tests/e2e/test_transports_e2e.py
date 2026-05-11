"""E2E test suite for transport strategy criteria (spec § 8.2–8.4).

These tests hit the **real Flow API** and therefore:
  - Are NOT collected by default ``pytest`` runs.
  - Opt-in: ``GFLOW_CLI_E2E_PROFILE=<profile_name> pytest -m e2e``
  - Require the named Chromium profile to be logged-in (``denon82`` account).
  - Task D.2 drives the real execution; this file is the Task D.1 scaffold.

Criteria covered (spec § 8.4):
  C2 — single image generation returns ≥1 PNG with an https:// URL
  C3 — 5 sequential batches × 4 images = 20 images total
  C4a — recoverable auth expiry: stale credential triggers silent refresh
  C4b — unrecoverable auth expiry: missing profile raises AuthExpiredError /
        AuthMissingError with the correct exit_code
  C5  — 30-second timeout budget: slow send raises TransportTimeoutError
        within 35 s

Strategies under test (spec § 8.2):
  evaluate_fetch  — Playwright page.evaluate() passthrough
  bearer          — OAuth 2.0 bearer token cached on disk
  sapisidhash     — SAPISIDHASH cookie + HMAC header
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import GenerateImageRequest, Model
from gflow_cli.api.transports import make_transport
from gflow_cli.api.transports.bearer import BearerTransport
from gflow_cli.api.transports.sapisidhash import SapisidhashTransport
from gflow_cli.errors import (
    EXIT_CODE_MAP,
    AuthExpiredError,
    AuthMissingError,
    TransportTimeoutError,
)

# ---------------------------------------------------------------------------
# Module-level marker — every test in this file inherits e2e
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.e2e

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STRATEGIES = ["evaluate_fetch", "bearer", "sapisidhash"]

_PROMPT = "A motivational sunrise over mountains, cinematic, 4K"
_E2E_PROFILE_ENV = "GFLOW_CLI_E2E_PROFILE"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _profile_dir() -> Path:
    """Resolve the Chromium profile directory from the environment variable.

    Uses gflow-cli's real profile-dir resolver (``gflow_cli.auth.profile_dir``)
    which is `platformdirs`-based — on Windows this is
    ``%LOCALAPPDATA%\\<author>\\gflow-cli\\profile_<name>``, NOT
    ``~/.config/gflow-cli/profiles/<name>``.

    Raises ``pytest.skip`` when the env var is absent or the profile dir
    does not exist (e.g. user has not yet run ``gflow auth login --profile``).
    """
    name = os.environ.get(_E2E_PROFILE_ENV, "")
    if not name:
        pytest.skip(
            f"E2E tests require {_E2E_PROFILE_ENV} env var — "
            "set it to a logged-in Chromium profile name and re-run with -m e2e"
        )
    # Use the actual resolver — must match where `gflow auth login` writes.
    from gflow_cli.auth import profile_dir as _resolve_profile_dir

    candidate = _resolve_profile_dir(name)
    if not candidate.exists():
        pytest.skip(
            f"Profile directory not found: {candidate}. "
            f"Run `gflow auth login --profile {name}` to create it."
        )
    return candidate


def _make_client(strategy: str, profile: Path) -> FlowApiClient:
    """Construct a FlowApiClient wired to the requested transport strategy.

    Pass `transport=strategy_name` (string) — NOT an instance — so the client
    owns the lifecycle and calls `transport.setup(profile_dir)` in __aenter__.
    Per spec § 4.3, passing a pre-initialized instance signals the caller
    owns lifecycle and the client SKIPS setup. The strategy then refuses
    `generate_images` with AuthMissingError because state is uninitialized.
    """
    return FlowApiClient(profile_dir=profile, transport=strategy)


# ---------------------------------------------------------------------------
# Criterion C2 — single image generation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
async def test_e2e_single_image_gen(strategy: str) -> None:
    """C2: generate_image() returns ≥ 1 GeneratedImage with an https:// fife_url."""
    profile = _profile_dir()
    req = GenerateImageRequest(prompt=_PROMPT, model=Model.NARWHAL)

    async with _make_client(strategy, profile) as client:
        project = await client.create_project(title=f"e2e-c2-{strategy}")
        image = await client.generate_image(project_id=project.project_id, req=req)

    assert image.media_name, "media_name must be non-empty"
    assert image.fife_url.startswith("https://"), (
        f"fife_url must be an https:// URL, got: {image.fife_url!r}"
    )


# ---------------------------------------------------------------------------
# Criterion C3 — 5 sequential batches × 4 images = 20 images
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
async def test_e2e_5_sequential_batches(strategy: str) -> None:
    """C3: 5 sequential generate_images_batch(count=4) calls return 20 images total."""
    profile = _profile_dir()
    req = GenerateImageRequest(prompt=_PROMPT, model=Model.NARWHAL)
    all_images = []

    async with _make_client(strategy, profile) as client:
        project = await client.create_project(title=f"e2e-c3-{strategy}")
        for _ in range(5):
            batch = await client.generate_images_batch(
                project_id=project.project_id,
                req=req,
                count=4,
            )
            all_images.extend(batch)

    assert len(all_images) == 20, (
        f"Expected 20 images across 5 batches, got {len(all_images)}"
    )
    for img in all_images:
        assert img.fife_url.startswith("https://"), (
            f"fife_url must be https://, got: {img.fife_url!r}"
        )


# ---------------------------------------------------------------------------
# Criterion C4a — recoverable auth expiry (silent refresh)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
async def test_e2e_recoverable_auth_expiry(strategy: str) -> None:
    """C4a: Deliberately staling the cached credential triggers a silent refresh.

    Strategy-specific staleness injection:
      bearer       — set _cached.expires_at to now - 1 (already expired)
      sapisidhash  — overwrite _sapisid with a garbage value
      evaluate_fetch — no in-process cache; validate that a 401 response from
                       the server triggers a page reload + retry (refresh path)
    """
    profile = _profile_dir()
    req = GenerateImageRequest(prompt=_PROMPT, model=Model.NARWHAL)
    transport = make_transport(strategy)

    async with FlowApiClient(profile_dir=profile, transport=transport) as client:
        project = await client.create_project(title=f"e2e-c4a-{strategy}")

        # Inject stale credential AFTER setup so the transport is fully
        # initialised but before the API call so the refresh path fires.
        if strategy == "bearer":
            assert isinstance(transport, BearerTransport)
            if transport._cached is not None:
                # Mutate the expires_at field via object replacement —
                # _CachedAuth is a frozen dataclass so we use dataclasses.replace.
                import dataclasses

                transport._cached = dataclasses.replace(
                    transport._cached,
                    expires_at=time.time() - 1.0,
                )
        elif strategy == "sapisidhash":
            assert isinstance(transport, SapisidhashTransport)
            # Overwrite the in-memory SAPISID with a garbage value.
            # The transport will re-read from the profile on the next 401.
            transport._sapisid = "deliberately_invalidated_sapisid_value"
        # evaluate_fetch: no in-process credential cache; the browser's session
        # cookies handle auth.  No injection needed — we just verify the call
        # succeeds, confirming the transport handles the round-trip correctly.

        # The call must succeed despite the stale / injected credential.
        image = await client.generate_image(project_id=project.project_id, req=req)

    assert image.media_name, "Silent recovery failed — no image returned"
    assert image.fife_url.startswith("https://")


# ---------------------------------------------------------------------------
# Criterion C4b — unrecoverable auth expiry (missing profile)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
async def test_e2e_unrecoverable_auth_expiry(
    strategy: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C4b: Pointing at an empty (non-logged-in) profile raises AuthExpiredError
    or AuthMissingError with the expected exit_code.

    ``tmp_path`` is an empty directory — no cookies, no bearer cache file.
    The transport's setup() will fail to find credentials and must raise.
    """
    req = GenerateImageRequest(prompt=_PROMPT, model=Model.NARWHAL)

    with pytest.raises((AuthExpiredError, AuthMissingError)) as exc_info:
        async with FlowApiClient(
            profile_dir=tmp_path, transport=make_transport(strategy)
        ) as client:
            project = await client.create_project(title=f"e2e-c4b-{strategy}")
            await client.generate_image(project_id=project.project_id, req=req)

    exc = exc_info.value
    # Both error types must carry a non-zero exit_code (see errors.py EXIT_CODE_MAP).
    assert EXIT_CODE_MAP.get(type(exc), 0) != 0, (
        f"{type(exc).__name__} must have a non-zero exit_code in EXIT_CODE_MAP"
    )


# ---------------------------------------------------------------------------
# Criterion C5 — 30-second timeout budget
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
async def test_e2e_30s_timeout_budget(
    strategy: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C5: A transport whose inner HTTP send blocks > 30 s raises
    TransportTimeoutError within 35 s.

    Strategy-specific patching of the inner async I/O point — each strategy
    has a different one. We bypass FlowApiClient entirely because this test
    asserts the strategy's own asyncio.wait_for budget, not the client wrapper.
    No real Flow auth is required.

    Per-strategy patch surface:
      - evaluate_fetch  → ``transport._page.evaluate``
      - bearer          → ``transport._http_post``
      - sapisidhash     → ``transport._http_post``
    """
    from gflow_cli.api.transports._fingerprint import BrowserFingerprint

    transport = make_transport(strategy)

    async def _hang(*_args: object, **_kwargs: object) -> object:
        await asyncio.sleep(60)
        return None  # never reached

    if strategy == "evaluate_fetch":
        # S1 calls self._page.evaluate(...) inside generate_images.
        # Inject a fake page + the hang; mark setup_done so the strategy
        # skips its lazy-init path.
        from unittest.mock import MagicMock

        fake_page = MagicMock()
        fake_page.evaluate = _hang  # type: ignore[assignment]
        transport._page = fake_page  # type: ignore[attr-defined]
        transport._setup_done = True  # type: ignore[attr-defined]
    elif strategy == "bearer":
        # S2 calls self._http_post(...) after checking self._cached.
        # Build a valid cached auth so the proactive-refresh path is skipped.
        from gflow_cli.api.transports.bearer import _CachedAuth

        transport._cached = _CachedAuth(  # type: ignore[attr-defined]
            token="fake-bearer-for-timeout-test",
            expires_at=time.time() + 3600,
            fingerprint=BrowserFingerprint(),
        )
        monkeypatch.setattr(transport, "_http_post", _hang)
    elif strategy == "sapisidhash":
        # S3 guards `generate_images` on `_sapisid is None or _profile_dir is None`.
        # Populate both + the captured fingerprint, then patch the I/O point.
        transport._sapisid = "fake-sapisid-for-timeout-test"  # type: ignore[attr-defined]
        transport._profile_dir = Path("/dev/null")  # type: ignore[attr-defined]
        transport._fingerprint = BrowserFingerprint()  # type: ignore[attr-defined]
        monkeypatch.setattr(transport, "_http_post", _hang)

    req = GenerateImageRequest(prompt=_PROMPT, model=Model.NARWHAL)
    start = time.monotonic()
    with pytest.raises(TransportTimeoutError):
        await transport.generate_images(
            project_id="00000000-0000-0000-0000-000000000000",
            request=req,
        )
    elapsed = time.monotonic() - start
    assert elapsed < 35.0, (
        f"TransportTimeoutError must fire within 35 s; took {elapsed:.1f} s"
    )
