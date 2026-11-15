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
_REDACTED_URL = "<redacted:url>"
_REDACTED_TOKEN = "<redacted:token>"  # noqa: S105  # the placeholder, not a credential

# Free-text secret patterns for error_detail scrubbing (#341). `redact_metadata`
# redacts by dict key name / URL markers only, so a secret interpolated into an
# exception message ("HTTP 403: ... Bearer ya29.xxx") would pass through it
# verbatim — these patterns cover the prose case. All case-insensitive: header
# dumps are frequently lowercased ("cookie: sapisid=...").
_SECRET_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Credentials inside a URL ("http://user:pass@127.0.0.1:7897", #1036's proxy).
    # First, so the email rule below cannot half-redact "pass@host" and leave "user:".
    # The password runs to the LAST "@" before the host: an unencoded "@" in it is common.
    # The scheme is bounded: unbounded, every word start inside a long `a-a-a-` run
    # scanned to its end for "://" (quadratic, 8 s on 40 KB; review of #987 slice 1).
    (
        re.compile(r"\b([a-z][a-z0-9+.-]{0,31}://)[^\s/@:]+:[^\s/]*@", re.IGNORECASE),
        r"\1<redacted:secret>@",
    ),
    # flow.google.com batchexecute material, shapes measured 2026-10-16
    # (docs/superpowers/spikes/2026-10-16-migrated-character-lifecycle-wire.md).
    # `lh3.googleusercontent.com/asb/…` grants access by its path alone — there is
    # no `Signature=` for the signed-query pattern below to key on. It stops at a JSON
    # delimiter, so the rest of a compact reply survives.
    (
        re.compile(r"https?://lh\d\.googleusercontent\.com/asb/[^\s\"'\\,\]]+", re.IGNORECASE),
        _REDACTED_URL,
    ),
    # The `at` XSRF value: 28 token chars measured, ':' (or '%3A' in a form body), ms
    # timestamp. The lookbehind lets a match START only where a token run starts, and
    # the length is bounded: unanchored and unbounded, every position inside a long run
    # re-scanned the run for its ':' — quadratic, minutes on a 160 KB body (#852 class).
    (
        re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{20,64}(?::|%3[Aa])\d{13}(?!\d)"),
        _REDACTED_TOKEN,
    ),
    (re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE), "<redacted:secret>"),
    (re.compile(r"SAPISIDHASH\s+\S+", re.IGNORECASE), "<redacted:secret>"),
    # Google auth cookie pairs — both header ("SAPISID=x") and equals forms.
    # SAPISIDHASH before SAPISID before the bare SID family so the longest
    # name wins; the lookbehind keeps bare SID from firing inside unrelated words
    # or on the batchexecute `f.sid=` field, which has its own rule below.
    (
        re.compile(
            r"(?<![\w.])(?:__Secure-(?:next-auth\.session-token|[13]PSID[A-Z]*|[13]PAPISID)"
            r"|SAPISIDHASH|SAPISID|APISID|SSID|HSID|OSID|LSID|SIDCC|SID|NID|AEC)"
            r"\s*=\s*\S+",
            re.IGNORECASE,
        ),
        "<redacted:secret>",
    ),
    # batchexecute form/query fields (#1229): the page's `at` token and session id. No
    # spaces around "=" and stops at "&", so prose and the next field survive.
    (re.compile(r"(?<![\w.])(at|f\.sid)=[^&\s]+"), r"\1=<redacted:secret>"),
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
        # The domain and the path lookahead are bounded (RFC 5321 lengths): unbounded, a
        # long `a.a.a.` run after an "@" took 36 s on 80 KB (review of #987 slice 1). The
        # local part needs no bound: the lookbehind lets it start only after a delimiter.
        re.compile(
            r"(?<![\w.%+/\\-])"
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}"
            r"(?![\w.-]{0,253}[/\\])",
            re.IGNORECASE,
        ),
        "<redacted:email>",
    ),
    # reCAPTCHA Enterprise tokens (`maseQ` carries one). A shape, not a length: a
    # generic "120+ token chars" rule would also erase Flow media ids, which recovery
    # messages must name. Last, so it cannot cut a Bearer token or an address in two.
    (re.compile(r"0cAF[A-Za-z0-9_-]{100,}"), _REDACTED_TOKEN),
)
# Any whitespace-delimited token carrying signed-query material — covers full
# URLs and bare "path?X-Goog-Signature=..." fragments alike.
#
# Token by token, not one pattern: an unanchored `\S*marker\S*` re-scanned the rest of a
# long whitespace-free run from every position — quadratic (10k URL-like chars took ~2 s;
# council D3, 2026-10-16, the #852 class).
_WHITESPACE_TOKEN = re.compile(r"\S+")
ERROR_DETAIL_MAX_CHARS = 500


def _drop_signed_query_token(match: re.Match[str]) -> str:
    token = match.group()
    lowered = token.lower()
    return _REDACTED_URL if any(marker in lowered for marker in SENSITIVE_QUERY_KEYS) else token


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
                out[key] = _REDACTED_TOKEN
            elif lowered in {"authorization", "cookie", "set-cookie"}:
                out[key] = "<redacted:secret>"
            elif lowered in SENSITIVE_URL_KEYS and isinstance(item, str):
                out[key] = _REDACTED_URL
            else:
                out[key] = redact_metadata(item)
        return out
    if isinstance(value, list):
        items = cast("list[Any]", value)
        return [redact_metadata(item) for item in items]
    if isinstance(value, str) and any(marker in value.lower() for marker in SENSITIVE_QUERY_KEYS):
        return _REDACTED_URL
    return value


def redact_error_detail(detail: str) -> str:
    """Scrub a free-text error detail before it is persisted to the DB (#341).

    Applied to ``GFlowError.to_problem_details()['detail']`` on the FAILED
    operation write path. Scrubs bearer/SAPISIDHASH/cookie-pair secrets, URL credentials,
    flow.google.com's ``at`` XSRF value and ``0cAF…`` reCAPTCHA tokens, and account
    addresses; drops URLs carrying signed-query material and ``lh*…/asb/`` capability
    links; and truncates post-redaction as defense-in-depth against a scrub bypass.
    """
    return scrub_secrets(detail)[:ERROR_DETAIL_MAX_CHARS]


def scrub_secrets(text: str) -> str:
    """:func:`redact_error_detail` without the length cap, for a caller that runs its own
    rules after this one and trims the result itself (``batchexecute.discovery_head``).
    Trimming first can cut a token into fragments shorter than any pattern's floor."""
    for pattern, replacement in _SECRET_TEXT_PATTERNS:
        text = pattern.sub(replacement, text)
    return _WHITESPACE_TOKEN.sub(_drop_signed_query_token, text)
