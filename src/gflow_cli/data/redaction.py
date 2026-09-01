from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal, cast

PromptMode = Literal["store", "redacted"]
# "fife_url": the recorder persists the snake_case spelling (recorder.py),
# which the camelCase-only "fifeurl" entry missed (#542).
SENSITIVE_URL_KEYS = {"fifeurl", "fife_url", "signedurl", "downloadurl", "mediaurl"}
SENSITIVE_QUERY_KEYS = ("signature=", "x-goog-signature=", "x-goog-credential=", "expires=")

# Free-text secret patterns for error_detail scrubbing (#341). `redact_metadata`
# redacts by dict key name / URL markers only, so a secret interpolated into an
# exception message ("HTTP 403: ... Bearer ya29.xxx") would pass through it
# verbatim — these patterns cover the prose case. All case-insensitive: header
# dumps are frequently lowercased ("cookie: sapisid=...").
_SECRET_TEXT_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"SAPISIDHASH\s+\S+", re.IGNORECASE),
    # Google auth cookie pairs — both header ("SAPISID=x") and equals forms.
    # SAPISIDHASH before SAPISID before the bare SID family so the longest
    # name wins; \b keeps bare SID from firing inside unrelated words.
    re.compile(
        r"\b(?:__Secure-(?:next-auth\.session-token|[13]PSID[A-Z]*)"
        r"|SAPISIDHASH|SAPISID|APISID|SSID|HSID|OSID|LSID|SID)"
        r"\s*=\s*\S+",
        re.IGNORECASE,
    ),
)
# Any whitespace-delimited token carrying signed-query material — covers full
# URLs and bare "path?X-Goog-Signature=..." fragments alike.
_SIGNED_QUERY_TOKEN_PATTERN = re.compile(
    r"\S*(?:signature=|x-goog-signature=|x-goog-credential=|expires=)\S*",
    re.IGNORECASE,
)
ERROR_DETAIL_MAX_CHARS = 500


@dataclass(frozen=True)
class PromptFields:
    prompt: str | None
    prompt_hash: str | None
    prompt_redacted: bool


def prompt_fields(prompt: str | None, *, mode: PromptMode) -> PromptFields:
    if prompt is None:
        return PromptFields(prompt=None, prompt_hash=None, prompt_redacted=False)
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if mode == "redacted":
        return PromptFields(prompt=None, prompt_hash=digest, prompt_redacted=True)
    return PromptFields(prompt=prompt, prompt_hash=digest, prompt_redacted=False)


def redact_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        d = cast("dict[str, Any]", value)
        for key, item in d.items():
            lowered: str = key.lower()
            # `sessionid` joined this set with the extend route (2026-09-01):
            # its clientContext carries one, and while it is not a credential it
            # is account-correlatable and would otherwise survive verbatim into
            # any logged body or diagnostics bundle.
            if lowered in {"token", "recaptchatoken", "sessionid", "session_id"}:
                out[key] = "<redacted:token>"
            elif lowered in {"authorization", "cookie", "set-cookie"}:
                out[key] = "<redacted:secret>"
            elif lowered in SENSITIVE_URL_KEYS and isinstance(item, str):
                out[key] = "<redacted:url>"
            else:
                out[key] = redact_metadata(item)
        return out
    if isinstance(value, list):
        items = cast("list[Any]", value)
        return [redact_metadata(item) for item in items]
    if isinstance(value, str) and any(marker in value.lower() for marker in SENSITIVE_QUERY_KEYS):
        return "<redacted:url>"
    return value


def redact_error_detail(detail: str) -> str:
    """Scrub a free-text error detail before it is persisted to the DB (#341).

    Applied to ``GFlowError.to_problem_details()['detail']`` on the FAILED
    operation write path. Scrubs bearer/SAPISIDHASH/cookie-pair secrets, drops
    URLs carrying signed-query material, and truncates post-redaction as
    defense-in-depth against a scrub bypass.
    """
    for pattern in _SECRET_TEXT_PATTERNS:
        detail = pattern.sub("<redacted:secret>", detail)
    detail = _SIGNED_QUERY_TOKEN_PATTERN.sub("<redacted:url>", detail)
    return detail[:ERROR_DETAIL_MAX_CHARS]
