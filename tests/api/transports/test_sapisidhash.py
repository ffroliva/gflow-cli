"""Tests for S3 SapisidhashTransport — RED phase first, then GREEN.

Adaptations vs. PLAN.md examples:
- GenerateImageRequest has no `count` or `seed` field — dropped.
- Model.NANO2 does not exist — using Model.NARWHAL.
- ContentPolicyRejection does not exist — using ContentPolicyError.
- asyncio_mode="auto" in pyproject.toml — no @pytest.mark.asyncio needed.
- _http_post is an injectable seam on the transport instance.
- Wire-shaped media JSON matches B.2 fixture shape (name/workflowId/image.*).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.api.transports._fingerprint import BrowserFingerprint
from gflow_cli.api.transports.experimental.sapisidhash import (
    SapisidhashTransport,
    compute_sapisidhash,
    read_sapisid_from_profile,
)
from gflow_cli.errors import AuthExpiredError, AuthMissingError, TransportTimeoutError

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


def _build_cookie_db(
    db_path: Path,
    sapisid_value: str,
    encrypted_value: bytes | None = None,
) -> None:
    """Create a minimal Chromium-shaped cookies SQLite DB."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB)"
        )
        conn.execute(
            "INSERT INTO cookies VALUES ('.google.com', 'SAPISID', ?, ?)",
            (sapisid_value, encrypted_value),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# compute_sapisidhash — pure function
# ---------------------------------------------------------------------------


def test_compute_sapisidhash_format() -> None:
    h = compute_sapisidhash(
        timestamp=1700000000, sapisid="testSAPISID", origin="https://labs.google"
    )
    parts = h.split("_")
    assert parts[0] == "1700000000"
    assert len(parts[1]) == 40  # sha1 hex digest = 40 chars


def test_compute_sapisidhash_deterministic() -> None:
    h1 = compute_sapisidhash(timestamp=1700000000, sapisid="x", origin="https://labs.google")
    h2 = compute_sapisidhash(timestamp=1700000000, sapisid="x", origin="https://labs.google")
    assert h1 == h2


def test_compute_sapisidhash_changes_with_inputs() -> None:
    base = compute_sapisidhash(timestamp=1700000000, sapisid="x", origin="https://labs.google")
    diff_ts = compute_sapisidhash(timestamp=1700000001, sapisid="x", origin="https://labs.google")
    diff_sapisid = compute_sapisidhash(
        timestamp=1700000000, sapisid="y", origin="https://labs.google"
    )
    assert base != diff_ts
    assert base != diff_sapisid


# ---------------------------------------------------------------------------
# read_sapisid_from_profile
# ---------------------------------------------------------------------------


def test_read_sapisid_from_profile_missing_file_raises(tmp_path: Path) -> None:
    """No cookie DB file at all → AuthMissingError."""
    with pytest.raises(AuthMissingError):
        read_sapisid_from_profile(tmp_path)


def test_read_sapisid_from_profile_returns_value(tmp_path: Path) -> None:
    """Minimal SQLite DB with SAPISID row → returns the value."""
    db_path = tmp_path / "Default" / "Network" / "Cookies"
    _build_cookie_db(db_path, "sap_value_xyz")
    val = read_sapisid_from_profile(tmp_path)
    assert val == "sap_value_xyz"


def test_read_sapisid_from_profile_missing_row_raises(tmp_path: Path) -> None:
    """DB exists but has no SAPISID row → AuthMissingError with login hint."""
    db_path = tmp_path / "Default" / "Network" / "Cookies"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB)"
        )
        conn.execute("INSERT INTO cookies VALUES ('.google.com', 'OTHER_COOKIE', 'val', NULL)")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(AuthMissingError, match="gflow auth login"):
        read_sapisid_from_profile(tmp_path)


def test_read_sapisid_from_profile_encrypted_value_raises_actionable(
    tmp_path: Path,
) -> None:
    """HIGH #8: value='' + encrypted_value blob → AuthMissingError with transport hint."""
    db_path = tmp_path / "Default" / "Network" / "Cookies"
    _build_cookie_db(db_path, sapisid_value="", encrypted_value=b"\x01\x02\x03\xde\xad")
    with pytest.raises(AuthMissingError, match="evaluate_fetch"):
        read_sapisid_from_profile(tmp_path)


def test_read_sapisid_from_profile_sqlite_error_mapped(tmp_path: Path) -> None:
    """MEDIUM #17: sqlite3.OperationalError → AuthMissingError (schema mismatch)."""
    db_path = tmp_path / "Default" / "Network" / "Cookies"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        # Deliberately create a cookies table with WRONG schema (missing columns)
        conn.execute("CREATE TABLE cookies (host_key TEXT, name TEXT)")
        conn.execute("INSERT INTO cookies VALUES ('.google.com', 'SAPISID')")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(AuthMissingError, match="failed reading SAPISID"):
        read_sapisid_from_profile(tmp_path)


# ---------------------------------------------------------------------------
# setup() — SAPISID read + fingerprint capture
# ---------------------------------------------------------------------------


async def test_setup_reads_sapisid_and_captures_fingerprint(tmp_path: Path) -> None:
    transport = SapisidhashTransport()
    transport._read_sapisid = lambda p: "sap_xyz"  # type: ignore[method-assign]
    transport._capture_fingerprint_via_playwright = AsyncMock(  # type: ignore[method-assign]
        return_value=BrowserFingerprint(
            headers={"user-agent": "ua-pw", "origin": "https://labs.google"}
        )
    )
    await transport.setup(tmp_path)
    assert transport._sapisid == "sap_xyz"
    assert transport._fingerprint.headers["user-agent"] == "ua-pw"


# ---------------------------------------------------------------------------
# generate_images() — SAPISIDHASH header + fingerprint replay
# ---------------------------------------------------------------------------


async def test_generate_images_includes_sapisidhash_and_fingerprint_headers(
    tmp_path: Path,
) -> None:
    transport = SapisidhashTransport()
    transport._sapisid = "sap"
    transport._fingerprint = BrowserFingerprint(
        headers={"user-agent": "ua", "origin": "https://labs.google"}
    )
    transport._profile_dir = tmp_path

    captured: dict[str, str] = {}

    async def fake_post(url: str, *, headers: dict[str, str], content: bytes) -> MagicMock:
        captured.update(headers)
        return _make_200_response()

    transport._http_post = fake_post  # type: ignore[method-assign]
    images = await transport.generate_images(project_id="proj", request=_req())

    assert captured["authorization"].startswith("SAPISIDHASH ")
    ts_part, hash_part = captured["authorization"][len("SAPISIDHASH ") :].split("_")
    assert ts_part.isdigit()
    assert len(hash_part) == 40  # sha1 hex
    assert captured["user-agent"] == "ua"
    # MEDIUM #16: origin must always equal _ORIGIN regardless of fingerprint
    assert captured["origin"] == "https://labs.google"
    assert captured["content-type"] == "text/plain;charset=UTF-8"
    assert len(images) == 1


async def test_generate_images_origin_overrides_fingerprint_origin(
    tmp_path: Path,
) -> None:
    """MEDIUM #16: origin header must equal _ORIGIN even if fingerprint has a different value."""
    transport = SapisidhashTransport()
    transport._sapisid = "sap"
    # Fingerprint has a DIFFERENT origin — _call_once must override it.
    transport._fingerprint = BrowserFingerprint(
        headers={"user-agent": "ua", "origin": "https://wrong.example.com"}
    )
    transport._profile_dir = tmp_path

    captured: dict[str, str] = {}

    async def fake_post(url: str, *, headers: dict[str, str], content: bytes) -> MagicMock:
        captured.update(headers)
        return _make_200_response()

    transport._http_post = fake_post  # type: ignore[method-assign]
    await transport.generate_images(project_id="proj", request=_req())

    assert captured["origin"] == "https://labs.google"


# ---------------------------------------------------------------------------
# generate_images() — 401 → refresh_auth once → retry succeeds
# ---------------------------------------------------------------------------


async def test_generate_images_401_rereads_cookie_then_retries(
    tmp_path: Path,
) -> None:
    transport = SapisidhashTransport()
    transport._sapisid = "old_sap"
    transport._fingerprint = BrowserFingerprint()
    transport._profile_dir = tmp_path

    async def _fake_refresh() -> None:
        transport._sapisid = "new_sap"

    transport.refresh_auth = AsyncMock(side_effect=_fake_refresh)  # type: ignore[method-assign]

    calls: dict[str, int] = {"n": 0}

    async def fake_post(url: str, *, headers: dict[str, str], content: bytes) -> MagicMock:
        calls["n"] += 1
        if calls["n"] == 1:
            return _make_response(401, "unauth")
        return _make_200_response([_fake_media_item("x")])

    transport._http_post = fake_post  # type: ignore[method-assign]
    images = await transport.generate_images(project_id="p", request=_req())

    assert len(images) == 1
    assert calls["n"] == 2
    transport.refresh_auth.assert_awaited_once()


# ---------------------------------------------------------------------------
# generate_images() — 401 persists after refresh → AuthExpiredError
# ---------------------------------------------------------------------------


async def test_generate_images_401_persists_raises_auth_expired() -> None:
    transport = SapisidhashTransport()
    transport._sapisid = "sap"
    transport._fingerprint = BrowserFingerprint()
    transport._profile_dir = Path("/fake")
    transport.refresh_auth = AsyncMock()  # type: ignore[method-assign]

    async def always_401(url: str, *, headers: dict[str, str], content: bytes) -> MagicMock:
        return _make_response(401, "still unauth")

    transport._http_post = always_401  # type: ignore[method-assign]

    with pytest.raises(AuthExpiredError):
        await transport.generate_images(project_id="p", request=_req())

    transport.refresh_auth.assert_awaited_once()


# ---------------------------------------------------------------------------
# generate_images() — 30s timeout → TransportTimeoutError
# ---------------------------------------------------------------------------


async def test_generate_images_30s_timeout_raises_transport_timeout(
    tmp_path: Path,
) -> None:
    import asyncio

    transport = SapisidhashTransport()
    transport._sapisid = "x"
    transport._fingerprint = BrowserFingerprint()
    transport._profile_dir = tmp_path

    async def hang(url: str, *, headers: dict[str, str], content: bytes) -> MagicMock:
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


async def test_teardown_clears_state_and_is_idempotent() -> None:
    transport = SapisidhashTransport()
    transport._sapisid = "sap"
    transport._fingerprint = BrowserFingerprint(headers={"user-agent": "ua"})

    await transport.teardown()
    assert transport._sapisid is None
    assert transport._fingerprint.headers == {}

    # Second call must not raise
    await transport.teardown()
    assert transport._sapisid is None


# ---------------------------------------------------------------------------
# compute_sapisidhash — pinned reference (LOW bonus)
# ---------------------------------------------------------------------------


def test_compute_sapisidhash_pinned_reference() -> None:
    """Pinned-value regression guard for SHA1 field order and separator."""
    # Reference: sha1("1700000000 testSAPISID https://labs.google")
    # Computed: hashlib.sha1(b"1700000000 testSAPISID https://labs.google").hexdigest()
    expected_sha1 = "7677682daa4bee6e2b26875da985970aa60e250c"
    h = compute_sapisidhash(
        timestamp=1700000000, sapisid="testSAPISID", origin="https://labs.google"
    )
    assert h == f"1700000000_{expected_sha1}"


# ---------------------------------------------------------------------------
# D3 — profile-lease ownership around the momentary fingerprint-capture context
# ---------------------------------------------------------------------------


class _FakePwCM:
    def __init__(self, pw: object) -> None:
        self._pw = pw

    async def __aenter__(self) -> object:
        return self._pw

    async def __aexit__(self, *_a: object) -> bool:
        return False


def _record_lease_events(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    from gflow_cli.profile_lease import ProfileLease

    def acq(self: ProfileLease) -> ProfileLease:
        events.append("acquire")
        return self

    def rel(self: ProfileLease) -> None:
        events.append("release")

    monkeypatch.setattr(ProfileLease, "acquire", acq)
    monkeypatch.setattr(ProfileLease, "release", rel)


async def test_capture_fingerprint_wraps_launch_in_profile_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_capture_fingerprint_via_playwright owns the profile for its momentary
    context: acquire before launch, release after the context closes."""
    events: list[str] = []
    _record_lease_events(monkeypatch, events)

    page = MagicMock()
    page.goto = AsyncMock()
    ctx = MagicMock()
    ctx.new_page = AsyncMock(return_value=page)
    ctx.close = AsyncMock()

    async def _launch(*_a: object, **_k: object) -> MagicMock:
        events.append("launch")
        return ctx

    pw = MagicMock()
    pw.chromium.launch_persistent_context = AsyncMock(side_effect=_launch)

    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: _FakePwCM(pw))
    monkeypatch.setattr(
        "gflow_cli.api.transports.experimental.sapisidhash.capture_fingerprint",
        AsyncMock(return_value=BrowserFingerprint()),
    )

    transport = SapisidhashTransport()
    await transport._capture_fingerprint_via_playwright(tmp_path)
    assert events == ["acquire", "launch", "release"]
