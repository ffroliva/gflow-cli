import hashlib

import pytest

from gflow_cli.api._sapisidhash import compute_sapisidhash


@pytest.mark.unit
def test_compute_sapisidhash_matches_google_convention():
    ts, sapisid, origin = 1700000000, "FAKE_SAPISID", "https://labs.google"
    expected_digest = hashlib.sha1(
        f"{ts} {sapisid} {origin}".encode()
    ).hexdigest()
    assert compute_sapisidhash(timestamp=ts, sapisid=sapisid, origin=origin) == (
        f"{ts}_{expected_digest}"
    )
