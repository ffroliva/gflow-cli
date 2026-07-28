"""Redaction utilities for sensitive text in error messages, payloads, and logs."""

from __future__ import annotations

from gflow_cli.data.redaction import redact_error_detail

__all__ = ["redact_sensitive_text"]


def redact_sensitive_text(text: str) -> str:
    """Scrub sensitive information (tokens, credentials, signed URLs) from free-text strings."""
    if not text:
        return ""
    return redact_error_detail(text)
