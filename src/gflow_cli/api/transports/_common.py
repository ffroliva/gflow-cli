"""Shared constants and helpers for image transport strategies.

Per spec § 5.4, all strategies share: the Flow URL, per-call timeout,
batch_id minting, and response interpretation. Extracted here to avoid
duplication across evaluate_fetch.py, bearer.py, and sapisidhash.py.

Council edit (Claude, 2026-05-11): _FLOW_URL was about to be triplicated
across B.1/B.2/B.3 and interpret_response() duplicated in B.2/B.3.
Extracted before strategies are written so the duplication never lands.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from gflow_cli.api.dto import GeneratedImage
from gflow_cli.errors import (
    AuthExpiredError,
    ContentPolicyError,
    NetworkError,
    RateLimitError,
    WafRejectionError,
    WireFormatError,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FLOW_URL: str = "https://labs.google/fx/tools/flow?hl=en"
PER_CALL_TIMEOUT_S: int = 30
BEARER_DEFAULT_TTL_S: int = 3600
REFRESH_SAFETY_MARGIN_S: int = 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def mint_batch_id() -> str:
    """Return a fresh UUID4 string for use as a batch request identifier."""
    return str(uuid.uuid4())


def interpret_response(strategy_name: str, resp: Any) -> list[GeneratedImage]:
    """Map an httpx-like response (status_code + text) to images or raise.

    The strategy_name is included in every error message for traceability
    across S1/S2/S3 stack traces.

    Exception mapping:
      200 + valid non-empty media[]  → list[GeneratedImage]
      200 + empty media[]            → ContentPolicyError
      200 + missing/invalid media    → WireFormatError
      200 + non-JSON body            → WireFormatError (chained from JSONDecodeError)
      401                            → AuthExpiredError (caller handles refresh+retry)
      403                            → WafRejectionError (fingerprint/auth mismatch)
      429                            → RateLimitError
      >=500                          → NetworkError
      other                          → WireFormatError
    """
    status: int = resp.status_code
    text: str = resp.text or ""

    if status == 200:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise WireFormatError(
                f"{strategy_name}: non-JSON response body: {text[:200]}"
            ) from exc

        media = payload.get("media")
        if not isinstance(media, list):
            raise WireFormatError(
                f"{strategy_name}: missing or invalid 'media' list in response: {text[:200]}"
            )
        if not media:
            raise ContentPolicyError(
                f"{strategy_name}: empty media[] — content policy rejection"
            )
        return GeneratedImage.from_response_dict(payload)

    if status == 401:
        raise AuthExpiredError(
            f"{strategy_name}: HTTP 401 from Flow API — session expired"
        )
    if status == 403:
        raise WafRejectionError(
            f"{strategy_name}: HTTP 403 — likely WAF/fingerprint mismatch: {text[:200]}"
        )
    if status == 429:
        raise RateLimitError(
            f"{strategy_name}: HTTP 429 — rate limit hit: {text[:200]}"
        )
    if status >= 500:
        raise NetworkError(
            f"{strategy_name}: HTTP {status} server error: {text[:200]}"
        )

    raise WireFormatError(
        f"{strategy_name}: unexpected HTTP status {status}: {text[:200]}"
    )
