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
_SECRET_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # flow.google.com batchexecute material, shapes measured 2026-10-16
    # (docs/superpowers/spikes/2026-10-16-migrated-character-lifecycle-wire.md).
    # `lh3.googleusercontent.com/asb/…` grants access by its path alone — there is
    # no `Signature=` for the signed-query pattern below to key on.
    (re.compile(r"https?://lh3\.googleusercontent\.com/asb/\S+", re.IGNORECASE), "<redacted:url>"),
    # The `at` XSRF value: 28 token chars measured, ':' (or '%3A' in a form body), ms
    # timestamp. The lookbehind lets a match START only where a token run starts, and
    # the length is bounded: unanchored and unbounded, every position inside a long run
    # re-scanned the run for its ':' — quadratic, minutes on a 160 KB body (#852 class).
    (
        re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{20,64}(?::|%3A)\d{13}(?!\d)", re.IGNORECASE),
        "<redacted:token>",
    ),
    # reCAPTCHA Enterprise tokens (`maseQ` carries one), then any token-alphabet run
    # long enough to be a credential. The floor matches batchexecute._TOKEN_RE, which
    # slices its window BEFORE matching and so can leave a fragment behind.
    (re.compile(r"0cAF[A-Za-z0-9_-]{100,}"), "<redacted:token>"),
    (re.compile(r"[A-Za-z0-9_-]{120,}"), "<redacted:token>"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE), "<redacted:secret>"),
    (re.compile(r"SAPISIDHASH\s+\S+", re.IGNORECASE), "<redacted:secret>"),
    # Google auth cookie pairs — both header ("SAPISID=x") and equals forms.
    # SAPISIDHASH before SAPISID before the bare SID family so the longest
    # name wins; \b keeps bare SID from firing inside unrelated words.
    (
        re.compile(
            r"\b(?:__Secure-(?:next-auth\.session-token|[13]PSID[A-Z]*)"
            r"|SAPISIDHASH|SAPISID|APISID|SSID|HSID|OSID|LSID|SID)"
            r"\s*=\s*\S+",
            re.IGNORECASE,
        ),
        "<redacted:secret>",
    ),
    # Account addresses (chooser/login identity in error details and logs).
    # Kept distinct from <redacted:secret>: an address is correlatable PII,
    # not a credential, and operators triage chooser failures by cohort.
    (
        # Bounded so a path is not mistaken for an address: `C:/x@y.co/path` and
        # `/var/x@y.io/cache` are ordinary paths, and this pattern runs on EVERY
        # persisted error detail, transport snippet and worker payload — the one
        # artifact left for debugging a failure, where a silent rewrite cannot be
        # told apart from the original text. The lookbehind rejects a match starting
        # mid-token or right after a path separator; the lookahead rejects one that
        # continues into a path segment.
        re.compile(
            r"(?<![\w.%+/\\-])"
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
            r"(?![\w.-]*[/\\])",
            re.IGNORECASE,
        ),
        "<redacted:email>",
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
    operation write path. Scrubs bearer/SAPISIDHASH/cookie-pair secrets and account addresses, drops
    URLs carrying signed-query material, and truncates post-redaction as
    defense-in-depth against a scrub bypass.
    """
    for pattern, replacement in _SECRET_TEXT_PATTERNS:
        detail = pattern.sub(replacement, detail)
    detail = _SIGNED_QUERY_TOKEN_PATTERN.sub("<redacted:url>", detail)
    return detail[:ERROR_DETAIL_MAX_CHARS]
