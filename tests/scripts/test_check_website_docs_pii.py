"""Unit tests for the website/docs PII-leak guard.

The guard forbids SPECIFIC private tokens while letting the public references
that legitimately contain ``ffroliva`` through untouched — the exact distinction
PR #362's blind find/replace got wrong.
"""

from __future__ import annotations

import pytest

from scripts.ci import check_website_docs_pii as guard

# Public references that contain 'ffroliva' but MUST pass — the #362 trap.
ALLOWED = [
    "See https://github.com/ffroliva/gflow-cli for the source.",
    "Report a vulnerability: https://github.com/ffroliva/gflow-cli/security/advisories/new",
    "Subscribe to GitHub Releases for `ffroliva/gflow-cli`.",
    r"type $env:LOCALAPPDATA\ffroliva\gflow-cli\profile_<name>\Default",
    "Use a placeholder like you@gmail.com or your.name@gmail.com.",
    "Example: user+flow@gmail.com works too.",
    "Log in as your-name with profile_your-name.",  # already-anonymized output
]

# Private tokens that MUST be flagged.
FORBIDDEN_SAMPLES = [
    ("verified end-to-end on denon82 (face...", "denon82"),
    # backslash-bounded path vector (the real leak is the home dir) without a
    # literal Windows user-profile path, which the repo-hygiene gate forbids.
    (r"cache under \ffrol\tmp", "ffrol"),
    ("the profile dir profile_ffroliva is created", "profile_ffroliva"),
    ("contact flavio for access", "flavio"),
    ("Contact FLAVIO.OLIVA directly", "FLAVIO"),
    ("email ffroliva@gmail.com for support", "ffroliva@"),
]


@pytest.mark.parametrize("line", ALLOWED)
def test_public_references_pass(line: str) -> None:
    """No false positives on legitimate public 'ffroliva' references or on
    already-anonymized placeholder output."""
    assert guard.find_pii(line) == []


@pytest.mark.parametrize(("line", "token"), FORBIDDEN_SAMPLES)
def test_private_tokens_are_flagged(line: str, token: str) -> None:
    hits = guard.find_pii(line)
    assert hits, f"expected a leak in {line!r}"
    assert any(token in matched for _, matched, _ in hits)


def test_ffrol_word_boundary_does_not_match_ffroliva() -> None:
    """The username guard 'ffrol' must not fire on the public owner 'ffroliva'."""
    assert guard.find_pii("owner is ffroliva/gflow-cli") == []


def test_lineno_is_reported() -> None:
    text = "clean line\nleak on denon82 here\nclean again"
    hits = guard.find_pii(text)
    assert [(lineno, tok) for lineno, tok, _ in hits] == [(2, "denon82")]


def test_scanned_suffixes_cover_published_types() -> None:
    # mkdocs publishes .md pages plus at least one .html mockup — both must scan.
    assert ".md" in guard.SCANNED_SUFFIXES
    assert ".html" in guard.SCANNED_SUFFIXES
