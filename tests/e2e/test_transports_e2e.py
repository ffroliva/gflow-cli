"""E2E test suite for transport strategy criteria (spec § 8.2–8.4).

These tests hit the **real Flow API** and therefore:
  - Are NOT collected by default ``pytest`` runs.
  - Opt-in: ``GFLOW_CLI_E2E_PROFILE=<profile_name> pytest -m e2e``
  - Require the named Chromium profile to be logged-in (a Pro/Ultra account).
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
from gflow_cli.api.transports.experimental.bearer import BearerTransport
from gflow_cli.api.transports.experimental.sapisidhash import SapisidhashTransport
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
# Criterion C2 (i2i variant) — local-file reference attach via media dialog (#56)
# ---------------------------------------------------------------------------


def _tiny_png(path: Path) -> Path:
    """Write a valid 8x8 red RGBA PNG (no external asset / Pillow dependency)."""
    import struct
    import zlib

    def _chunk(typ: bytes, data: bytes) -> bytes:
        body = typ + data
        crc = zlib.crc32(body) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + body + struct.pack(">I", crc)

    w = h = 8
    raw = b"".join(b"\x00" + b"\xff\x00\x00\xff" * w for _ in range(h))
    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += _chunk(b"IDAT", zlib.compress(raw))
    png += _chunk(b"IEND", b"")
    path.write_bytes(png)
    return path


@pytest.mark.asyncio
async def test_e2e_i2i_local_ref_attach(tmp_path: Path) -> None:
    """C2/i2i (#56): generate_image with a LOCAL-FILE ``ref_paths`` binds the
    reference through the editor's media dialog and returns >= 1 image.

    UI-automation transport ONLY — the REST transports (bearer/sapisidhash)
    cannot drive the add-media dialog, so they never invoke ``_attach_references``.

    This exercises the locale-agnostic media-dialog selectors (icon ``upload`` +
    iconless 'Add to Prompt') that replaced the text-based selectors which hung
    on non-English Chrome profiles. Costs 1 credit when it runs.
    """
    profile = _profile_dir()
    ref = _tiny_png(tmp_path / "ref.png")
    req = GenerateImageRequest(prompt=_PROMPT, model=Model.NARWHAL, ref_paths=(ref,))

    async with _make_client("evaluate_fetch", profile) as client:
        image = await client.generate_image(req=req)

    assert image.media_name, "i2i ref-attach returned no image"
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

    assert len(all_images) == 20, f"Expected 20 images across 5 batches, got {len(all_images)}"
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
        from gflow_cli.api.transports.experimental.bearer import _CachedAuth

        transport._cached = _CachedAuth(  # type: ignore[attr-defined]
            token="fake-bearer-for-timeout-test",  # NOSONAR
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
    assert elapsed < 35.0, f"TransportTimeoutError must fire within 35 s; took {elapsed:.1f} s"


# ---------------------------------------------------------------------------
# Auto-create project_id (Issue #16 — optional project_id)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
async def test_e2e_generate_image_without_project_id(strategy: str) -> None:
    """generate_image(req=req) without project_id auto-creates a project.

    Confirms the auto-create path works end-to-end against the real Flow API.
    """
    profile = _profile_dir()
    req = GenerateImageRequest(prompt=_PROMPT, model=Model.NARWHAL)

    async with _make_client(strategy, profile) as client:
        # Intentionally omit project_id — the client must create one internally.
        image = await client.generate_image(req=req)

    assert image.media_name, "media_name must be non-empty"
    assert image.fife_url.startswith("https://"), (
        f"fife_url must be an https:// URL, got: {image.fife_url!r}"
    )


# ---------------------------------------------------------------------------
# health_check() (Issue #16 — new method)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
async def test_e2e_health_check_returns_true_when_active(strategy: str) -> None:
    """health_check() returns True for a live browser context on a Google domain."""
    profile = _profile_dir()

    async with _make_client(strategy, profile) as client:
        result = await client.health_check()

    assert result is True, "health_check() must return True for an active Google-domain page"


# ---------------------------------------------------------------------------
# health_check() false path (Issue #16 — new method)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_health_check_false_after_close() -> None:
    """health_check() returns False (never raises) once the client is closed.

    A long-lived worker holding a client whose context has been torn down must
    get a clean False, not an exception. Zero credits — no image generation.

    Not parametrized over STRATEGIES: health_check is transport-agnostic, and
    the bearer / sapisidhash experimental transports fail at setup() (obsolete —
    see KNOWN_ISSUES.md). evaluate_fetch is the live transport.
    """
    profile = _profile_dir()
    client = _make_client("evaluate_fetch", profile)

    async with client:
        assert await client.health_check() is True, (
            "health_check() must be True while the context is live"
        )

    # Context is now closed.
    assert await client.health_check() is False, (
        "health_check() must return False (not raise) on a closed client"
    )
