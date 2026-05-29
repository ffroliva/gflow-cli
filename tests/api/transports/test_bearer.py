"""Tests for S2 BearerTransport — RED phase first, then GREEN.

Adaptations vs. PLAN.md examples:
- GenerateImageRequest has no `count` or `seed` field — dropped.
- Model.NANO2 does not exist — using Model.NARWHAL.
- ContentPolicyRejection does not exist — using ContentPolicyError.
- asyncio_mode="auto" in pyproject.toml — no @pytest.mark.asyncio needed.
- _http_post is an injectable seam on the transport instance.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.api.transports._fingerprint import BrowserFingerprint
from gflow_cli.api.transports.experimental.bearer import BearerTransport, _BearerCache, _CachedAuth
from gflow_cli.errors import AuthExpiredError, TransportTimeoutError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _req() -> GenerateImageRequest:
    return GenerateImageRequest(
        prompt="test prompt",
        model=Model.NARWHAL,
        aspect=Aspect.PORTRAIT,
        recaptcha_token="recap",
    )


def _fake_media_item(uid: str = "abc") -> dict:
    """Build a wire-shaped media item matching GeneratedImage.from_response_item."""
    return {
        "name": f"media/{uid}",
        "workflowId": "wf-001",
        "image": {
            "generatedImage": {
                "seed": "42",
                "prompt": "test prompt",
                "modelNameType": "NARWHAL",
                "aspectRatio": "IMAGE_ASPECT_RATIO_PORTRAIT",
                "fifeUrl": f"https://x.example.com/{uid}.jpg",
            },
            "dimensions": {"width": 1080, "height": 1920},
        },
    }


def _make_200_response(media: list[dict] | None = None) -> MagicMock:
    if media is None:
        media = [_fake_media_item()]
    body = json.dumps({"media": media})
    r = MagicMock()
    r.status_code = 200
    r.text = body
    r.content = body.encode()
    return r


def _make_response(status: int, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.content = text.encode()
    return r


# ---------------------------------------------------------------------------
# _BearerCache — persistence roundtrip
# ---------------------------------------------------------------------------


def test_bearer_cache_persistence_roundtrip(tmp_path: Path) -> None:
    cache = _BearerCache(tmp_path / "transport_bearer.json")
    fp = BrowserFingerprint(headers={"user-agent": "ua1", "origin": "https://labs.google"})
    cache.save(token="bearer_xyz", expires_at=time.time() + 3600, fingerprint=fp)
    loaded = cache.load()
    assert loaded is not None
    assert loaded.token == "bearer_xyz"
    assert loaded.fingerprint.headers["user-agent"] == "ua1"
    assert loaded.fingerprint.headers["origin"] == "https://labs.google"


def test_bearer_cache_load_returns_none_when_missing(tmp_path: Path) -> None:
    cache = _BearerCache(tmp_path / "does_not_exist.json")
    assert cache.load() is None


def test_bearer_cache_json_shape(tmp_path: Path) -> None:
    """Verify the on-disk JSON shape matches the spec."""
    path = tmp_path / "transport_bearer.json"
    cache = _BearerCache(path)
    fp = BrowserFingerprint(headers={"user-agent": "ua-test"})
    expires = time.time() + 3600
    cache.save(token="tok123", expires_at=expires, fingerprint=fp)

    raw = json.loads(path.read_text())
    assert raw["token"] == "tok123"
    assert raw["expires_at"] == pytest.approx(expires, abs=1.0)
    assert raw["fingerprint"]["headers"]["user-agent"] == "ua-test"


# ---------------------------------------------------------------------------
# _CachedAuth — expiry detection
# ---------------------------------------------------------------------------


def test_cached_auth_detects_expiry() -> None:
    already_expired = _CachedAuth(
        token="x",
        expires_at=time.time() - 10,
        fingerprint=BrowserFingerprint(),
    )
    assert already_expired.is_expired(safety_margin_s=0)


def test_cached_auth_not_expired_for_future_token() -> None:
    future = _CachedAuth(
        token="x",
        expires_at=time.time() + 7200,
        fingerprint=BrowserFingerprint(),
    )
    assert not future.is_expired(safety_margin_s=60)


def test_cached_auth_proactive_safety_margin() -> None:
    """Token expiring in 30s is considered expired with a 60s safety margin."""
    near_expiry = _CachedAuth(
        token="x",
        expires_at=time.time() + 30,
        fingerprint=BrowserFingerprint(),
    )
    assert near_expiry.is_expired(safety_margin_s=60)


# ---------------------------------------------------------------------------
# setup() — captures and persists
# ---------------------------------------------------------------------------


async def test_setup_captures_bearer_and_fingerprint(tmp_path: Path) -> None:
    transport = BearerTransport()
    transport._capture_bearer_via_playwright = AsyncMock(  # type: ignore[method-assign]
        return_value=(
            "bearer_captured",
            time.time() + 3600,
            BrowserFingerprint(headers={"user-agent": "ua-from-pw"}),
        )
    )
    await transport.setup(tmp_path)

    cached = transport._cache.load()  # type: ignore[union-attr]
    assert cached is not None
    assert cached.token == "bearer_captured"
    assert cached.fingerprint.headers["user-agent"] == "ua-from-pw"


async def test_setup_uses_cache_hit_without_playwright(tmp_path: Path) -> None:
    """If a non-expired cache exists, setup() must NOT call Playwright."""
    cache = _BearerCache(tmp_path / "transport_bearer.json")
    fp = BrowserFingerprint(headers={"user-agent": "cached-ua"})
    cache.save(token="cached_token", expires_at=time.time() + 7200, fingerprint=fp)

    transport = BearerTransport()
    transport._capture_bearer_via_playwright = AsyncMock()  # type: ignore[method-assign]
    await transport.setup(tmp_path)

    transport._capture_bearer_via_playwright.assert_not_called()
    assert transport._cached is not None
    assert transport._cached.token == "cached_token"


# ---------------------------------------------------------------------------
# generate_images() — fingerprint header replay
# ---------------------------------------------------------------------------


async def test_generate_images_replays_fingerprint_headers(tmp_path: Path) -> None:
    transport = BearerTransport()
    transport._cached = MagicMock(
        token="bearer_xyz",
        expires_at=time.time() + 3600,
        fingerprint=BrowserFingerprint(
            headers={"user-agent": "ua-x", "origin": "https://labs.google"}
        ),
        is_expired=lambda safety_margin_s: False,
    )

    captured_headers: dict[str, str] = {}

    async def fake_post(
        url: str, *, headers: dict, content: bytes, call_timeout: float
    ) -> MagicMock:
        captured_headers.update(headers)
        return _make_200_response()

    transport._http_post = fake_post  # type: ignore[method-assign]
    await transport.generate_images(project_id="proj", request=_req())

    assert captured_headers["authorization"] == "Bearer bearer_xyz"
    assert captured_headers["user-agent"] == "ua-x"
    assert captured_headers["origin"] == "https://labs.google"
    assert captured_headers["content-type"] == "text/plain;charset=UTF-8"


# ---------------------------------------------------------------------------
# generate_images() — proactive TTL refresh
# ---------------------------------------------------------------------------


async def test_generate_images_proactive_refresh_when_near_expiry(tmp_path: Path) -> None:
    transport = BearerTransport()
    transport._cached = MagicMock(
        token="stale",
        expires_at=time.time() + 30,
        fingerprint=BrowserFingerprint(),
        is_expired=lambda safety_margin_s: True,
    )
    transport.refresh_auth = AsyncMock()  # type: ignore[method-assign]

    async def fake_post(*a: object, **kw: object) -> MagicMock:
        return _make_200_response()

    transport._http_post = fake_post  # type: ignore[method-assign]
    await transport.generate_images(project_id="p", request=_req())

    transport.refresh_auth.assert_awaited_once()


# ---------------------------------------------------------------------------
# generate_images() — 401 → single retry → AuthExpiredError
# ---------------------------------------------------------------------------


async def test_generate_images_401_triggers_single_retry_then_auth_expired() -> None:
    transport = BearerTransport()
    transport._cached = MagicMock(
        token="x",
        expires_at=time.time() + 3600,
        fingerprint=BrowserFingerprint(),
        is_expired=lambda safety_margin_s: False,
    )
    transport.refresh_auth = AsyncMock()  # type: ignore[method-assign]

    calls: dict[str, int] = {"n": 0}

    async def fake_post(*a: object, **kw: object) -> MagicMock:
        calls["n"] += 1
        return _make_response(401, "unauthorized")

    transport._http_post = fake_post  # type: ignore[method-assign]

    with pytest.raises(AuthExpiredError):
        await transport.generate_images(project_id="p", request=_req())

    assert calls["n"] == 2  # initial + 1 retry
    transport.refresh_auth.assert_awaited_once()


# ---------------------------------------------------------------------------
# generate_images() — 401 → refresh succeeds → retry returns images
# ---------------------------------------------------------------------------


async def test_generate_images_401_then_refresh_succeeds_returns_images() -> None:
    """Mid-batch TTL recovery: 401 on first call, refresh mints fresh token,
    retry with fresh token succeeds."""
    transport = BearerTransport()
    transport._cached = MagicMock(
        token="stale_token",
        expires_at=time.time() + 3600,
        fingerprint=BrowserFingerprint(),
        is_expired=lambda safety_margin_s: False,
    )

    async def _refresh() -> None:
        transport._cached = MagicMock(
            token="fresh_token",
            expires_at=time.time() + 3600,
            fingerprint=BrowserFingerprint(),
            is_expired=lambda safety_margin_s: False,
        )

    transport.refresh_auth = AsyncMock(side_effect=_refresh)  # type: ignore[method-assign]

    call_count: dict[str, int] = {"n": 0}
    tokens_seen: list[str] = []

    async def fake_post(
        url: str, *, headers: dict, content: bytes, call_timeout: float
    ) -> MagicMock:
        call_count["n"] += 1
        tokens_seen.append(headers.get("authorization", ""))
        if call_count["n"] == 1:
            return _make_response(401, "expired")
        return _make_200_response([_fake_media_item("a")])

    transport._http_post = fake_post  # type: ignore[method-assign]
    images = await transport.generate_images(project_id="p", request=_req())

    assert len(images) == 1
    assert call_count["n"] == 2
    assert tokens_seen == ["Bearer stale_token", "Bearer fresh_token"]


# ---------------------------------------------------------------------------
# generate_images() — 30s timeout → TransportTimeoutError
# ---------------------------------------------------------------------------


async def test_generate_images_30s_timeout_raises_transport_timeout() -> None:
    import asyncio

    transport = BearerTransport()
    transport._cached = MagicMock(
        token="x",
        expires_at=time.time() + 3600,
        fingerprint=BrowserFingerprint(),
        is_expired=lambda safety_margin_s: False,
    )

    async def hang(*a: object, **kw: object) -> MagicMock:
        await asyncio.sleep(9999)
        return MagicMock()  # unreachable

    transport._http_post = hang  # type: ignore[method-assign]

    started = time.perf_counter()
    with pytest.raises(TransportTimeoutError):
        await transport.generate_images(project_id="p", request=_req())
    elapsed = time.perf_counter() - started
    assert elapsed < 35, f"timeout enforcement too slow: {elapsed:.1f}s"


# ---------------------------------------------------------------------------
# teardown() — idempotent
# ---------------------------------------------------------------------------


async def test_teardown_clears_cached_and_is_idempotent() -> None:
    transport = BearerTransport()
    transport._cached = MagicMock(token="tok")
    await transport.teardown()
    assert transport._cached is None
    # Second call must not raise
    await transport.teardown()
    assert transport._cached is None


# ---------------------------------------------------------------------------
# _BearerCache — corruption resilience (HIGH #4)
# ---------------------------------------------------------------------------


def test_bearer_cache_load_returns_none_on_corrupted_json(tmp_path: Path) -> None:
    """Simulate a partial/truncated write; load() must return None, not raise."""
    path = tmp_path / "transport_bearer.json"
    path.write_text('{"token": "partial"')  # truncated — JSONDecodeError
    cache = _BearerCache(path)
    assert cache.load() is None


def test_bearer_cache_load_returns_none_on_missing_keys(tmp_path: Path) -> None:
    """Valid JSON but missing required keys → KeyError → returns None."""
    path = tmp_path / "transport_bearer.json"
    path.write_text('{"something_else": 42}')
    cache = _BearerCache(path)
    assert cache.load() is None


# ---------------------------------------------------------------------------
# _BearerCache — file permissions (HIGH #5)
# ---------------------------------------------------------------------------


def test_bearer_cache_save_sets_owner_only_permissions(tmp_path: Path) -> None:
    """tmp file permissions must be 0o600 before rename (owner-only)."""
    if os.name == "nt":
        pytest.skip("chmod is a no-op on Windows — permission semantics differ")
    path = tmp_path / "transport_bearer.json"
    cache = _BearerCache(path)
    fp = BrowserFingerprint(headers={"user-agent": "ua-perm-test"})
    cache.save(token="tok-perm", expires_at=time.time() + 3600, fingerprint=fp)
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"


# ---------------------------------------------------------------------------
# refresh_auth() — clears _cached even on failure (MEDIUM #14)
# ---------------------------------------------------------------------------


async def test_refresh_auth_clears_cached_on_playwright_failure(tmp_path: Path) -> None:
    """_cached must be None after refresh_auth() raises, even if a stale token existed."""
    transport = BearerTransport()
    transport._profile_dir = tmp_path
    transport._cache = _BearerCache(tmp_path / "transport_bearer.json")
    # Seed a stale cached value
    transport._cached = _CachedAuth(
        token="stale",
        expires_at=time.time() - 1,
        fingerprint=BrowserFingerprint(),
    )

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("browser crashed")

    transport._capture_bearer_via_playwright = _boom  # type: ignore[method-assign]

    from gflow_cli.errors import AuthExpiredError as _AuthExpiredError

    with pytest.raises(_AuthExpiredError):
        await transport.refresh_auth()

    assert transport._cached is None


# ---------------------------------------------------------------------------
# refresh_auth() — wraps non-AuthExpiredError as AuthExpiredError (MEDIUM #13)
# ---------------------------------------------------------------------------


async def test_refresh_auth_wraps_arbitrary_exception_as_auth_expired(
    tmp_path: Path,
) -> None:
    """Any exception from _capture_bearer_via_playwright must become AuthExpiredError."""
    transport = BearerTransport()
    transport._profile_dir = tmp_path
    transport._cache = _BearerCache(tmp_path / "transport_bearer.json")

    async def _crash(*_args: object, **_kwargs: object) -> None:
        raise ConnectionResetError("network died")

    transport._capture_bearer_via_playwright = _crash  # type: ignore[method-assign]

    from gflow_cli.errors import AuthExpiredError as _AuthExpiredError

    with pytest.raises(_AuthExpiredError, match="bearer: refresh failed"):
        await transport.refresh_auth()
