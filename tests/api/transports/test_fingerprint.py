"""Tests for BrowserFingerprint dataclass and REQUIRED_HEADERS constant.

Per spec § 5.4.1: capture_fingerprint() is NOT tested here (requires live
Playwright page). Phase D e2e tests cover it. This module covers:
- REQUIRED_HEADERS constant completeness
- BrowserFingerprint.to_dict() lowercases all keys
- BrowserFingerprint round-trip serialization via to_json() / from_json()
- Edge cases: empty headers, extra keys in JSON
"""

from __future__ import annotations

import json

import pytest

from gflow_cli.api.transports._fingerprint import (
    REQUIRED_HEADERS,
    BrowserFingerprint,
)

# ---------------------------------------------------------------------------
# REQUIRED_HEADERS constant
# ---------------------------------------------------------------------------

EXPECTED_HEADER_NAMES = {
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
}


def test_required_headers_constant_includes_critical() -> None:
    """All 11 critical header names must be present in REQUIRED_HEADERS."""
    assert EXPECTED_HEADER_NAMES.issubset(REQUIRED_HEADERS)


def test_required_headers_is_frozenset() -> None:
    """REQUIRED_HEADERS must be a frozenset (immutable)."""
    assert isinstance(REQUIRED_HEADERS, frozenset)


def test_required_headers_has_exactly_eleven_entries() -> None:
    """REQUIRED_HEADERS must contain exactly 11 entries."""
    assert len(REQUIRED_HEADERS) == 11


# ---------------------------------------------------------------------------
# BrowserFingerprint.to_dict()
# ---------------------------------------------------------------------------


def test_browser_fingerprint_to_dict_lowercase_keys() -> None:
    """to_dict() must return all-lowercase keys regardless of input case."""
    fp = BrowserFingerprint(
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "en-US,en;q=0.9",
            "SEC-FETCH-MODE": "cors",
        }
    )
    result = fp.to_dict()
    assert list(result.keys()) == [k.lower() for k in result]
    assert result["user-agent"] == "Mozilla/5.0"
    assert result["accept-language"] == "en-US,en;q=0.9"
    assert result["sec-fetch-mode"] == "cors"


def test_browser_fingerprint_to_dict_empty_headers() -> None:
    """to_dict() on an empty-headers fingerprint must return an empty dict."""
    fp = BrowserFingerprint(headers={})
    assert fp.to_dict() == {}


# ---------------------------------------------------------------------------
# BrowserFingerprint serialization round-trip
# ---------------------------------------------------------------------------


def test_browser_fingerprint_serializes_to_json() -> None:
    """Round-trip: to_json() → from_json() must reproduce identical headers."""
    original_headers = {
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "accept": "*/*",
        "origin": "https://labs.google",
    }
    fp = BrowserFingerprint(headers=original_headers)
    recovered = BrowserFingerprint.from_json(fp.to_json())
    assert recovered.headers == original_headers


def test_browser_fingerprint_from_json_missing_headers_key() -> None:
    """from_json() with missing 'headers' key must produce empty headers."""
    raw = json.dumps({})
    fp = BrowserFingerprint.from_json(raw)
    assert fp.headers == {}


def test_browser_fingerprint_to_json_is_valid_json() -> None:
    """to_json() must return a string that is valid JSON."""
    fp = BrowserFingerprint(headers={"accept": "text/html"})
    parsed = json.loads(fp.to_json())
    assert "headers" in parsed
    assert parsed["headers"]["accept"] == "text/html"


def test_browser_fingerprint_is_frozen() -> None:
    """BrowserFingerprint must be immutable (frozen dataclass)."""
    fp = BrowserFingerprint(headers={"accept": "text/html"})
    with pytest.raises((AttributeError, TypeError)):
        fp.headers = {}  # type: ignore[misc]
