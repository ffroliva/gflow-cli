"""Create a redacted, transport-comparison summary from a HAR file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-goog-authuser",
    "x-origin",
}

SENSITIVE_KEY_PARTS = (
    "auth",
    "batchid",
    "bearer",
    "cookie",
    "credential",
    "idtoken",
    "key",
    "nonce",
    "recaptcha",
    "sapisid",
    "secret",
    "session",
    "sid",
    "token",
)

PROMPT_KEYS = {"prompt", "textPrompt", "negativePrompt"}


def _safe_url(url: str) -> tuple[str, str, list[str]]:
    parsed = urlsplit(url)
    query_keys = sorted({key for key, _value in parse_qsl(parsed.query, keep_blank_values=True)})
    safe = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return safe, parsed.netloc, query_keys


def _redact_header(name: str, value: str) -> str:
    lowered = name.lower()
    if lowered in SENSITIVE_HEADERS or any(part in lowered for part in SENSITIVE_KEY_PARTS):
        return "<redacted:present>" if value else "<redacted:empty>"
    return value


def _headers_to_dict(headers: list[dict[str, Any]]) -> dict[str, str]:
    safe: dict[str, str] = {}
    for header in headers:
        name = str(header.get("name", "")).strip()
        if not name:
            continue
        value = str(header.get("value", ""))
        safe[name.lower()] = _redact_header(name, value)
    return safe


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _redact_json(value: Any, key: str | None = None) -> Any:
    if key in PROMPT_KEYS:
        return "<redacted:prompt>"

    if isinstance(value, dict):
        return {str(k): _redact_json(v, str(k)) for k, v in value.items()}

    if isinstance(value, list):
        return [_redact_json(item) for item in value]

    if key is not None and _is_sensitive_key(key):
        return f"<redacted:{key}>"

    return value


def _summarize_body(post_data: dict[str, Any] | None) -> dict[str, Any]:
    if not post_data:
        return {"present": False}

    text = post_data.get("text")
    mime_type = post_data.get("mimeType")
    summary: dict[str, Any] = {
        "present": bool(text),
        "mimeType": mime_type,
        "length": len(text) if isinstance(text, str) else 0,
    }

    if isinstance(text, str) and text.strip():
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            summary["textPreview"] = "<redacted:non-json>"
        else:
            summary["json"] = _redact_json(parsed)

    return summary


def summarize_har(har_path: Path, *, host_filter: str | None = None) -> dict[str, Any]:
    har = json.loads(har_path.read_text(encoding="utf-8"))
    entries = har.get("log", {}).get("entries", [])
    safe_entries: list[dict[str, Any]] = []

    for entry in entries:
        request = entry.get("request", {})
        response = entry.get("response", {})
        url = str(request.get("url", ""))
        safe_url, host, query_keys = _safe_url(url)

        if host_filter and host_filter not in host:
            continue

        safe_entries.append(
            {
                "request": {
                    "method": request.get("method"),
                    "url": safe_url,
                    "host": host,
                    "query_keys": query_keys,
                    "headers": _headers_to_dict(request.get("headers", [])),
                    "body": _summarize_body(request.get("postData")),
                },
                "response": {
                    "status": response.get("status"),
                    "headers": _headers_to_dict(response.get("headers", [])),
                    "mimeType": response.get("content", {}).get("mimeType"),
                },
            }
        )

    return {
        "source": str(har_path),
        "host_filter": host_filter,
        "entry_count": len(safe_entries),
        "entries": safe_entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("har", type=Path, help="HAR file to summarize")
    parser.add_argument("--host", default="aisandbox-pa.googleapis.com", help="Host substring filter")
    parser.add_argument("--out", type=Path, help="Output JSON path")
    args = parser.parse_args()

    summary = summarize_har(args.har, host_filter=args.host)
    output = json.dumps(summary, indent=2, sort_keys=True)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
