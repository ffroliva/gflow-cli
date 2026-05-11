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
from gflow_cli.api.transports.sapisidhash import (
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


def _build_cookie_db(db_path: Path, sapisid_value: str) -> None:
    """Create a minimal Chromium-shaped cookies SQLite DB."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE cookies "
        "(host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB)"
    )
    conn.execute(
        "INSERT INTO cookies VALUES ('.google.com', 'SAPISID', ?, NULL)",
        (sapisid_value,),
    )
    conn.commit()
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
    h1 = compute_sapisidhash(
        timestamp=1700000000, sapisid="x", origin="https://labs.google"
    )
    h2 = compute_sapisidhash(
        timestamp=1700000000, sapisid="x", origin="https://labs.google"
    )
    assert h1 == h2


def test_compute_sapisidhash_changes_with_inputs() -> None:
    base = compute_sapisidhash(
        timestamp=1700000000, sapisid="x", origin="https://labs.google"
    )
    diff_ts = compute_sapisidhash(
        timestamp=1700000001, sapisid="x", origin="https://labs.google"
    )
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
    """DB exists but has no SAPISID row → AuthMissingError."""
    db_path = tmp_path / "Default" / "Network" / "Cookies"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE cookies "
        "(host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB)"
    )
    conn.execute(
        "INSERT INTO cookies VALUES ('.google.com', 'OTHER_COOKIE', 'val', NULL)"
    )
    conn.commit()
    conn.close()
    with pytest.raises(AuthMissingError):
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

    async def fake_post(
        url: str, *, headers: dict[str, str], content: bytes, timeout: float
    ) -> MagicMock:
        captured.update(headers)
        return _make_200_response()

    transport._http_post = fake_post  # type: ignore[method-assign]
    images = await transport.generate_images(project_id="proj", request=_req())

    assert captured["authorization"].startswith("SAPISIDHASH ")
    ts_part, hash_part = captured["authorization"][len("SAPISIDHASH "):].split("_")
    assert ts_part.isdigit()
    assert len(hash_part) == 40  # sha1 hex
    assert captured["user-agent"] == "ua"
    assert captured["origin"] == "https://labs.google"
    assert captured["content-type"] == "text/plain;charset=UTF-8"
    assert len(images) == 1


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

    async def fake_post(
        url: str, *, headers: dict[str, str], content: bytes, timeout: float
    ) -> MagicMock:
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

    async def always_401(
        url: str, *, headers: dict[str, str], content: bytes, timeout: float
    ) -> MagicMock:
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

    async def hang(
        url: str, *, headers: dict[str, str], content: bytes, timeout: float
    ) -> MagicMock:
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
