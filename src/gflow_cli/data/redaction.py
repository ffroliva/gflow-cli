from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal, cast

PromptMode = Literal["store", "redacted"]
SENSITIVE_URL_KEYS = {"fifeurl", "signedurl", "downloadurl", "mediaurl"}
SENSITIVE_QUERY_KEYS = ("signature=", "x-goog-signature=", "x-goog-credential=", "expires=")


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
            if lowered in {"token", "recaptchatoken"}:
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
