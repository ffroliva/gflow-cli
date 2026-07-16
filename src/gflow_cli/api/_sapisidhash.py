"""Pure SAPISIDHASH computation — shared by the client and the S3 transport."""

from __future__ import annotations

import hashlib


def compute_sapisidhash(*, timestamp: int, sapisid: str, origin: str) -> str:
    """Return ``<timestamp>_<sha1("<timestamp> <sapisid> <origin>")>``.

    Google's first-party web-app authentication scheme for its private APIs.
    """
    payload = f"{timestamp} {sapisid} {origin}".encode()
    # Google's scheme mandates SHA-1; usedforsecurity=False marks it a protocol hash
    # (not a security primitive) so it also works under FIPS-mode Python.
    digest = hashlib.sha1(payload, usedforsecurity=False).hexdigest()  # noqa: S324
    return f"{timestamp}_{digest}"
