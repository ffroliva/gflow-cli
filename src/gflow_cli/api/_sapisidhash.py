"""Pure SAPISIDHASH computation — shared by the client and the S3 transport."""

from __future__ import annotations

import hashlib


def compute_sapisidhash(*, timestamp: int, sapisid: str, origin: str) -> str:
    """Return ``<timestamp>_<sha1("<timestamp> <sapisid> <origin>")>``.

    Google's first-party web-app authentication scheme for its private APIs.
    """
    payload = f"{timestamp} {sapisid} {origin}".encode()
    digest = hashlib.sha1(payload).hexdigest()  # noqa: S324 — Google's scheme mandates SHA-1
    return f"{timestamp}_{digest}"
